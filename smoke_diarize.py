"""Phase 3 proof: does sherpa-onnx diarization separate speakers on this machine?

Runs the Diarizer on a 2-voice SAPI clip and checks it finds ~2 speaker clusters.
"""

from __future__ import annotations

import time
from pathlib import Path

from quietminutes.diarize.engine import Diarizer

WAV = Path("phase3_proof") / "twovoice.wav"


def main() -> int:
    d = Diarizer()
    print("models present:", d.is_present())
    t = time.time()
    if not d.load():
        print(f"FAIL: load error = {d.error}")
        return 1
    print(f"loaded diarizer (sample_rate={d.sd.sample_rate}) in {time.time()-t:.1f}s")

    t = time.time()
    segs = d.diarize(str(WAV))
    speakers = sorted({s["speaker"] for s in segs})
    print(f"\ndiarized in {time.time()-t:.1f}s — {len(segs)} segments, "
          f"{len(speakers)} speakers {speakers}:")
    for s in segs:
        print(f"  [{s['start']:5.1f}-{s['end']:5.1f}]  Speaker {s['speaker']}")

    # SAPI alternates two voices, so expect exactly 2 clusters.
    ok = len(speakers) == 2 and len(segs) >= 4
    print("\nSMOKE DIARIZE:", "PASS" if ok else f"CHECK (got {len(speakers)} speakers)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
