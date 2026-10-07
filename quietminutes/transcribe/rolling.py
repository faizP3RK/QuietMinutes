"""Rolling, chunked transcription that runs DURING the meeting.

Audio is teed from the capture writer (per track) into per-track buffers. When a buffer
reaches a minimum length AND a silence tail is detected (so we never cut mid-word), or a
hard maximum length, the chunk is handed to a background worker that transcribes it with
faster-whisper and offsets the timestamps by the chunk's wall-clock start. On Stop, only
the final partial chunk remains, so finalize completes in seconds.

Timestamps are kept on the recording's wall clock (the capture layer already pads silence
so each track's sample index == elapsed time), which lets us merge Me + Others correctly.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import uuid
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf

MIN_CHUNK_S = 20.0     # don't cut shorter than this
MAX_CHUNK_S = 45.0     # hard cap — cut even without a silence tail
SILENCE_TAIL_S = 0.6   # require this much trailing quiet before a soft cut
SILENCE_PEAK = 0.01    # below this a fed buffer counts as "quiet"


@dataclass
class _TrackState:
    sr: int
    ch: int
    label: str
    buffers: list = field(default_factory=list)
    frames: int = 0
    start_frame: int = 0
    silent_run: int = 0


class RollingTranscriber:
    def __init__(self, engine, me_name: str):
        self.engine = engine
        self.me_name = me_name
        self.tracks: dict[str, _TrackState] = {}
        self.segments: list[dict] = []
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    def register(self, key: str, sr: int, ch: int, label: str) -> None:
        self.tracks[key] = _TrackState(sr=sr, ch=ch, label=label)

    # ---- producer side (called from the capture writer thread) -----------
    def feed(self, key: str, data: np.ndarray, start_frame: int) -> None:
        st = self.tracks.get(key)
        if st is None or data.size == 0:
            return
        with self._lock:
            if not st.buffers:
                st.start_frame = start_frame
            st.buffers.append(data)
            st.frames += data.shape[0]
            peak = float(np.abs(data).max())
            st.silent_run = st.silent_run + data.shape[0] if peak < SILENCE_PEAK else 0
            dur = st.frames / st.sr
            soft = dur >= MIN_CHUNK_S and st.silent_run >= SILENCE_TAIL_S * st.sr
            if dur >= MAX_CHUNK_S or soft:
                self._cut_locked(key)

    def _cut_locked(self, key: str) -> None:
        st = self.tracks[key]
        if not st.buffers:
            return
        audio = np.concatenate(st.buffers, axis=0)
        start_time = st.start_frame / st.sr
        st.buffers = []
        st.frames = 0
        st.silent_run = 0
        self._q.put((st.sr, st.ch, st.label, audio, start_time))

    # ---- consumer side ----------------------------------------------------
    def _work(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                break
            sr, ch, label, audio, start_time = item
            try:
                self._transcribe_chunk(sr, ch, label, audio, start_time)
            except Exception as exc:  # noqa: BLE001
                print(f"[rolling] chunk error: {exc}")
            finally:
                self._q.task_done()

    def _transcribe_chunk(self, sr, ch, label, audio, start_time) -> None:
        tmp = os.path.join(tempfile.gettempdir(), f"ms_chunk_{uuid.uuid4().hex}.wav")
        try:
            sf.write(tmp, audio, sr, subtype="PCM_16")
            segs = self.engine.transcribe_file(tmp, offset=start_time)
            for s in segs:
                s["speaker"] = label
            if segs:
                with self._lock:
                    self.segments.extend(segs)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ---- finalize ---------------------------------------------------------
    def finalize(self) -> list[dict]:
        """Flush remaining buffers, wait for the worker to drain, return sorted segments."""
        with self._lock:
            for key in self.tracks:
                self._cut_locked(key)
        self._q.join()           # wait until all queued chunks are transcribed
        self._q.put(None)        # stop the worker
        with self._lock:
            return sorted(self.segments, key=lambda s: s["start"])
