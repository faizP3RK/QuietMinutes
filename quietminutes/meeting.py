"""Per-meeting metadata + naming helpers.

Each meeting folder may carry a `meeting.json` with a human display name plus a few
stats (created time, duration, segment count). The folder itself is named
`<YYYY-MM-DD_HH-MM>[ - <safe name>]` so it sorts chronologically and is browsable in
Explorer, while the unsanitized display name lives in the meta file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

META = "meeting.json"
_ILLEGAL = re.compile(r'[<>:"/\\|?*\n\r\t]+')  # not allowed in Windows paths


def sanitize(name: str) -> str:
    name = _ILLEGAL.sub(" ", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:60]


def folder_name(ts: str, name: str) -> str:
    safe = sanitize(name)
    return f"{ts} - {safe}" if safe else ts


def ts_of(folder) -> str:
    """The leading YYYY-MM-DD_HH-MM token of a meeting folder name."""
    meta = read_meta(folder)
    if meta.get("ts"):
        return meta["ts"]
    return Path(folder).name[:16]


def read_meta(folder) -> dict:
    p = Path(folder) / META
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_meta(folder, **fields) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = read_meta(folder)
    data.update({k: v for k, v in fields.items() if v is not None})
    (folder / META).write_text(json.dumps(data, indent=2), encoding="utf-8")


def display_name(folder) -> str:
    return read_meta(folder).get("name") or Path(folder).name


def rename(folder, new_name: str) -> Path:
    """Rename a finished meeting: move the folder (keeping its timestamp prefix),
    update meta, and patch the transcript.md title. Returns the new folder path."""
    old = Path(folder)
    ts = ts_of(old)
    new = old.parent / folder_name(ts, new_name)
    if new != old:
        try:
            old.rename(new)
        except OSError as exc:
            print(f"[meeting] could not rename folder: {exc}")
            new = old
    write_meta(new, name=new_name.strip(), ts=ts)
    md = new / "transcript.md"
    if md.exists():
        try:
            lines = md.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].startswith("#"):
                lines[0] = f"# {display_name(new)}"
                md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
    return new
