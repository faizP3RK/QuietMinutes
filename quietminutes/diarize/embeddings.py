"""Speaker-embedding extraction (sherpa-onnx) — the same model that powers diarization
clustering also produces the voiceprint vectors stored in the DB.
"""

from __future__ import annotations

import numpy as np

from . import models
from .engine import _to_mono_16k

SAMPLE_RATE = 16000


class Embedder:
    def __init__(self):
        self.ex = None

    def load(self) -> bool:
        if self.ex is not None:
            return True
        if not models.EMB_PATH.exists():
            return False
        import sherpa_onnx as so

        self.ex = so.SpeakerEmbeddingExtractor(
            so.SpeakerEmbeddingExtractorConfig(model=str(models.EMB_PATH)))
        return True

    def embed_samples(self, samples: np.ndarray) -> np.ndarray | None:
        """L2-normalized embedding for a mono 16 kHz float32 waveform."""
        if not self.load() or samples is None or samples.size < SAMPLE_RATE // 4:
            return None
        stream = self.ex.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE,
                               waveform=np.ascontiguousarray(samples, dtype=np.float32))
        stream.input_finished()
        if not self.ex.is_ready(stream):
            return None
        v = np.asarray(self.ex.compute(stream), dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def embed_regions(self, wav_path: str, regions: list[tuple[float, float]]) -> np.ndarray | None:
        """Concatenate the given [start,end] regions of a WAV and embed them as one voice."""
        audio = _to_mono_16k(wav_path, SAMPLE_RATE)
        chunks = []
        for start, end in regions:
            a, b = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
            if b > a:
                chunks.append(audio[a:b])
        if not chunks:
            return None
        return self.embed_samples(np.concatenate(chunks))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))  # both are L2-normalized
