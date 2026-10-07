"""Entrypoint + controller wiring tray, dashboard, and the capture core together.

Threading model:
  * Tk dashboard mainloop runs on the MAIN thread.
  * The tray runs its own message loop in a daemon thread.
  * Capture runs its own worker/writer/watchdog threads.
Tray callbacks that touch the window are marshalled onto the Tk thread via `after`.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import pyaudiowpatch as pyaudio

import threading

from . import meeting, models
from .logging_setup import log, setup_logging
from .audio import devices as dv
from .audio.capture import DualCapture
from .audio.profile import classify
from .config import Config
from .diarize import models as diar_models
from .diarize import pipeline, voiceprints
from .diarize.embeddings import Embedder
from .diarize.engine import Diarizer
from .transcribe.whisper_engine import WhisperEngine
from .transcribe.writer import delete_raw_audio, write_outputs
from .ui.dashboard import Dashboard
from .ui.tray import Tray


class Controller:
    def __init__(self):
        self.cfg = Config.load()
        self.p = pyaudio.PyAudio()
        self.engine = WhisperEngine(self.cfg)
        self.engine.warm()  # load the model once, in the background
        self.diarizer = Diarizer(self.cfg.diar_threshold)
        self.embedder = Embedder()
        from .diarize.pyannote_engine import PyannoteDiarizer
        self.pyannote = PyannoteDiarizer()  # loads lazily per job (heavy; ~1-2 GB while running)
        threading.Thread(target=self._warm_diar, daemon=True).start()
        self.cap = DualCapture(self.p, self.cfg, self.engine)
        self.tray: Tray | None = None
        self.dashboard: Dashboard | None = None
        # background jobs: folder -> {"name", "stage", "pct"}  (transcription/diarization)
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._notes_running: set[str] = set()
        self._audio_lock = threading.RLock()
        self._audio_refreshed = time.monotonic()
        self.last_transcript: str | None = None
        self.hotkeys = None  # HotkeyManager, set in main()
        self._pf_cache: dict | None = None
        self._pf_time = 0.0
        self._shut = False

    def attach(self, tray: Tray, dashboard: Dashboard) -> None:
        self.tray = tray
        self.dashboard = dashboard

    # ---- background job tracking -------------------------------------------
    @property
    def transcribing(self) -> bool:
        return bool(self._jobs)

    def job_info(self) -> dict | None:
        with self._jobs_lock:
            if not self._jobs:
                return None
            folder, j = next(iter(self._jobs.items()))
            return {"folder": folder, **j, "count": len(self._jobs)}

    def _job(self, folder: str, **kw) -> None:
        with self._jobs_lock:
            if folder in self._jobs:
                self._jobs[folder].update(kw)

    # ---- capture controls -------------------------------------------------
    @staticmethod
    def parse_attendees(text) -> list[str]:
        """'Alice, Bob; Carol\\nDan' -> ['Alice', 'Bob', 'Carol', 'Dan']."""
        import re
        if isinstance(text, list):
            items = text
        else:
            items = re.split(r"[,;\n\r]+", text or "")
        seen, out = set(), []
        for it in items:
            it = re.sub(r"\s*<[^>]*>", "", str(it)).strip(" \t\"'")  # drop <email> parts
            if it and it.lower() not in seen:
                seen.add(it.lower())
                out.append(it)
        return out

    def set_meeting_details(self, folder: str, people=None, attendees=None) -> None:
        kw = {}
        if people is not None:
            try:
                kw["people"] = max(0, int(people))
            except (TypeError, ValueError):
                kw["people"] = 0
        if attendees is not None:
            kw["attendees"] = self.parse_attendees(attendees)
        if kw:
            meeting.write_meta(folder, **kw)

    def start_recording(self, name: str = "", people=None, attendees=None) -> None:
        if self.cap.state != "idle":
            return
        if not self.engine.is_ready():
            # Not fatal any more: the meeting will be batch-transcribed after Stop.
            log.warning("recording started while whisper not ready (error=%s) — "
                        "will batch-transcribe after stop", self.engine.error)
            self.engine.warm()
            self._notify("Model still loading",
                         "Recording anyway — this meeting will be transcribed after you stop.")
        try:
            self.refresh_audio_devices(force=True)  # see devices connected since launch
            with self._audio_lock:
                folder = self.cap.start(name)
            self.set_meeting_details(str(folder), people, attendees)
            log.info("recording started -> %s (live transcription: %s)",
                     folder, self.cap.transcriber is not None)
        except Exception as exc:  # noqa: BLE001
            self._error(f"Could not start recording:\n{exc}")

    def stop_recording(self) -> None:
        if self.cap.state == "idle":
            return
        res = self.cap.stop()
        if not res:
            return
        # Take ownership of THIS meeting's live transcriber now, so a new recording
        # started immediately afterwards can't swap it out from under _finalize.
        transcriber = self.cap.transcriber
        self.cap.transcriber = None
        duration = max(res.get("others_seconds", 0), res.get("me_seconds", 0))
        log.info("recording stopped: others=%ss me=%ss live=%s -> %s",
                 res["others_seconds"], res["me_seconds"], transcriber is not None,
                 res["folder"])
        meeting.write_meta(res["folder"], duration=duration)
        self._start_job(res["folder"], duration, transcriber)

    def _start_job(self, folder: str, duration: float, transcriber=None,
                   mode: str = "full") -> bool:
        with self._jobs_lock:
            if folder in self._jobs:
                return False
            self._jobs[folder] = {"name": meeting.display_name(folder),
                                  "stage": "finishing" if transcriber else "queued", "pct": 0.0}
        if mode == "speakers":
            target, args = self._reidentify, (folder,)
        else:
            target, args = self._finalize, (folder, duration, transcriber)
        threading.Thread(target=target, args=args, name=mode, daemon=True).start()
        return True

    def reidentify_speakers(self, folder: str, people: int | None = None) -> bool:
        """Re-run speaker identification on a saved meeting (e.g. after setting the
        headcount). Needs the kept raw audio."""
        f = Path(folder)
        if not (f / "others.wav").exists() or not (f / "segments.json").exists():
            return False
        if people is not None:
            meeting.write_meta(f, people=int(people) if int(people) > 0 else 0)
        log.info("re-identify speakers requested: %s (people=%s)", folder, people)
        return self._start_job(str(f), 0.0, None, mode="speakers")

    def transcribe_existing(self, folder: str) -> bool:
        """Recovery: transcribe a saved meeting that has audio but no transcript."""
        f = Path(folder)
        if not (f / "others.wav").exists() and not (f / "me.wav").exists():
            return False
        dur = meeting.read_meta(f).get("duration") or 0.0
        log.info("transcribe_existing requested: %s", folder)
        return self._start_job(str(f), dur, None)

    def _warm_diar(self) -> None:
        try:
            if self.diarizer.is_present():
                self.diarizer.load()
                self.embedder.load()
                print("[diar] models warm")
        except Exception as exc:  # noqa: BLE001
            print(f"[diar] warm failed: {exc}")

    def _finalize(self, folder: str, duration: float, transcriber=None) -> None:
        """Background: get the transcript (live, or batch from the saved WAVs), diarize
        'Others', write outputs, then kick off notes. A meeting is never left without
        a transcript just because the live path wasn't running."""
        import json
        from pathlib import Path as _Path

        from .transcribe import batch
        t0 = time.monotonic()
        try:
            segs: list[dict] = []
            if transcriber is not None:
                self._job(folder, stage="finishing live transcript")
                segs = transcriber.finalize() or []
                log.info("live transcript: %d segments", len(segs))
            if not segs:
                # SAFETY NET: transcribe from the saved audio.
                self._job(folder, stage="loading model")
                if not self.engine.ensure_ready(180):
                    raise RuntimeError(f"Whisper model unavailable ({self.engine.error})")
                self._job(folder, stage="transcribing", pct=0.0)
                log.info("batch transcription starting: %s", folder)
                segs = batch.transcribe_meeting(
                    self.engine, _Path(folder), self.cfg.me_name,
                    progress=lambda f: self._job(folder, pct=round(f * 100, 1)))
                log.info("batch transcription done: %d segments in %.0fs",
                         len(segs), time.monotonic() - t0)
            # Warn hard if the mic contributed nothing — a silent Me track means the
            # user's own words are missing from the whole transcript.
            me_segs = sum(1 for s in segs if s["speaker"] == self.cfg.me_name)
            if segs and me_segs == 0:
                log.warning("Me track produced 0 segments (muted=%s) — mic silent or wrong device?",
                            self.cap.me_muted)
                self._notify("⚠ Your mic was silent",
                             "No speech captured from your microphone this meeting — "
                             "check the mic device / mute state before the next call.")

            # PHASE 1: transcript available immediately (speakers still un-separated).
            title = meeting.display_name(folder)
            out = self._write_transcript(folder, segs, duration=duration, speakers=0,
                                         speakers_pending=True)
            log.info("transcript ready (phase 1): %s segments -> %s", out["segments"], folder)
            self._notify("Transcript ready", f"{out['segments']} lines — identifying speakers… "
                         f"({title})")

            # PHASE 2: identify speakers (can take ~20 min per hour of audio), rewrite.
            n_speakers = self._identify_speakers(folder, segs)
            if n_speakers:
                out = self._write_transcript(folder, segs, speakers=n_speakers,
                                             speakers_pending=False)
                self._notify("Speakers identified", f"{n_speakers} speakers — {title}")
            else:
                meeting.write_meta(folder, speakers_pending=False)
            if self.cfg.delete_raw_audio:
                delete_raw_audio(_Path(folder))
            log.info("meeting finalized: %s lines, %s speakers in %.0fs -> %s",
                     out["segments"], n_speakers, time.monotonic() - t0, folder)

            # PHASE 3: notes (after speakers, so owners can be named).
            if self.cfg.notes_enabled and out["segments"] >= 5:  # skip trivially short clips
                threading.Thread(target=self._gen_notes, args=(folder,),
                                 name="notes", daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            log.exception("transcription failed: %s", exc)
            self._notify("Transcription failed", f"{exc} — audio is saved; use 'Transcribe now'.")
        finally:
            with self._jobs_lock:
                self._jobs.pop(folder, None)

    # ---- shared post-meeting steps -----------------------------------------
    def _write_transcript(self, folder: str, segs: list[dict], **meta) -> dict:
        """Persist segments.json (incl. word timing + source track) and the outputs."""
        import json
        for s in segs:  # remember which track a line came from (for re-identification)
            s.setdefault("src", "me" if s["speaker"] == self.cfg.me_name else "others")
        rows = []
        for s in segs:
            r = {k: s[k] for k in ("start", "end", "text", "speaker", "src")}
            if s.get("words"):
                r["words"] = [list(w) for w in s["words"]]
            rows.append(r)
        Path(folder, "segments.json").write_text(json.dumps(rows), encoding="utf-8")
        out = write_outputs(Path(folder), segs, title=meeting.display_name(folder))
        self.last_transcript = out["txt"]
        meeting.write_meta(folder, segments=out["segments"], **meta)
        return out

    def _others_count(self, folder: str) -> int | None:
        """'People on the call' (incl. you) -> number of OTHER voices, or None (auto)."""
        p = meeting.read_meta(folder).get("people")
        try:
            p = int(p)
        except (TypeError, ValueError):
            return None
        return max(1, p - 1) if p > 0 else None

    def _identify_speakers(self, folder: str, segs: list[dict]) -> int:
        """Diarize the Others track and relabel `segs` in place (word-level).
        Uses pyannote 3.1 when installed (≈2x more accurate), else the sherpa engine."""
        from .diarize import pyannote_engine
        # results from a previous run no longer match the new transcript
        for old in [Path(folder) / "speakers.json", Path(folder) / "embeddings.npz",
                    *Path(folder).glob("speaker_*.wav")]:
            old.unlink(missing_ok=True)
        others_wav = Path(folder) / "others.wav"
        others_segs = sum(1 for s in segs if s["speaker"] == "Speaker")
        if not (self.cfg.diarization_enabled and others_wav.exists() and others_segs):
            log.warning("diarization SKIPPED: enabled=%s others_wav=%s others_segments=%s",
                        self.cfg.diarization_enabled, others_wav.exists(), others_segs)
            return 0
        k = self._others_count(folder)
        use_pya = self.cfg.diar_engine == "pyannote" and pyannote_engine.available()
        self._job(folder, stage="identifying speakers", pct=0.0)
        t0 = time.monotonic()
        diar = None
        if use_pya:
            try:
                diar = self.pyannote.diarize(
                    str(others_wav), num_speakers=k,
                    progress=lambda f: self._job(folder, pct=round(f * 100, 1)))
                engine = "pyannote3.1"
            except Exception as exc:  # noqa: BLE001
                log.exception("pyannote failed — falling back to built-in engine: %s", exc)
                diar = None
        try:
            if diar is None:
                if not self.diarizer.is_present():
                    log.warning("diarization SKIPPED: no speaker models installed")
                    return 0
                self.diarizer.set_threshold(self.cfg.diar_threshold)
                diar = self.diarizer.diarize(str(others_wav))
                diar = pipeline.merge_clusters(
                    diar, str(others_wav), self.embedder, self.cfg.diar_merge_threshold,
                    self.cfg.diar_min_speaker_s, log=log)
                engine = "sherpa+merge"
            n = len({d["speaker"] for d in diar})
            log.info("diarization (%s): %d regions -> %d speaker(s), count hint=%s, %.0fs "
                     "(%.1f min audio)", engine, len(diar), n, k, time.monotonic() - t0,
                     others_wav.stat().st_size / 44100 / 4 / 60)
            if not diar:
                return 0
            self._job(folder, stage="labelling speakers", pct=100.0)
            pipeline.label_segments(segs, diar)
            speakers, embs = pipeline.build_speakers(
                segs, diar, str(others_wav), self.embedder,
                self.cfg.auto_name_threshold, folder=folder)
            pipeline.save_speakers(folder, speakers, embs)
            return len(speakers)
        except Exception as exc:  # noqa: BLE001
            log.exception("diarization FAILED (keeping single Speaker): %s", exc)
            return 0

    def _reidentify(self, folder: str) -> None:
        """Job: reload segments.json, reset 'Others' lines, identify speakers again."""
        import json
        try:
            segs = json.loads(Path(folder, "segments.json").read_text(encoding="utf-8-sig"))
            for s in segs:
                src = s.get("src") or ("me" if s["speaker"] == self.cfg.me_name else "others")
                s["src"] = src
                if src == "others":
                    s["speaker"] = "Speaker"
                    s.pop("cluster", None)
            n = self._identify_speakers(folder, segs)
            if n:
                self._write_transcript(folder, segs, speakers=n, speakers_pending=False)
                self._notify("Speakers re-identified", f"{n} speakers — {meeting.display_name(folder)}")
        except Exception as exc:  # noqa: BLE001
            log.exception("re-identify failed: %s", exc)
            self._notify("Re-identify failed", str(exc))
        finally:
            with self._jobs_lock:
                self._jobs.pop(folder, None)

    def toggle_record(self) -> None:
        """Single action for the global hotkey: start if idle, else stop."""
        if self.cap.state == "idle":
            self.start_recording()
        else:
            self.stop_recording()

    def apply_speaker_names(self, folder, mapping: dict) -> None:
        """Apply Speaker N -> name edits (enroll + re-render). See pipeline.apply_names."""
        relabel = pipeline.apply_names(folder, mapping)
        if relabel:
            print(f"[diar] applied names: {relabel}")

    # --- AI notes (Phase 4) ---
    def generate_notes(self, folder, on_done=None) -> None:
        threading.Thread(target=self._gen_notes, args=(folder, on_done), daemon=True).start()

    def _gen_notes(self, folder, on_done=None) -> None:
        from pathlib import Path as _Path
        from .notes import backend
        folder = str(folder)
        if folder in self._notes_running:
            if on_done:
                on_done(False)
            return
        self._notes_running.add(folder)
        t0 = time.monotonic()
        try:
            txt = _Path(folder) / "transcript.txt"
            transcript = txt.read_text(encoding="utf-8") if txt.exists() else ""
            if not transcript.strip():
                self._notify("Notes", "No transcript to summarize.")
                if on_done:
                    on_done(False)
                return
            att = meeting.read_meta(folder).get("attendees") or []
            if att:  # lets the model write "Adam: ..." instead of "Speaker 3: ..."
                transcript = "Meeting attendees: " + ", ".join(att) + "\n\n" + transcript
            if self.cfg.notes_backend == "local" and not backend.ensure_ollama(self.cfg):
                msg = "Local AI (Ollama) isn't running and couldn't be started."
                log.error("notes skipped: %s", msg)
                self._notify("Notes unavailable", msg)
                if on_done:
                    on_done(False)
                return
            log.info("notes: generating (%s, %d chars transcript)",
                     self.cfg.notes_backend, len(transcript))
            md, status = backend.write_notes(folder, transcript, self.cfg)
            if md:
                log.info("notes written (%s) in %.0fs -> %s\\notes.md",
                         self.cfg.notes_backend, time.monotonic() - t0, folder)
                meeting.write_meta(folder, notes=True)
                self._notify("Notes ready", meeting.display_name(folder))
            else:
                log.error("notes failed (%s): %s", self.cfg.notes_backend, status)
                self._notify("Notes unavailable", status)
            if on_done:
                on_done(bool(md))
        except Exception as exc:  # noqa: BLE001
            log.exception("notes error: %s", exc)
            if on_done:
                on_done(False)
        finally:
            self._notes_running.discard(folder)

    def test_notes_connection(self, on_done) -> None:
        """Run a quick reachability/key check off the UI thread; on_done(ok, message)."""
        from .notes import backend

        def run():
            ok, msg = backend.test_connection(self.cfg)
            on_done(ok, msg)

        threading.Thread(target=run, daemon=True).start()

    # --- voiceprint DB passthroughs (Manage voices) ---
    def list_voices(self) -> list:
        return voiceprints.list_people()

    def delete_voice(self, name: str) -> None:
        voiceprints.delete_person(name)

    def rename_voice(self, old: str, new: str) -> None:
        voiceprints.rename_person(old, new)

    def rename_meeting(self, folder, new_name: str) -> str:
        """Rename a finished meeting (delegates to meeting.rename)."""
        from pathlib import Path as _Path
        old = _Path(folder)
        new = meeting.rename(old, new_name)
        if self.last_transcript and str(old) in self.last_transcript:
            self.last_transcript = str(new / "transcript.txt")
        return str(new)

    def toggle_pause(self) -> None:
        self.cap.toggle_pause()

    def toggle_mute(self) -> None:
        muted = self.cap.toggle_me_muted()
        print(f"[rec] me {'MUTED' if muted else 'unmuted'}")

    def set_mute(self, value: bool) -> None:
        self.cap.set_me_muted(bool(value))
        print(f"[rec] me {'MUTED' if value else 'unmuted'}")

    # ---- pre-flight (cached ~1 Hz; the dashboard polls at 10 Hz) ----------
    def refresh_audio_devices(self, force: bool = False) -> bool:
        """Re-initialize PortAudio so newly connected devices (e.g. AirPods paired after
        the app started) appear. PortAudio snapshots the device list at init, so without
        this they were invisible until a restart. Only done while NOT recording."""
        with self._audio_lock:
            if self.cap.state != "idle":
                return False
            if not force and time.monotonic() - self._audio_refreshed < 15.0:
                return False
            try:
                old = self.p
                self.p = pyaudio.PyAudio()
                self.cap._p = self.p
                try:
                    old.terminate()
                except Exception:  # noqa: BLE001
                    pass
                self._audio_refreshed = time.monotonic()
                self._pf_cache = None
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("audio device refresh failed: %s", exc)
                return False

    def preflight(self) -> dict:
        self.refresh_audio_devices()  # throttled to every 15s, idle only
        now = time.monotonic()
        if self._pf_cache and now - self._pf_time < 3.0:
            return self._pf_cache
        target = self.cfg.output_device_name
        with self._audio_lock:  # never enumerate while a refresh swaps PortAudio
            try:
                loop = dv.loopback_by_name(self.p, target) if target else None
                if loop is None:
                    loop = dv.default_loopback(self.p)
                mic = dv.mic_for_mode(self.p, self.cfg.capture_mode, self.cfg.me_device_index)
                loops = dv.list_loopbacks(self.p)
            except Exception as exc:  # noqa: BLE001 - a device vanishing mid-read
                log.warning("device enumeration failed: %s", exc)
                loop, mic, loops = None, None, []
        profile = classify(loop.rate, loop.channels) if loop else None
        choices = ["Follow Windows default"] + [dv.loopback_label(d) for d in loops]
        current = target or "Follow Windows default"
        folder = Path(self.cfg.output_folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            free_gb = shutil.disk_usage(folder).free / 1e9
        except Exception:  # noqa: BLE001
            free_gb = 0.0
        self._pf_cache = {
            "others_device": loop.name if loop else "(no loopback found)",
            "me_device": mic.name if mic else "(no mic found)",
            "mode": self.cfg.capture_mode,
            "profile": profile,
            "model": self.model_status(),
            "free_gb": free_gb,
            "output_choices": choices,
            "output_current": current,
        }
        self._pf_time = now
        return self._pf_cache

    def set_output_device(self, label: str) -> None:
        self.cfg.output_device_name = "" if label == "Follow Windows default" else label
        self.cfg.save()
        self._pf_cache = None
        log.info("output (Others) device set to: %s", label)

    # ---- recent meetings --------------------------------------------------
    def recent_meetings(self) -> list[Path]:
        base = Path(self.cfg.output_folder)
        if not base.exists():
            return []
        dirs = [d for d in base.iterdir() if d.is_dir()]
        return sorted(dirs, key=lambda d: d.name, reverse=True)[:8]

    def open_path(self, path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606 - Windows shell open
        except Exception as exc:  # noqa: BLE001
            print(f"[open] {exc}")

    def open_output_folder(self) -> None:
        Path(self.cfg.output_folder).mkdir(parents=True, exist_ok=True)
        self.open_path(self.cfg.output_folder)

    def open_last_transcript(self) -> None:
        if self.last_transcript and Path(self.last_transcript).exists():
            self.open_path(self.last_transcript)
        else:
            self._notify("No transcript yet", "Record and stop a meeting first.")

    def _notify(self, title: str, message: str) -> None:
        """Windows balloon/toast via the tray icon (no extra dependency)."""
        try:
            if self.tray and self.tray.icon is not None:
                self.tray.icon.notify(message, title)
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] {title}: {message} ({exc})")

    def needs_setup(self) -> bool:
        return not models.is_present(self.cfg.model)

    def download_model(self, on_done=None) -> None:
        """One-time model download (network). Re-warms the engine when finished."""
        name = self.cfg.model

        def run():
            ok = False
            try:
                print(f"[setup] downloading model '{name}' ...")
                models.download(name)
                self.engine = WhisperEngine(self.cfg)
                self.engine.warm()
                self.cap.engine = self.engine
                if not diar_models.is_present():
                    print("[setup] downloading diarization models ...")
                    diar_models.download()
                    self._warm_diar()
                from .diarize import pyannote_engine
                if not (pyannote_engine.SEG.exists() and pyannote_engine.EMB.exists()):
                    print("[setup] downloading pyannote speaker weights ...")
                    try:
                        pyannote_engine.download()
                    except Exception as exc:  # noqa: BLE001 — sherpa still works without it
                        print(f"[setup] pyannote weights failed (using built-in engine): {exc}")
                ok = True
                self._notify("Model ready", f"{name} + diarization downloaded.")
            except Exception as exc:  # noqa: BLE001
                self._notify("Model download failed", str(exc))
                print(f"[setup] download failed: {exc}")
            finally:
                if on_done:
                    on_done(ok)

        threading.Thread(target=run, daemon=True).start()

    def model_status(self) -> str:
        if self.engine.is_ready():
            return f"{self.engine.model_name} (ready)"
        if self.engine.error == "model-missing":
            return f"{self.cfg.model} — MISSING (run setup)"
        if self.engine.error:
            return f"{self.cfg.model} — error"
        return f"{self.cfg.model} (loading…)"

    # ---- window / settings (marshalled to Tk thread) ----------------------
    def show_dashboard(self) -> None:
        if self.dashboard:
            self.dashboard.after(0, self.dashboard.show)

    def show_settings(self) -> None:
        if self.dashboard:
            self.dashboard.after(0, lambda: (self.dashboard.show(),
                                             self.dashboard.show_view("settings")))

    def show_voices(self) -> None:
        if self.dashboard:
            self.dashboard.after(0, lambda: (self.dashboard.show(),
                                             self.dashboard.show_view("voices")))

    def open_viewer(self, folder) -> None:
        if self.dashboard:
            self.dashboard.after(0, lambda: self._open_viewer(folder))

    def _open_viewer(self, folder) -> None:
        from .ui.speakers import TranscriptViewer
        TranscriptViewer(self.dashboard, self, folder)

    def open_speaker_rename(self, folder, on_done=None) -> None:
        if self.dashboard:
            self.dashboard.after(0, lambda: self._open_rename(folder, on_done))

    def _open_rename(self, folder, on_done) -> None:
        from .ui.speakers import RenameSpeakersDialog
        RenameSpeakersDialog(self.dashboard, self, folder, on_done)

    def save_settings(self, me_name: str, capture_mode: str, output_folder: str,
                      hotkey: str | None = None, notes_enabled: bool | None = None,
                      notes_backend: str | None = None, cloud_allowed: bool | None = None,
                      gemini_api_key: str | None = None, diarization_enabled: bool | None = None,
                      delete_raw_audio: bool | None = None, vocabulary: str | None = None) -> None:
        self.cfg.me_name = me_name.strip() or "Me"
        self.cfg.capture_mode = capture_mode
        self.cfg.output_folder = output_folder.strip() or self.cfg.output_folder
        if hotkey is not None and hotkey.strip() != self.cfg.hotkey and self.hotkeys is not None:
            self.cfg.hotkey = hotkey.strip()
            self.hotkeys.start(self.cfg.hotkey)  # rebind
        if notes_enabled is not None:
            self.cfg.notes_enabled = notes_enabled
        if notes_backend is not None:
            self.cfg.notes_backend = notes_backend
        if cloud_allowed is not None:
            self.cfg.cloud_allowed = cloud_allowed
        if gemini_api_key is not None:
            self.cfg.gemini_api_key = gemini_api_key.strip()
        if diarization_enabled is not None:
            self.cfg.diarization_enabled = diarization_enabled
        if delete_raw_audio is not None:
            self.cfg.delete_raw_audio = delete_raw_audio
        if vocabulary is not None:
            self.cfg.vocabulary = vocabulary
        self.cfg.save()
        self._pf_cache = None  # force pre-flight refresh

    # ---- errors -----------------------------------------------------------
    def _error(self, msg: str) -> None:
        log.error("%s", msg)
        if self.tray:
            self.tray.set_state("error", "QuietMinutes — error")
        if self.dashboard:
            self.dashboard.after(0, lambda: messagebox.showerror("QuietMinutes", msg))

    # ---- lifecycle --------------------------------------------------------
    def quit(self) -> None:
        if self.cap.state != "idle":
            try:
                self.cap.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.tray:
            self.tray.stop()
        if self.dashboard:
            self.dashboard.after(0, self.dashboard.destroy)

    def shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        if self.hotkeys is not None:
            self.hotkeys.stop()
        try:
            if self.cap.state != "idle":
                self.cap.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.p.terminate()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    setup_logging()
    ctk.set_appearance_mode("dark")
    controller = Controller()
    dashboard = Dashboard(controller)
    tray = Tray(controller)
    controller.attach(tray, dashboard)

    threading.Thread(target=tray.run, daemon=True).start()

    from .ui.hotkey import HotkeyManager
    controller.hotkeys = HotkeyManager(controller.toggle_record)
    controller.hotkeys.start(controller.cfg.hotkey)

    # First-run: if the model isn't downloaded yet, land on Settings and prompt.
    if controller.needs_setup():
        dashboard.show_view("settings")
        dashboard.after(600, lambda: controller._notify(
            "Setup needed", "Download the Whisper model in Settings to enable transcription."))

    try:
        dashboard.mainloop()
    finally:
        controller.shutdown()


if __name__ == "__main__":
    main()
