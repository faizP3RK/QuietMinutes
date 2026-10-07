"""Offline speaker diarization via sherpa-onnx (CPU, no PyTorch, no HF token).

Runs once at end-of-meeting on the full 'Others' WAV (a global view gives consistent
clustering across many speakers). Uses cluster-threshold mode (num_clusters=-1) so we
don't need to know the speaker count in advance.
"""

from __future__ import annotations

import threading

import numpy as np
import soundfile as sf

from . import models

import os as _os

# Same budget as Whisper: half the cores (was hard-coded to 2 — the slowest stage).
_THREADS = max(2, (_os.cpu_count() or 4) // 2)


def _to_mono_16k(path: str, target_sr: int) -> np.ndarray:
    """Load any WAV as mono float32 at target_sr, BLOCKWISE.

    The old implementation materialized float64 index arrays over the whole file,
    which spiked to several GB on a 1-hour recording (crash risk). This version
    streams ~60s blocks, so peak memory is the output array plus one block.
    """
    with sf.SoundFile(path) as f:
        sr = f.samplerate
        n_in = f.frames
        if sr == target_sr:
            out = np.empty(n_in, dtype=np.float32)
            pos = 0
            while True:
                block = f.read(sr * 60, dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                mono = block.mean(axis=1)
                out[pos:pos + mono.shape[0]] = mono
                pos += mono.shape[0]
            return np.ascontiguousarray(out[:pos])

        ratio = sr / float(target_sr)
        n_out = int(n_in / ratio)
        out = np.empty(n_out, dtype=np.float32)
        block_out = target_sr * 60  # ~60s of output per iteration
        for o0 in range(0, n_out, block_out):
            o1 = min(o0 + block_out, n_out)
            # source range covering these output samples (+1 for interpolation edge)
            s0 = int(o0 * ratio)
            s1 = min(int(o1 * ratio) + 2, n_in)
            f.seek(s0)
            block = f.read(s1 - s0, dtype="float32", always_2d=True)
            mono = block.mean(axis=1)
            # positions of output samples inside this source block
            pos = (np.arange(o0, o1, dtype=np.float64) * ratio) - s0
            np.clip(pos, 0, mono.shape[0] - 1, out=pos)
            out[o0:o1] = np.interp(pos, np.arange(mono.shape[0], dtype=np.float64),
                                   mono).astype(np.float32)
        return np.ascontiguousarray(out)


class Diarizer:
    def __init__(self, threshold: float = 0.5):
        self.sd = None
        self.threshold = threshold
        self.error: str | None = None
        self._lock = threading.Lock()

    def is_present(self) -> bool:
        return models.is_present()

    def set_threshold(self, threshold: float) -> None:
        """Clustering threshold: higher merges more (fewer speakers). Rebuilds on next use."""
        if abs(threshold - self.threshold) > 1e-6:
            self.threshold = threshold
            self.sd = None

    def load(self) -> bool:
        if self.sd is not None:
            return True
        if not models.is_present():
            self.error = "models-missing"
            return False
        try:
            import sherpa_onnx as so

            cfg = so.OfflineSpeakerDiarizationConfig(
                segmentation=so.OfflineSpeakerSegmentationModelConfig(
                    pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(models.SEG_PATH)),
                    num_threads=_THREADS,
                ),
                embedding=so.SpeakerEmbeddingExtractorConfig(
                    model=str(models.EMB_PATH), num_threads=_THREADS),
                clustering=so.FastClusteringConfig(num_clusters=-1, threshold=self.threshold),
                min_duration_on=0.3,
                min_duration_off=0.5,
            )
            self.sd = so.OfflineSpeakerDiarization(cfg)
            self.error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            print(f"[diar] load failed: {exc}")
            return False

    def diarize(self, wav_path: str, threshold: float = 0.5) -> list[dict]:
        """Return [{start, end, speaker}] where speaker is an int cluster id."""
        if not self.load():
            return []
        with self._lock:
            samples = _to_mono_16k(wav_path, self.sd.sample_rate)
            result = self.sd.process(samples).sort_by_start_time()
            return [{"start": s.start, "end": s.end, "speaker": s.speaker} for s in result]
