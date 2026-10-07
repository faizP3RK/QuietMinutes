"""Local Whisper model management.

Models are downloaded ONCE into %LOCALAPPDATA%\\QuietMinutes\\models\\<name> and
loaded from that path at runtime with HF_HUB_OFFLINE=1 — we never contact Hugging Face
again. A missing model is reported (prompt to run setup), never silently re-downloaded.
"""

from __future__ import annotations

from pathlib import Path

from .config import data_dir

MODELS_DIR = data_dir() / "models"
DEFAULT_MODEL = "small.en"
# Default + the accuracy/HFP fallback the brief specifies.
SUPPORTED = ("small.en", "distil-large-v3")


def model_dir(name: str) -> Path:
    return MODELS_DIR / name


def is_present(name: str) -> bool:
    d = model_dir(name)
    return (d / "model.bin").exists() and (d / "config.json").exists()


def download(name: str) -> Path:
    """One-time download into the local models dir. Network is used only here."""
    from faster_whisper import download_model

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out = model_dir(name)
    out.mkdir(parents=True, exist_ok=True)
    download_model(name, output_dir=str(out))
    return out
