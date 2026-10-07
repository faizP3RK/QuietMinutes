"""Glue: diarization → per-speaker transcript labels → voiceprint suggestions.

Run once at end-of-meeting. Aligns diarized clusters to the transcribed 'Others'
segments by timestamp overlap (relabelling them `Speaker 1..N`), extracts one
embedding per cluster, and matches against the voiceprint DB to *suggest* known names.
Embeddings + suggestions are persisted in the meeting folder so the rename dialog can
enroll them later (implicit enrollment), even after a restart.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from . import voiceprints
from .embeddings import SAMPLE_RATE
from .engine import _to_mono_16k

OTHERS_LABEL = "Speaker"  # placeholder the rolling transcriber gives the Others track
SNIPPET_SECONDS = 6       # per-speaker sample kept for the rename dialog


def _save_hq_snippet(others_wav: str, regions: list, out_path: Path,
                     seconds: int = SNIPPET_SECONDS) -> bool:
    """Write a FULL-QUALITY, loudness-normalized voice sample from the ORIGINAL WAV
    (native rate/channels) — not the 16 kHz mono the AI models use. Picks the longest
    clean regions (best-sounding, least cross-talk) so the sample is pleasant to audition.
    """
    try:
        info = sf.info(others_wav)
        sr = info.samplerate
        chosen, total = [], 0.0
        for start, end in sorted(regions, key=lambda r: r[1] - r[0], reverse=True):
            if end - start < 0.4:
                continue
            chosen.append((start, end))
            total += end - start
            if total >= seconds:
                break
        if not chosen:
            return False
        chosen.sort()  # chronological for a natural-sounding clip
        parts = []
        with sf.SoundFile(others_wav) as f:
            for start, end in chosen:
                f.seek(int(start * sr))
                n = int(min(end - start, seconds) * sr)
                block = f.read(n, dtype="float32", always_2d=True)
                if block.shape[0]:
                    parts.append(block)
                if sum(p.shape[0] for p in parts) >= seconds * sr:
                    break
        if not parts:
            return False
        audio = np.concatenate(parts, axis=0)[: seconds * sr]
        peak = float(np.abs(audio).max())
        if peak > 0:
            audio = audio * (0.95 / peak)  # normalize so quiet speakers are audible
        sf.write(str(out_path), audio, sr, subtype="PCM_16")
        return True
    except Exception as exc:  # noqa: BLE001 - snippet is cosmetic, never break finalize
        print(f"[snippet] {exc}")
        return False


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


MAX_EMBED_S = 120    # per-cluster audio cap for computing its voiceprint (speed)
ABSORB_FLOOR = 0.40  # tiny clusters must at least resemble an anchor to be absorbed
SURE_MERGE = 0.85    # >= this similarity: same voice for certain, always merge
# Measured on real calls: two DIFFERENT-but-similar voices reached 0.71 cosine as
# clusters grew, while genuine same-voice medium merges sit >= 0.75. A cluster with
# real speech therefore only merges when at least PROTECT_SIM similar.
PROTECT_SIM = 0.75
PROTECT_S = 8.0      # "real speech" = at least this many seconds


def merge_clusters(diar: list[dict], wav_path: str, embedder,
                   merge_threshold: float = 0.50, min_speaker_s: float = 5.0,
                   log=None) -> list[dict]:
    """Second-pass cleanup of over-split diarization — duration-aware.

    Measured on real calls: same-voice fragment pairs span 0.65..0.98 cosine while
    different-but-similar voices can reach 0.67, so similarity alone cannot decide.
    Durations break the tie: a genuine speaker accumulates speech, fragments stay
    small. Iteratively (re-embedding every cluster FROM ITS AUDIO each round):
      * absorb < min_speaker_s clusters into the most-similar anchor (if >= 0.40);
      * merge pairs >= SURE_MERGE unconditionally;
      * merge medium pairs (merge_threshold..SURE_MERGE) only if the smaller side is
        tiny (< PROTECT_S) or similarity >= PROTECT_SIM — this is what stops a real
        second speaker being swallowed by a similar-sounding main speaker;
      * finally force-fold any leftover sub-min_speaker_s scraps into the nearest
        voice so junk never becomes a 'Speaker N' row.
    Returns diar with cluster ids renumbered 0..N-1 by first appearance.
    """
    if not diar:
        return diar
    audio = _to_mono_16k(wav_path, SAMPLE_RATE)
    assign = {c: c for c in {d["speaker"] for d in diar}}
    blocked: set = set()
    # Voiceprint cache: only clusters whose membership changed are re-embedded. The
    # embedder is deterministic, so this is identical output at a fraction of the cost
    # (previously EVERY cluster was re-embedded EVERY round — minutes on long calls).
    cache: dict[int, np.ndarray] = {}
    dirty: set = set(assign.values())

    def embed_groups():
        groups: dict[int, list] = {}
        for d in diar:
            groups.setdefault(assign[d["speaker"]], []).append((d["start"], d["end"]))
        embs, durs = {}, {}
        for gid, regs in groups.items():
            durs[gid] = sum(e - s for s, e in regs)
            if gid in cache and gid not in dirty:
                embs[gid] = cache[gid]
                continue
            parts, got = [], 0.0
            for s, e in sorted(regs, key=lambda r: r[1] - r[0], reverse=True):
                a, b = int(s * SAMPLE_RATE), int(e * SAMPLE_RATE)
                if b > a:
                    parts.append(audio[a:b])
                    got += (b - a) / SAMPLE_RATE
                if got >= MAX_EMBED_S:
                    break
            if parts:
                v = embedder.embed_samples(np.concatenate(parts))
                if v is not None:
                    embs[gid] = v
                    cache[gid] = v
            dirty.discard(gid)
        return embs, durs

    def fold(drop, keep):
        for c in assign:
            if assign[c] == drop:
                assign[c] = keep
        dirty.add(keep)
        cache.pop(drop, None)

    trace = []
    for _ in range(60):  # safety cap; converges long before this
        embs, durs = embed_groups()
        if len(embs) <= 1:
            break

        def cos(a, b):
            return float(np.dot(embs[a], embs[b]))

        changed = False
        # absorb tiny clusters that resemble an anchor; dissimilar ones stay (for now)
        anchors = [g for g in embs if durs[g] >= min_speaker_s]
        if anchors:
            for g in sorted(embs):
                if g in anchors:
                    continue
                nearest = max(anchors, key=lambda a: cos(a, g))
                if cos(nearest, g) >= ABSORB_FLOOR:
                    fold(g, nearest)
                    del embs[g]
                    changed = True
        # best allowed pair
        ids = sorted(embs)
        best, best_sim = None, merge_threshold
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if (a, b) in blocked:
                    continue
                sim = cos(a, b)
                if sim < best_sim:
                    continue
                small = min(durs.get(a, 0), durs.get(b, 0))
                if sim < SURE_MERGE and small >= PROTECT_S and sim < PROTECT_SIM:
                    blocked.add((a, b))  # protected: real speaker, not similar enough
                    trace.append(("block", round(sim, 3), int(small)))
                    continue
                best, best_sim = (a, b), sim
        if best:
            a, b = best
            keep, drop = (a, b) if durs.get(a, 0) >= durs.get(b, 0) else (b, a)
            trace.append(("merge", round(best_sim, 3), int(durs.get(drop, 0))))
            fold(drop, keep)
            changed = True
        if not changed:
            break

    # final pass: force-fold surviving scraps (< min_speaker_s) into nearest voice
    embs, durs = embed_groups()
    anchors = [g for g in embs if durs[g] >= min_speaker_s]
    if anchors:
        for g in sorted(embs):
            if g in anchors:
                continue
            nearest = max(anchors, key=lambda a: float(np.dot(embs[a], embs[g])))
            trace.append(("scrap", round(float(np.dot(embs[nearest], embs[g])), 3),
                          int(durs.get(g, 0))))
            fold(g, nearest)

    if log is not None:
        log.info("merge trace: %s", trace)

    order: dict[int, int] = {}
    out = []
    for d in sorted(diar, key=lambda x: x["start"]):
        f = assign[d["speaker"]]
        if f not in order:
            order[f] = len(order)
        out.append({**d, "speaker": order[f]})
    return out


MIN_RUN_WORDS = 2     # a speaker "turn" shorter than this ...
MIN_RUN_S = 0.6       # ... and shorter than this is treated as noise and smoothed away


def _speaker_lookup(diar: list[dict]):
    """Return f(a, b) -> cluster with max overlap on [a,b] (nearest region if none)."""
    import bisect
    regs = sorted(diar, key=lambda d: d["start"])
    starts = [d["start"] for d in regs]
    max_len = max((d["end"] - d["start"] for d in regs), default=0.0)

    def at(a: float, b: float):
        if not regs:
            return None
        i = bisect.bisect_left(starts, b)
        best, best_ov = None, 0.0
        j = i - 1
        while j >= 0 and regs[j]["start"] >= a - max_len:  # only regions that could overlap
            ov = _overlap(a, b, regs[j]["start"], regs[j]["end"])
            if ov > best_ov:
                best, best_ov = regs[j]["speaker"], ov
            j -= 1
        if best is not None:
            return best
        mid = (a + b) / 2  # no overlap: nearest region in time
        cand = [regs[k] for k in (i - 1, i) if 0 <= k < len(regs)]
        return min(cand, key=lambda d: min(abs(mid - d["start"]), abs(mid - d["end"])))["speaker"]

    return at


def _smooth(runs: list[list]) -> list[list]:
    """Fold tiny speaker runs (a stray word) into the bigger neighbouring run.
    Always absorbs the SHORTEST blip first, into whichever neighbour holds more speech,
    so a one-word misattribution can never take over the line."""
    def dur(r):
        return r[1][-1][1] - r[1][0][0]

    def merge_equal():
        k = 0
        while k < len(runs) - 1:
            if runs[k][0] == runs[k + 1][0]:
                runs[k][1].extend(runs[k + 1][1])
                del runs[k + 1]
            else:
                k += 1

    while len(runs) > 1:
        tiny = [i for i, r in enumerate(runs)
                if len(r[1]) < MIN_RUN_WORDS and dur(r) < MIN_RUN_S]
        if not tiny:
            break
        i = min(tiny, key=lambda j: dur(runs[j]))
        tgt = max((j for j in (i - 1, i + 1) if 0 <= j < len(runs)), key=lambda j: dur(runs[j]))
        if tgt < i:
            runs[tgt][1].extend(runs[i][1])
        else:
            runs[tgt][1][:0] = runs[i][1]
        del runs[i]
        merge_equal()
    return runs


def label_segments(segments: list[dict], diar: list[dict]) -> list[dict]:
    """Assign 'Others' speech to diarized speakers WORD BY WORD, splitting a transcript
    line wherever the speaker changes (an answer no longer inherits the asker's label).
    Lines without word timing fall back to whole-line overlap. Updates `segments` in
    place (callers rely on that) and returns it."""
    at = _speaker_lookup(diar)
    out: list[dict] = []
    for seg in segments:
        if seg["speaker"] != OTHERS_LABEL:
            seg["cluster"] = None
            out.append(seg)
            continue
        words = seg.get("words")
        if not words:
            c = at(seg["start"], seg["end"])
            seg["cluster"] = c
            seg["speaker"] = f"Speaker {c + 1}" if c is not None else OTHERS_LABEL
            out.append(seg)
            continue
        runs: list[list] = []
        for w in words:
            c = at(w[0], w[1])
            if runs and runs[-1][0] == c:
                runs[-1][1].append(w)
            else:
                runs.append([c, [w]])
        for c, ws in _smooth(runs):
            text = "".join(t for _, _, t in ws).strip()
            if not text:
                continue
            out.append({"start": ws[0][0], "end": ws[-1][1], "text": text, "cluster": c,
                        "speaker": f"Speaker {c + 1}" if c is not None else OTHERS_LABEL,
                        "words": ws})
    segments[:] = out
    return segments


def build_speakers(segments, diar, others_wav, embedder, threshold, folder=None) -> tuple[dict, dict]:
    """For each cluster present in the transcript, compute one embedding, match the
    voiceprint DB for a suggested name, and (if folder given) save a tiny snippet WAV.
    Returns (speakers_meta, embeddings) keyed by cluster id (as str). Loads audio once."""
    regions: dict[int, list] = {}
    for d in diar:
        regions.setdefault(d["speaker"], []).append((d["start"], d["end"]))

    by_cluster: dict[int, list] = {}
    for seg in segments:
        c = seg.get("cluster")
        if c is not None:
            by_cluster.setdefault(c, []).append(seg)

    audio = _to_mono_16k(others_wav, SAMPLE_RATE)
    speakers, embs = {}, {}
    for c, segs in sorted(by_cluster.items()):
        parts = []
        for start, end in regions.get(c, []):
            a, b = int(start * SAMPLE_RATE), int(end * SAMPLE_RATE)
            if b > a:
                parts.append(audio[a:b])
        if not parts:
            continue
        samples = np.concatenate(parts)
        emb = embedder.embed_samples(samples)
        name, score = voiceprints.match(emb, threshold) if emb is not None else (None, 0.0)

        snippet = None
        if folder is not None:
            name = f"speaker_{c}.wav"
            if _save_hq_snippet(others_wav, regions.get(c, []), Path(folder) / name):
                snippet = name
            else:  # fallback to the 16 kHz sample if the HQ read failed
                sf.write(str(Path(folder) / name), samples[:SNIPPET_SECONDS * SAMPLE_RATE],
                         SAMPLE_RATE, subtype="PCM_16")
                snippet = name

        speakers[str(c)] = {
            "label": f"Speaker {c + 1}",
            "sample": max(segs, key=lambda s: len(s["text"]))["text"][:140],
            "suggested": name,
            "confidence": round(float(score), 3),
            "snippet": snippet,
        }
        if emb is not None:
            embs[str(c)] = emb
    return speakers, embs


def save_speakers(folder, speakers: dict, embs: dict) -> None:
    folder = Path(folder)
    (folder / "speakers.json").write_text(json.dumps(speakers, indent=2), encoding="utf-8")
    if embs:
        np.savez(folder / "embeddings.npz", **embs)


def load_speakers(folder) -> tuple[dict, dict]:
    folder = Path(folder)
    sp_p = folder / "speakers.json"
    speakers = json.loads(sp_p.read_text(encoding="utf-8")) if sp_p.exists() else {}
    emb_p = folder / "embeddings.npz"
    embs = {k: np.asarray(v, dtype=np.float32) for k, v in np.load(emb_p).items()} \
        if emb_p.exists() else {}
    return speakers, embs


import re

_PLACEHOLDER = re.compile(r"^Speaker \d+$")


def apply_names(folder, mapping: dict) -> dict:
    """Apply `Speaker N` -> name edits: enroll each named cluster's embedding
    (implicit enrollment), relabel the saved segments, and re-render the transcript.
    Merging is just naming: setting 'Speaker 5' to 'Speaker 2' (or two rows to the
    same name) folds them together everywhere. Chains resolve transitively, and
    placeholder 'Speaker N' names are never enrolled as voiceprints.
    Returns the {old_label: final_name} map that was actually applied."""
    from .. import meeting
    from ..transcribe.writer import write_outputs

    folder = Path(folder)
    seg_p = folder / "segments.json"
    if not seg_p.exists():
        return {}
    segs = json.loads(seg_p.read_text(encoding="utf-8"))
    speakers, embs = load_speakers(folder)
    label_to_cid = {v["label"]: cid for cid, v in speakers.items()}

    clean = {}
    for label, name in mapping.items():
        name = (name or "").strip()
        if name and name != label:
            clean[label] = name

    def resolve(name):  # follow Speaker-label chains to the final name
        seen = set()
        while name in clean and name not in seen:
            seen.add(name)
            name = clean[name]
        return name

    relabel = {}
    for label in clean:
        final = resolve(label)
        if final == label:
            continue
        relabel[label] = final
        if not _PLACEHOLDER.match(final):  # real names only in the voiceprint DB
            cid = label_to_cid.get(label)
            if cid is not None and cid in embs:
                voiceprints.add_embedding(final, embs[cid])
    if not relabel:
        return {}

    for s in segs:
        if s["speaker"] in relabel:
            s["speaker"] = relabel[s["speaker"]]
    seg_p.write_text(json.dumps(segs, indent=2), encoding="utf-8")
    write_outputs(folder, segs, title=meeting.display_name(folder))
    for label, name in relabel.items():
        cid = label_to_cid.get(label)
        if cid in speakers:
            speakers[cid]["named"] = name
    save_speakers(folder, speakers, embs)
    return relabel
