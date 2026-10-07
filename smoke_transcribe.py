"""Phase 2 pipeline smoke test (offline, no live capture).

1. Loads the warm WhisperEngine from the local model path (HF_HUB_OFFLINE).
2. Direct-transcribes a known SAPI clip -> prints text (eyeball accuracy).
3. Feeds both 'others.wav' + 'me.wav' through RollingTranscriber as if streamed
   (me offset later in time) -> finalize -> writes transcript.txt/.srt/.md.

Run after generating phase2_smoke_out/*.wav via SAPI:
    .venv\\Scripts\\python.exe -u smoke_transcribe.py
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import soundfile as sf

from quietminutes.config import Config
from quietminutes.transcribe.rolling import RollingTranscriber
from quietminutes.transcribe.whisper_engine import WhisperEngine
from quietminutes.transcribe.writer import write_outputs

OUT = Path("phase2_smoke_out")


def feed_wav(roll: RollingTranscriber, key: str, wav: Path, start_offset_s: float):
    data, sr = sf.read(str(wav), dtype="float32")
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    base = int(start_offset_s * sr)
    step = int(1.0 * sr)  # ~1s feed chunks, like the capture writer
    for i in range(0, len(data), step):
        roll.feed(key, data[i:i + step], base + i)


def main() -> int:
    cfg = Config.load()
    engine = WhisperEngine(cfg)
    engine.warm()
    print("loading model ...")
    if not engine.ready.wait(timeout=120):
        print(f"FAIL: engine not ready (error={engine.error})")
        return 1
    print(f"engine ready: {engine.model_name}")

    # --- 2. direct transcribe accuracy ---
    t = time.time()
    segs = engine.transcribe_file(OUT / "others.wav")
    print(f"\nDirect transcribe of others.wav ({time.time()-t:.1f}s):")
    for s in segs:
        print(f"  [{s['start']:.1f}-{s['end']:.1f}] {s['text']}")

    # --- 3. rolling, two tracks, merge + write ---
    roll = RollingTranscriber(engine, cfg.me_name)
    roll.register("others", 1, 1, "Speaker")   # sr/ch set per-feed below via real data
    roll.register("me", 1, 1, cfg.me_name)
    # fix sr/ch from the actual files
    for key, wav in [("others", OUT / "others.wav"), ("me", OUT / "me.wav")]:
        info = sf.info(str(wav))
        roll.tracks[key].sr = info.samplerate
        roll.tracks[key].ch = info.channels

    feed_wav(roll, "others", OUT / "others.wav", start_offset_s=0.0)
    feed_wav(roll, "me", OUT / "me.wav", start_offset_s=8.0)  # me speaks later
    t = time.time()
    merged = roll.finalize()
    print(f"\nRolling finalize ({time.time()-t:.1f}s), {len(merged)} segments merged:")
    for s in merged:
        print(f"  [{s['start']:.1f}] {s['speaker']}: {s['text']}")

    res = write_outputs(OUT, merged, title="Phase 2 smoke")
    print(f"\nwrote: {res}")
    txt = (OUT / 'transcript.txt').read_text(encoding='utf-8')
    print("\n--- transcript.txt ---\n" + txt)

    ok = len(merged) >= 2 and any(s["speaker"] == cfg.me_name for s in merged) \
        and any(s["speaker"] == "Speaker" for s in merged)
    print("SMOKE TRANSCRIBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
