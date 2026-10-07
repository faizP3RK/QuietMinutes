"""Level metering helpers for VU meters and silent-capture detection."""

from __future__ import annotations

import math

import numpy as np

SILENCE_PEAK = 0.002  # below this we treat a track as silent


def peak_rms(data: np.ndarray) -> tuple[float, float]:
    if data is None or data.size == 0:
        return (0.0, 0.0)
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    return (peak, rms)


def dbfs(x: float) -> float:
    if x <= 0:
        return -float("inf")
    return 20.0 * math.log10(x)


def meter_fraction(peak: float) -> float:
    """Map a linear peak (0..1) to a 0..1 VU bar using a dBFS scale (-60..0)."""
    if peak <= 0:
        return 0.0
    db = dbfs(peak)
    return max(0.0, min(1.0, (db + 60.0) / 60.0))
