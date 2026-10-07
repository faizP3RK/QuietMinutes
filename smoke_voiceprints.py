"""Phase 3 backend round-trip: diarize -> label -> embed -> enroll -> re-match.

Uses the 2-voice clip as a stand-in 'others.wav' and a temp voiceprint DB.
"""

from __future__ import annotations

from pathlib import Path

from quietminutes.diarize import pipeline, voiceprints
from quietminutes.diarize.embeddings import Embedder
from quietminutes.diarize.engine import Diarizer

WAV = str(Path("phase3_proof") / "twovoice.wav")
voiceprints.DB_PATH = Path("phase3_proof") / "test_vp.db"  # don't touch the real DB
voiceprints.DB_PATH.unlink(missing_ok=True)


def fake_transcript(diar):
    """One Others segment per diarized region + a Me line."""
    segs = [{"start": d["start"], "end": d["end"],
             "text": f"utterance from cluster {d['speaker']} at {d['start']:.0f}s",
             "speaker": "Speaker"} for d in diar]
    segs.append({"start": 2.0, "end": 3.0, "text": "this is me talking", "speaker": "Alex"})
    return sorted(segs, key=lambda s: s["start"])


def main() -> int:
    diar = Diarizer(threshold=0.9)
    embedder = Embedder()
    diar_segs = diar.diarize(WAV)
    clusters = sorted({d["speaker"] for d in diar_segs})
    print(f"diarized: {len(diar_segs)} segments, clusters {clusters}")

    segs = fake_transcript(diar_segs)
    pipeline.label_segments(segs, diar_segs)
    others = [s["speaker"] for s in segs if s["speaker"].startswith("Speaker")]
    print(f"relabeled Others -> {sorted(set(others))}")

    # First meeting: no DB yet -> no suggestions
    speakers, embs = pipeline.build_speakers(segs, diar_segs, WAV, embedder, threshold=0.7)
    print(f"clusters with embeddings: {sorted(embs.keys())}")
    print(f"suggestions (empty DB): {[(k, v['suggested']) for k, v in speakers.items()]}")

    # Implicit enrollment: user names the clusters
    names = {"0": "Alice", "1": "Bob"}
    for cid, nm in names.items():
        if cid in embs:
            voiceprints.add_embedding(nm, embs[cid])
    print(f"enrolled people: {[p['name'] for p in voiceprints.list_people()]}")

    # Next meeting (same voices): should now be suggested
    speakers2, _ = pipeline.build_speakers(segs, diar_segs, WAV, embedder, threshold=0.5)
    sugg = {v["label"]: (v["suggested"], v["confidence"]) for v in speakers2.values()}
    print(f"suggestions after enrollment: {sugg}")

    ok = len(embs) >= 2 and len(voiceprints.list_people()) >= 2 and \
        any(v[0] in ("Alice", "Bob") for v in sugg.values())
    voiceprints.DB_PATH.unlink(missing_ok=True)
    print("SMOKE VOICEPRINTS:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
