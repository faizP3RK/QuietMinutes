"""Dual-track capture core.

Two independent sources, each written to its own WAV, kept aligned to a single
WALL CLOCK so they can be merged by timestamp later:

  * "Others"  -> WASAPI loopback of the default render endpoint (everyone else).
  * "Me"      -> a microphone (laptop builtin in quality mode; AirPods in convenience).

Phase 0 taught us two things this design depends on:
  1. An idle WASAPI loopback delivers ZERO callback buffers (a blocking read hangs).
     So we capture in callback mode and a wall-clock writer pads silence for any gap,
     guaranteeing each WAV's length == elapsed recording time (tracks never drift).
  2. Capture float32 at the device-native rate/channels; soundfile writes PCM_16.

Robustness goals (Phase 1 acceptance): never crash and never lose the recording when
AirPods connect/disconnect mid-meeting. A watchdog re-attaches the input stream to the
new default device while the SAME WAV keeps being written.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

from . import devices as dv
from . import meters
from .profile import classify

CHUNK = 1024
WRITER_TICK = 0.05  # seconds between writer passes
MIC_DEAD_S = 4.0    # no mic buffers this long => stream is dead (mics never go quiet)
MIC_RETRY_S = 5.0   # retry re-attaching a dead mic at most this often


class Track:
    """One source -> one WAV, wall-clock aligned, pause-aware, re-attachable."""

    def __init__(self, p: pyaudio.PyAudio, name: str, dev: dv.Dev, wav_path: Path):
        self._p = p
        self.name = name
        self.dev = dev
        self.sr = dev.rate
        self.ch = max(1, dev.channels)
        self.wav_path = wav_path

        self._sf = sf.SoundFile(
            str(wav_path), mode="w", samplerate=self.sr,
            channels=self.ch, subtype="PCM_16",
        )
        self._buf: deque[bytes] = deque()
        self._buf_lock = threading.Lock()
        self._stream = None
        self._stream_dead = False
        # The live stream's format can differ from the WAV's after a re-attach
        # (e.g. mic swapped from a 44.1k to a 48k device); audio is converted on write.
        self.stream_rate = self.sr
        self.stream_ch = self.ch
        self._last_buf = 0.0  # monotonic time the last callback buffer arrived
        self.reattaches = 0
        # optional tee for rolling transcription: tap(data, start_frame, sr)
        self.tap = None

        self._t0 = 0.0
        self._paused = False
        self._paused_total = 0.0
        self._pause_started = 0.0
        self._muted = False
        self._frames_written = 0

        self._peak = 0.0
        self._rms = 0.0
        self._running = False
        self._writer = threading.Thread(target=self._writer_loop, daemon=True)

    # ---- stream lifecycle -------------------------------------------------
    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        if status:
            # overflow/underflow flags; not fatal, just note it
            pass
        with self._buf_lock:
            self._buf.append(in_data)
        self._last_buf = time.monotonic()
        return (None, pyaudio.paContinue)

    @property
    def seconds_since_audio(self) -> float:
        return time.monotonic() - self._last_buf if self._last_buf else 1e9

    def _open_stream(self) -> bool:
        try:
            self._stream = self._p.open(
                format=pyaudio.paFloat32,
                channels=self.stream_ch,
                rate=self.stream_rate,
                frames_per_buffer=CHUNK,
                input=True,
                input_device_index=self.dev.index,
                stream_callback=self._callback,
            )
            self._stream.start_stream()
            self._stream_dead = False
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[{self.name}] could not open stream: {exc}")
            self._stream = None
            self._stream_dead = True
            return False

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._last_buf = self._t0
        self._running = True
        self._open_stream()
        self._writer.start()

    def reattach(self, new_dev: dv.Dev) -> bool:
        """Swap the input stream to a (possibly different) device WITHOUT touching the
        WAV. The new stream opens in the device's native format; incoming audio is
        converted to the WAV's rate/channels on write, so ANY device can take over
        mid-meeting (previously a format mismatch meant silence until the end)."""
        self._close_stream()
        self.dev = new_dev
        self.stream_rate = new_dev.rate
        self.stream_ch = max(1, new_dev.channels)
        with self._buf_lock:
            self._buf.clear()  # drop anything captured mid-switch
        ok = self._open_stream()
        self._last_buf = time.monotonic()  # grace period before judging the new stream
        self.reattaches += 1
        conv = "" if (self.stream_rate, self.stream_ch) == (self.sr, self.ch) else \
            f" (converting {self.stream_rate}/{self.stream_ch}ch -> {self.sr}/{self.ch}ch)"
        print(f"[{self.name}] re-attached to: {new_dev.name}{conv} ok={ok}")
        return ok

    def _to_wav_format(self, data: np.ndarray) -> np.ndarray:
        """Interleaved float32 from the stream -> (frames, ch) at the WAV's rate."""
        sch = self.stream_ch
        if sch > 1:
            usable = (len(data) // sch) * sch
            data = data[:usable].reshape(-1, sch)
        else:
            data = data.reshape(-1, 1)
        if sch != self.ch:
            mono = data.mean(axis=1, keepdims=True)
            data = np.repeat(mono, self.ch, axis=1) if self.ch > 1 else mono
        if self.stream_rate != self.sr and data.shape[0] > 1:
            n_out = int(round(data.shape[0] * self.sr / self.stream_rate))
            x_old = np.arange(data.shape[0], dtype=np.float64)
            x_new = np.linspace(0, data.shape[0] - 1, n_out)
            data = np.stack([np.interp(x_new, x_old, data[:, c]) for c in range(data.shape[1])],
                            axis=1).astype(np.float32)
        return data if self.ch > 1 else data[:, 0]

    # ---- pause / resume ---------------------------------------------------
    def pause(self) -> None:
        if not self._paused:
            self._paused = True
            self._pause_started = time.monotonic()

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self._paused_total += time.monotonic() - self._pause_started
            with self._buf_lock:
                self._buf.clear()  # discard audio captured while paused

    def set_muted(self, value: bool) -> None:
        """Mute this source: incoming audio is discarded and the wall-clock writer
        fills silence for the muted span, so the WAV stays time-aligned but contains
        no audio (i.e. it won't be transcribed)."""
        self._muted = bool(value)

    @property
    def muted(self) -> bool:
        return self._muted

    # ---- writer -----------------------------------------------------------
    def _active_elapsed(self) -> float:
        return (time.monotonic() - self._t0) - self._paused_total

    def _writer_loop(self) -> None:
        while self._running:
            time.sleep(WRITER_TICK)
            try:
                self._writer_pass()
            except Exception as exc:  # noqa: BLE001 - never let the writer die
                print(f"[{self.name}] writer error: {exc}")

    def _writer_pass(self) -> None:
        if self._paused:
            with self._buf_lock:
                self._buf.clear()
            return

        with self._buf_lock:
            chunks = list(self._buf)
            self._buf.clear()

        if self._muted:
            # discard captured audio; the wall-clock pad below fills silence
            self._peak = 0.0
            self._rms = 0.0
        elif chunks:
            data = self._to_wav_format(np.frombuffer(b"".join(chunks), dtype=np.float32))
            pos = self._frames_written  # wall-clock sample position of this audio
            self._sf.write(data)
            self._frames_written += data.shape[0]
            self._peak, self._rms = meters.peak_rms(data)
            if self.tap is not None:
                try:
                    self.tap(data, pos, self.sr)
                except Exception as exc:  # noqa: BLE001 - tee must never break capture
                    print(f"[{self.name}] tap error: {exc}")
        else:
            # decay the meter while no audio is arriving
            self._peak *= 0.55
            self._rms *= 0.55

        # wall-clock catch-up: pad silence so WAV length == elapsed time
        target = int(self._active_elapsed() * self.sr)
        gap = target - self._frames_written
        if gap > 0:
            shape = (gap, self.ch) if self.ch > 1 else (gap,)
            self._sf.write(np.zeros(shape, dtype=np.float32))
            self._frames_written += gap

    # ---- status -----------------------------------------------------------
    @property
    def level(self) -> tuple[float, float]:
        return (self._peak, self._rms)

    @property
    def degraded(self) -> bool:
        return self._stream_dead

    def stop(self) -> float:
        """Stop capture, finalize the WAV, return duration in seconds."""
        self._running = False
        if self._writer.is_alive():
            self._writer.join(timeout=2)
        self._close_stream()
        try:
            self._sf.flush()
            self._sf.close()
        except Exception:  # noqa: BLE001
            pass
        return self._frames_written / float(self.sr)


class DualCapture:
    """Owns the two Tracks, the meeting folder, and the device watchdog."""

    def __init__(self, p: pyaudio.PyAudio, config, engine=None):
        self._p = p
        self.cfg = config
        self.engine = engine  # WhisperEngine or None (transcription optional in capture)
        self.state = "idle"  # idle | recording | paused
        self.folder: Path | None = None
        self.others: Track | None = None
        self.me: Track | None = None
        self.others_profile = None
        self.me_muted = False
        self.meeting_name = ""
        self.output_target = ""  # pinned output device name ("" = follow default)
        self.transcriber = None
        self._t0 = 0.0
        self._paused_total = 0.0
        self._pause_started = 0.0
        self._watchdog = None
        self._watch_running = False
        self._me_last_try = 0.0

    # ---- control ----------------------------------------------------------
    def start(self, name: str = "") -> Path:
        if self.state != "idle":
            return self.folder
        from .. import meeting

        ts = time.strftime("%Y-%m-%d_%H-%M")
        self.meeting_name = (name or "").strip()
        self.folder = Path(self.cfg.output_folder) / meeting.folder_name(ts, self.meeting_name)
        self.folder.mkdir(parents=True, exist_ok=True)
        meeting.write_meta(self.folder, name=self.meeting_name, ts=ts,
                           created=time.strftime("%Y-%m-%d %H:%M"))

        # "Others" loopback: a pinned device by name, else the Windows default.
        self.output_target = self.cfg.output_device_name
        loop = dv.loopback_by_name(self._p, self.output_target) if self.output_target else None
        if loop is None:
            if self.output_target:
                print(f"[capture] pinned output '{self.output_target}' not found; using default")
            loop = dv.default_loopback(self._p)
        mic = dv.mic_for_mode(self._p, self.cfg.capture_mode, self.cfg.me_device_index)
        if loop is None:
            raise RuntimeError("No WASAPI loopback device found.")
        if mic is None:
            raise RuntimeError("No microphone found for 'Me' track.")

        self.others_profile = classify(loop.rate, loop.channels)
        meeting.write_meta(self.folder, others_device=loop.name, me_device=mic.name,
                           capture_mode=self.cfg.capture_mode)
        print(f"[capture] Others={loop.name} ({loop.rate}/{loop.channels}ch) | "
              f"Me={mic.name} ({mic.rate}/{mic.channels}ch)")
        self.others = Track(self._p, "Others", loop, self.folder / "others.wav")
        self.me = Track(self._p, "Me", mic, self.folder / "me.wav")
        self.me.set_muted(self.me_muted)  # carry mute state into the new recording

        # Wire rolling transcription if a warm engine is available.
        self.transcriber = None
        if self.engine is not None and self.engine.is_ready():
            from ..transcribe.rolling import RollingTranscriber

            self.transcriber = RollingTranscriber(self.engine, self.cfg.me_name)
            self.transcriber.register("others", self.others.sr, self.others.ch, "Speaker")
            self.transcriber.register("me", self.me.sr, self.me.ch, self.cfg.me_name)
            self.others.tap = lambda d, pos, sr: self.transcriber.feed("others", d, pos)
            self.me.tap = lambda d, pos, sr: self.transcriber.feed("me", d, pos)

        self.others.start()
        self.me.start()

        self._t0 = time.monotonic()
        self._paused_total = 0.0
        self.state = "recording"
        self._start_watchdog()
        return self.folder

    def set_me_muted(self, value: bool) -> None:
        self.me_muted = bool(value)
        if self.me is not None:
            self.me.set_muted(self.me_muted)

    def toggle_me_muted(self) -> bool:
        self.set_me_muted(not self.me_muted)
        return self.me_muted

    def toggle_pause(self) -> None:
        if self.state == "recording":
            self.others.pause()
            self.me.pause()
            self._pause_started = time.monotonic()
            self.state = "paused"
        elif self.state == "paused":
            self.others.resume()
            self.me.resume()
            self._paused_total += time.monotonic() - self._pause_started
            self.state = "recording"

    def stop(self) -> dict:
        if self.state == "idle":
            return {}
        self._watch_running = False
        d_others = self.others.stop() if self.others else 0.0
        d_me = self.me.stop() if self.me else 0.0
        result = {
            "folder": str(self.folder),
            "others_wav": str(self.folder / "others.wav"),
            "me_wav": str(self.folder / "me.wav"),
            "others_seconds": round(d_others, 2),
            "me_seconds": round(d_me, 2),
        }
        self.state = "idle"
        self.others = self.me = None
        result["has_transcriber"] = self.transcriber is not None
        return result

    def finalize_transcription(self) -> list[dict] | None:
        """Flush + drain the rolling transcriber (fast: only the last chunk remains).
        Returns merged segments sorted by start time, or None if no transcriber."""
        if self.transcriber is None:
            return None
        segs = self.transcriber.finalize()
        self.transcriber = None
        return segs

    # ---- status (read by the UI poll loop) --------------------------------
    def elapsed(self) -> float:
        if self.state == "idle":
            return 0.0
        base = (time.monotonic() - self._t0) - self._paused_total
        if self.state == "paused":
            base -= time.monotonic() - self._pause_started
        return max(0.0, base)

    def status(self) -> dict:
        o_peak = self.others.level[0] if self.others else 0.0
        m_peak = self.me.level[0] if self.me else 0.0
        return {
            "state": self.state,
            "elapsed": self.elapsed(),
            "others_meter": meters.meter_fraction(o_peak),
            "me_meter": meters.meter_fraction(m_peak),
            "others_peak": o_peak,
            "me_peak": m_peak,
            "others_silent": o_peak < meters.SILENCE_PEAK,
            "me_silent": m_peak < meters.SILENCE_PEAK,
            "me_muted": self.me_muted,
            "others_device": self.others.dev.name if self.others else "-",
            "me_device": self.me.dev.name if self.me else "-",
            "profile": self.others_profile,
            "degraded": bool(self.others and self.others.degraded),
            "folder": str(self.folder) if self.folder else "-",
        }

    # ---- device-change watchdog ------------------------------------------
    def _start_watchdog(self) -> None:
        self._watch_running = True
        self._watchdog = threading.Thread(target=self._watch_loop, daemon=True)
        self._watchdog.start()

    def _watch_loop(self) -> None:
        """Follow the default render endpoint; re-attach 'Others' on change.

        Handles AirPods connect/disconnect: when the default output device changes
        (or our stream died), bind 'Others' to the new default loopback and keep
        appending to the same WAV. Never crashes the recording.
        """
        while self._watch_running:
            time.sleep(1.0)
            if self.state == "idle" or self.others is None:
                continue
            try:
                # --- Me (mic) track: a live mic ALWAYS delivers buffers (even silence),
                # so a multi-second gap means the stream died (device switched/removed,
                # grabbed by another app). Previously nothing watched this, and the rest
                # of the meeting was silently recorded as zeros.
                me = self.me
                if (me is not None and me.seconds_since_audio > MIC_DEAD_S
                        and time.monotonic() - self._me_last_try > MIC_RETRY_S):
                    self._me_last_try = time.monotonic()
                    mic = dv.mic_for_mode(self._p, self.cfg.capture_mode, self.cfg.me_device_index)
                    print(f"[watchdog] Me stream dead ({me.seconds_since_audio:.0f}s no audio) "
                          f"-> re-attaching to {mic.name if mic else 'NO MIC FOUND'}")
                    if mic is not None:
                        me.reattach(mic)

                # Target the pinned device if set, else follow the Windows default.
                desired = (dv.loopback_by_name(self._p, self.output_target)
                           if self.output_target else dv.default_loopback(self._p))
                if desired is None:
                    continue  # pinned device not present right now; keep padding silence
                if desired.name != self.others.dev.name or self.others.degraded:
                    print(f"[watchdog] Others -> {desired.name}; re-attaching")
                    self.others.reattach(desired)
                    self.others_profile = classify(desired.rate, desired.channels)
            except Exception as exc:  # noqa: BLE001
                print(f"[watchdog] {exc}")
