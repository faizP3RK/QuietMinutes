"""One-time download of the two offline diarization ONNX models.

Both come from the k2-fsa/sherpa-onnx GitHub releases (no HF token, no PyTorch):
  * segmentation  — sherpa-onnx-pyannote-segmentation-3-0 (who-speaks-when)
  * embedding     — 3dspeaker speaker-embedding (voiceprint vectors; also powers the DB)

Stored under %LOCALAPPDATA%\\QuietMinutes\\models\\diarization\\ and loaded locally.
"""

from __future__ import annotations

import tarfile
import urllib.request
from pathlib import Path

from ..config import data_dir

DIAR_DIR = data_dir() / "models" / "diarization"

_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEG_URL = f"{_BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
# English CAM++ speaker-embedding (16 kHz) — used for diarization clustering AND the
# voiceprint DB. Names verified against the k2-fsa release assets.
EMB_NAME = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
EMB_URL = f"{_BASE}/speaker-recongition-models/{EMB_NAME}"

SEG_PATH = DIAR_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_PATH = DIAR_DIR / EMB_NAME


def is_present() -> bool:
    return SEG_PATH.exists() and EMB_PATH.exists()


def download() -> tuple[Path, Path]:
    DIAR_DIR.mkdir(parents=True, exist_ok=True)
    if not EMB_PATH.exists():
        print(f"[diar] downloading embedding model -> {EMB_PATH.name}")
        urllib.request.urlretrieve(EMB_URL, EMB_PATH)
    if not SEG_PATH.exists():
        print("[diar] downloading + extracting segmentation model")
        tarp = DIAR_DIR / "seg.tar.bz2"
        urllib.request.urlretrieve(SEG_URL, tarp)
        with tarfile.open(tarp, "r:bz2") as t:
            t.extractall(DIAR_DIR)
        tarp.unlink()
    return SEG_PATH, EMB_PATH
