"""Phase 3 integration: diarize -> label -> snippets -> write -> rename -> re-render."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from quietminutes import meeting
from quietminutes.diarize import pipeline, voiceprints
from quietminutes.diarize.embeddings import Embedder
from quietminutes.diarize.engine import Diarizer
from quietminutes.transcribe.writer import write_outputs

OUT = Path("phase3_finalize_out")
shutil.rmtree(OUT, ignore_errors=True)
folder = OUT / "2026-06-18_10-00 - Test Meeting"
folder.mkdir(parents=True, exist_ok=True)
shutil.copy("phase3_proof/twovoice.wav", folder / "others.wav")
meeting.write_meta(folder, name="Test Meeting", ts="2026-06-18_10-00")
voiceprints.DB_PATH = OUT / "vp.db"


def main() -> int:
    diar = Diarizer(threshold=0.9)
    emb = Embedder()
    d = diar.diarize(str(folder / "others.wav"))

    segs = [{"start": x["start"], "end": x["end"], "text": f"others line {i}",
             "speaker": "Speaker"} for i, x in enumerate(d)]
    segs.insert(0, {"start": 0.0, "end": 1.0, "text": "hello team", "speaker": "Alex"})
    pipeline.label_segments(segs, d)
    speakers, embs = pipeline.build_speakers(segs, d, str(folder / "others.wav"), emb, 0.7,
                                             folder=folder)
    pipeline.save_speakers(folder, speakers, embs)  # _finalize does this
    (folder / "segments.json").write_text(
        json.dumps([{k: s[k] for k in ("start", "end", "text", "speaker")} for s in segs],
                   indent=2), encoding="utf-8")
    write_outputs(folder, segs, title="Test Meeting")

    labels = [v["label"] for v in speakers.values()]
    snippets_ok = all((folder / v["snippet"]).exists() for v in speakers.values())
    print(f"speakers: {labels}   snippets_ok={snippets_ok}")
    print("--- transcript before ---\n" + (folder / "transcript.txt").read_text(encoding="utf-8"))

    mapping = {labels[0]: "Alice"}
    if len(labels) > 1:
        mapping[labels[1]] = "Bob"
    applied = pipeline.apply_names(folder, mapping)
    txt = (folder / "transcript.txt").read_text(encoding="utf-8")
    print(f"applied: {applied}")
    print("--- transcript after ---\n" + txt)
    print(f"enrolled: {[p['name'] for p in voiceprints.list_people()]}")

    ok = snippets_ok and "Alice" in txt and "Alex" in txt \
        and ("Bob" in txt if len(labels) > 1 else True) \
        and len(voiceprints.list_people()) >= 1
    print("SMOKE FINALIZE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
