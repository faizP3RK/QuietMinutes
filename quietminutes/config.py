"""User config + well-known paths, all under the user profile (no admin)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "QuietMinutes"
LEGACY_APP_NAME = "MeetingScribe"  # the app's earlier name; existing installs keep their data


def data_dir() -> Path:
    """%LOCALAPPDATA%\\QuietMinutes — models, db, config, logs.
    Installs from before the rename keep using %LOCALAPPDATA%\\MeetingScribe."""
    base = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    d, legacy = base / APP_NAME, base / LEGACY_APP_NAME
    if not d.exists() and legacy.exists():
        return legacy
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "MeetingTranscripts"


CONFIG_PATH = data_dir() / "config.json"


@dataclass
class Config:
    # --- Phase 1 (capture) ---
    me_name: str = "Me"
    # "quality" = laptop mic for Me, AirPods output-only (A2DP).
    # "convenience" = AirPods mic for Me (HFP).
    capture_mode: str = "quality"
    # Explicit "Me" mic device index; None = auto-pick (builtin mic in quality mode,
    # default input in convenience mode).
    me_device_index: int | None = None
    # Which output endpoint to loopback-capture for the "Others" track. "" = follow the
    # Windows default. Stored by name (survives index changes / reconnects). Set this to
    # pin a specific device (e.g. AirPods) — important on VDI/AVD where the default is flaky.
    output_device_name: str = ""
    output_folder: str = field(default_factory=lambda: str(default_output_dir()))
    delete_raw_audio: bool = True  # honored in Phase 2
    # global start/stop hotkey (pynput GlobalHotKeys syntax)
    hotkey: str = "<ctrl>+<alt>+r"

    # --- forward-looking (later phases; harmless defaults now) ---
    model: str = "small.en"
    # Domain terms Whisper should get right (comma/space separated). Fed to the model
    # as a prompt so jargon/product names stop being mangled (e.g. "Kubernetes" not "Cooper Netties").
    vocabulary: str = ""
    diarization_enabled: bool = True
    # "pyannote" (benchmarked ~2x more accurate; used when installed) | "sherpa" (fast, legacy)
    diar_engine: str = "pyannote"
    diar_threshold: float = 0.5  # first-pass clustering threshold (higher = fewer speakers)
    # Second-pass auto-merge FLOOR: below this cosine similarity clusters are never
    # merged. (Medium similarities are further gated by speech duration — see
    # pipeline.merge_clusters.)
    diar_merge_threshold: float = 0.50
    # Clusters with less than this many seconds of speech never get their own label —
    # they're absorbed into the nearest real speaker (kills "Speaker 7/8/9" noise).
    diar_min_speaker_s: float = 5.0
    notes_enabled: bool = False        # auto-generate notes.md after each transcript
    notes_backend: str = "local"       # "local" | "gemini"
    cloud_allowed: bool = False        # explicit consent to send TEXT to the cloud
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-lite"   # cheapest capable tier
    ollama_model: str = "gemma3:4b"   # benchmarked best local notes model (vs llama3.2/qwen3)
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_keep_alive: str = "2m"  # unload the model from RAM 2 min after use (saves memory)
    https_proxy: str = ""  # optional corporate proxy, e.g. http://proxy:8080 (blank = none)
    auto_name_threshold: float = 0.70
    beam_size: int = 1            # 1 = fastest (much less CPU/lag); 5 = most accurate
    cpu_threads: int = 0          # 0 = auto (leave ~half the cores free to avoid lag)
    vad_threshold: float = 0.5

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        # Decrypt the API key into memory; migrate legacy plaintext to encrypted at rest.
        stored = raw.get("gemini_api_key", "")
        try:
            from .secrets import is_encrypted, unprotect
            cfg.gemini_api_key = unprotect(stored)
            if stored and not is_encrypted(stored):
                cfg.save()  # one-time migration -> encrypted on disk
        except Exception:  # noqa: BLE001
            pass
        return cfg

    def save(self) -> None:
        data = asdict(self)
        try:
            from .secrets import protect
            if data.get("gemini_api_key"):
                data["gemini_api_key"] = protect(data["gemini_api_key"])
        except Exception:  # noqa: BLE001
            pass
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
