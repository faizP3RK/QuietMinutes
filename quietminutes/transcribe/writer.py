"""Render merged segments to transcript.txt / .srt / .md and clean up raw audio."""

from __future__ import annotations

from pathlib import Path


def _ts(seconds: float, srt: bool = False) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if srt:
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_outputs(folder: Path, segments: list[dict], title: str | None = None) -> dict:
    """segments: [{start, end, text, speaker}] sorted by start. Writes three files."""
    folder = Path(folder)
    txt = folder / "transcript.txt"
    srt = folder / "transcript.srt"
    md = folder / "transcript.md"

    # transcript.txt — one line per utterance
    with txt.open("w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"[{_ts(seg['start'])}] {seg['speaker']}: {seg['text']}\n")

    # transcript.srt
    with srt.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{_ts(seg['start'], True)} --> {_ts(seg['end'], True)}\n")
            f.write(f"{seg['speaker']}: {seg['text']}\n\n")

    # transcript.md
    with md.open("w", encoding="utf-8") as f:
        f.write(f"# {title or folder.name}\n\n")
        for seg in segments:
            f.write(f"**[{_ts(seg['start'])}] {seg['speaker']}:** {seg['text']}\n\n")

    return {"txt": str(txt), "srt": str(srt), "md": str(md), "segments": len(segments)}


def delete_raw_audio(folder: Path) -> list[str]:
    """Delete per-track WAVs after a successful transcript write."""
    removed = []
    for name in ("others.wav", "me.wav"):
        p = Path(folder) / name
        try:
            if p.exists():
                p.unlink()
                removed.append(name)
        except OSError as exc:
            print(f"[writer] could not delete {p}: {exc}")
    return removed
