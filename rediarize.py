"""Re-run diarization on an already-recorded meeting (raw audio must still be present).

Lets us tune clustering without re-recording. Usage:

    .venv\\Scripts\\python.exe rediarize.py "C:\\path\\to\\meeting folder" [threshold] [--dry]
    .venv\\Scripts\\python.exe rediarize.py "..." 0.5 --merge 0.6 --min 5 --dry

--dry prints the resulting speaker counts WITHOUT touching any files (safe tuning).
Lower threshold -> MORE raw clusters; --merge lower -> merge more aggressively.
Without --dry: re-writes transcript.txt/.srt/.md + speakers.json + embeddings + snippets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from quietminutes import meeting
from quietminutes.config import Config
from quietminutes.diarize import pipeline
from quietminutes.diarize.embeddings import Embedder
from quietminutes.diarize.engine import Diarizer
from quietminutes.transcribe.writer import write_outputs


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    folder = Path(args[0])
    cfg = Config.load()
    threshold = float(args[1]) if len(args) > 1 else cfg.diar_threshold
    dry = "--dry" in flags

    def flag_val(name, default):
        if name in flags:
            i = flags.index(name)
            if i + 1 < len(flags):
                return float(flags[i + 1])
        return default

    merge_thr = flag_val("--merge", cfg.diar_merge_threshold)
    min_s = flag_val("--min", cfg.diar_min_speaker_s)

    others = folder / "others.wav"
    seg_p = folder / "segments.json"
    if not others.exists():
        print(f"!! {others} not found — raw audio was deleted. Enable 'Keep raw audio' and "
              "record again to tune on a real meeting.")
        return 1
    if not seg_p.exists():
        print(f"!! {seg_p} not found.")
        return 1

    segs = json.loads(seg_p.read_text(encoding="utf-8-sig"))  # tolerate a BOM
    # Reset any Others labels (Speaker / Speaker N) back to the placeholder so we re-cluster.
    # Real names already applied and the Me track are left untouched.
    me = cfg.me_name
    for s in segs:
        sp = s["speaker"]
        if sp == "Speaker" or sp.startswith("Speaker "):
            s["speaker"] = "Speaker"
    others_n = sum(1 for s in segs if s["speaker"] == "Speaker")
    print(f"others segments to cluster: {others_n}")

    diar = Diarizer(threshold=threshold)
    emb = Embedder()
    regions = diar.diarize(str(others))
    raw = sorted({d["speaker"] for d in regions})
    regions = pipeline.merge_clusters(regions, str(others), emb, merge_thr, min_s)
    clusters = sorted({d["speaker"] for d in regions})
    print(f"threshold={threshold} merge={merge_thr} min_s={min_s}: "
          f"{len(regions)} regions, {len(raw)} raw -> {len(clusters)} merged speaker(s)")
    per = {}
    for d in regions:
        per[d["speaker"]] = per.get(d["speaker"], 0.0) + (d["end"] - d["start"])
    for c in clusters:
        print(f"  Speaker {c + 1}: {per[c]:6.1f}s of speech")
    if dry:
        print("(--dry: no files changed)")
        return 0

    pipeline.label_segments(segs, regions)
    speakers, embs = pipeline.build_speakers(segs, regions, str(others), emb,
                                             cfg.auto_name_threshold, folder=folder)
    pipeline.save_speakers(folder, speakers, embs)
    seg_p.write_text(json.dumps([{k: s[k] for k in ("start", "end", "text", "speaker")}
                                 for s in segs], indent=2), encoding="utf-8")
    out = write_outputs(folder, segs, title=meeting.display_name(folder))
    meeting.write_meta(folder, speakers=len(speakers))
    print(f"re-wrote transcript with {len(speakers)} speaker(s). Tweak threshold and re-run if needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
