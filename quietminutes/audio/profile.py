"""Classify a render-endpoint mix format into a Bluetooth profile.

The whole point of Phase 0: AirPods in HFP collapse to narrowband mono "phone-call"
audio (<=16 kHz / mono), which wrecks transcription and many-speaker diarization.
A2DP stays full-quality stereo. We infer the profile from the mix format.
"""

from __future__ import annotations

from dataclasses import dataclass

A2DP = "A2DP"
HFP = "HFP"
OTHER = "OTHER"


@dataclass(frozen=True)
class Profile:
    status: str  # A2DP | HFP | OTHER
    label: str
    color: str  # hex for the UI pill
    is_warning: bool


def classify(sample_rate: int, channels: int) -> Profile:
    if sample_rate <= 16000 or channels < 2:
        return Profile(
            HFP,
            f"HFP — narrowband {sample_rate} Hz / {channels}ch (phone-call quality)",
            "#E0A100",  # amber
            True,
        )
    if sample_rate >= 44100 and channels >= 2:
        return Profile(
            A2DP,
            f"A2DP — {sample_rate} Hz / {channels}ch (full quality)",
            "#2FA85A",  # green
            False,
        )
    return Profile(
        OTHER,
        f"{sample_rate} Hz / {channels}ch",
        "#888888",
        False,
    )
