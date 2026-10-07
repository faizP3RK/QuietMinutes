"""Batch (post-meeting) transcription of saved WAV tracks.

The safety net: if live rolling transcription wasn't running (model still loading when
Start was pressed, a crash, anything), the meeting is transcribed from its saved audio
instead of being lost. Also powers "Transcribe now" for older untranscribed meetings.

Audio is loaded blockwise to 16 kHz mono, then processed in ~10-minute slices whose
boundaries are moved to the quietest nearby moment so no word is cut in half.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..diarize.engine import _to_mono_16k
from ..logging_setup import log

SR = 16000
SLICE_S = 600        # ~10 min per slice (bounded memory + steady progress)
SEARCH_S = 5         # look +/- this many seconds for a quiet cut point
FRAME = SR // 10     # 100 ms energy frames


def track_has_audio(samples: np.ndarray, floor: float = 0.003) -> bool:
    return samples.size > 0 and float(np.max(np.abs(samples))) >= floor


def _quiet_cut(samples: np.ndarray, target: int) -> int:
    lo = max(0, target - SEARCH_S * SR)
    hi = min(len(samples), target + SEARCH_S * SR)
    if hi - lo < FRAME * 2:
        return target
    win = samples[lo:hi]
    n = len(win) // FRAME
    energy = np.sqrt(np.mean(np.square(win[: n * FRAME].reshape(n, FRAME)), axis=1))
    return lo + int(np.argmin(energy)) * FRAME


def slices(total: int) -> list[tuple[int, int]]:
    return [(a, min(a + SLICE_S * SR, total)) for a in range(0, total, SLICE_S * SR)]


def transcribe_wav(engine, wav: Path, label: str, progress=None) -> list[dict]:
    """Transcribe one track. `progress(fraction_0_to_1)` is called as it advances."""
    samples = _to_mono_16k(str(wav), SR)
    total = len(samples)
    if not track_has_audio(samples):
        log.info("batch: %s is silent — skipped", wav.name)
        if progress:
            progress(1.0)
        return []
    out: list[dict] = []
    start = 0
    while start < total:
        end = total if total - start <= SLICE_S * SR * 1.2 else _quiet_cut(samples, start + SLICE_S * SR)
        chunk = samples[start:end]
        base = start / SR

        def prog(sec, base=base):
            if progress:
                progress(min(1.0, (base + sec) * SR / total))

        segs = engine.transcribe_samples(chunk, offset=base, progress=prog)
        for s in segs:
            s["speaker"] = label
        out.extend(segs)
        start = end
        if progress:
            progress(min(1.0, start / total))
    log.info("batch: %s -> %d segments (%.1f min audio)", wav.name, len(out), total / SR / 60)
    return out


def transcribe_meeting(engine, folder: Path, me_name: str, progress=None) -> list[dict]:
    """Transcribe others.wav (as 'Speaker') + me.wav (as me_name); merged by time.
    progress(fraction) spans both tracks (others weighted 60%, me 40%)."""
    folder = Path(folder)
    segs: list[dict] = []
    others, me = folder / "others.wav", folder / "me.wav"
    if others.exists():
        segs += transcribe_wav(engine, others, "Speaker",
                               lambda f: progress and progress(0.6 * f))
    if me.exists():
        segs += transcribe_wav(engine, me, me_name,
                               lambda f: progress and progress(0.6 + 0.4 * f))
    return sorted(segs, key=lambda s: s["start"])
