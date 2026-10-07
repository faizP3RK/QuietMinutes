"""Warm, reusable faster-whisper engine.

Loaded ONCE at startup on a background thread and reused across meetings (never
reloaded per recording). CPU-only, int8, with VAD on to skip silence.

Self-healing: `ensure_ready()` waits for an in-flight load and re-attempts a failed one,
so callers (e.g. the post-meeting batch transcription) never silently get nothing.
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np

from .. import models
from ..logging_setup import log

# Common Whisper hallucinations on silence/noise — dropped from output.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching!", "thank you for watching.", "you", ".",
    "thanks for watching.", "bye.", "subtitles by the amara.org community",
}


class WhisperEngine:
    def __init__(self, config):
        self.cfg = config
        self.model = None
        self.model_name = config.model
        self.ready = threading.Event()
        self.error: str | None = None  # "model-missing" or an exception string
        self._lock = threading.Lock()
        self._loading = threading.Lock()

    # ---- lifecycle --------------------------------------------------------
    def warm(self) -> None:
        """Kick off background load. Safe to call repeatedly."""
        threading.Thread(target=self._load, name="whisper-load", daemon=True).start()

    def _load(self) -> None:
        if not self._loading.acquire(blocking=False):
            return  # a load is already in flight
        t0 = time.monotonic()
        try:
            if self.is_ready():
                return
            os.environ["HF_HUB_OFFLINE"] = "1"  # never reach out to HF at runtime
            self.model_name = self.cfg.model
            if not models.is_present(self.model_name):
                self.error = "model-missing"
                log.error("whisper model '%s' not found locally; run setup", self.model_name)
                return
            from faster_whisper import WhisperModel

            # Leave headroom so background transcription doesn't lag the whole machine.
            cfg_threads = getattr(self.cfg, "cpu_threads", 0)
            threads = cfg_threads if cfg_threads and cfg_threads > 0 else max(1, (os.cpu_count() or 4) // 2)
            self.model = WhisperModel(
                str(models.model_dir(self.model_name)),
                device="cpu",
                compute_type="int8",
                cpu_threads=threads,
            )
            self.error = None
            self.ready.set()
            log.info("whisper loaded '%s' (int8, %d threads) in %.1fs",
                     self.model_name, threads, time.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.exception("whisper load FAILED: %s", exc)
        finally:
            self._loading.release()

    def is_ready(self) -> bool:
        return self.ready.is_set() and self.model is not None

    def ensure_ready(self, timeout: float = 180.0) -> bool:
        """Block until the model is usable, retrying a failed load. Never raises."""
        deadline = time.monotonic() + timeout
        attempts = 0
        while time.monotonic() < deadline:
            if self.is_ready():
                return True
            if self.error == "model-missing":
                return False
            if not self._loading.locked() and attempts < 3:
                attempts += 1
                if attempts > 1:
                    log.warning("whisper not ready (error=%s) — reload attempt %d", self.error, attempts)
                self.warm()
            self.ready.wait(1.0)
        return self.is_ready()

    # ---- transcription ----------------------------------------------------
    def _prompt(self):
        vocab = (getattr(self.cfg, "vocabulary", "") or "").strip()
        return f"Domain terms: {vocab}." if vocab else None

    def _collect(self, segments, offset, progress=None) -> list[dict]:
        out = []
        for s in segments:
            text = s.text.strip()
            if progress is not None:
                progress(s.end)
            if not text or text.lower() in _HALLUCINATIONS:
                continue
            # no_speech_prob high + low avg_logprob = Whisper talking to itself
            if getattr(s, "no_speech_prob", 0) > 0.8 and getattr(s, "avg_logprob", 0) < -1.0:
                continue
            seg = {"start": s.start + offset, "end": s.end + offset, "text": text}
            words = getattr(s, "words", None)
            if words:  # per-word timing -> lets speakers change mid-sentence correctly
                seg["words"] = [(w.start + offset, w.end + offset, w.word) for w in words]
            out.append(seg)
        return out

    def transcribe_file(self, path, offset: float = 0.0) -> list[dict]:
        """Transcribe an audio file (any rate/channels — PyAV resamples to 16 kHz).
        Returns segments [{start, end, text}] with `offset` added to timestamps."""
        if not self.is_ready():
            return []
        with self._lock:  # CT2 model is not re-entrant across threads
            segments, _info = self.model.transcribe(
                str(path), beam_size=self.cfg.beam_size, vad_filter=True,
                language="en", initial_prompt=self._prompt(), word_timestamps=True)
            return self._collect(segments, offset)

    def transcribe_samples(self, samples: np.ndarray, offset: float = 0.0,
                           progress=None) -> list[dict]:
        """Transcribe a 16 kHz mono float32 array (used by batch transcription).
        `progress(sec_within_slice)` is called as segments are produced."""
        if not self.is_ready() or samples is None or samples.size < 1600:
            return []
        with self._lock:
            segments, _info = self.model.transcribe(
                np.ascontiguousarray(samples, dtype=np.float32), beam_size=self.cfg.beam_size,
                vad_filter=True, language="en", initial_prompt=self._prompt(), word_timestamps=True)
            return self._collect(segments, offset, progress)
