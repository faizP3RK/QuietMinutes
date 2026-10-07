"""Smoke test: meeting naming, meta.json, rename, and hotkey manager construction."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pyaudiowpatch as pyaudio

from quietminutes import meeting
from quietminutes.audio.capture import DualCapture
from quietminutes.config import Config
from quietminutes.ui.hotkey import HotkeyManager

OUT = Path("phase_naming_out")


def main() -> int:
    ok = True

    # --- sanitize / folder_name ---
    assert meeting.folder_name("2026-06-18_15-04", "Acme: Weekly/Sync?") \
        == "2026-06-18_15-04 - Acme Weekly Sync", meeting.folder_name("x", "Acme: Weekly/Sync?")
    assert meeting.folder_name("2026-06-18_15-04", "") == "2026-06-18_15-04"
    print("naming helpers OK")

    # --- named recording writes meta + folder ---
    cfg = Config.load()
    cfg.output_folder = str(OUT)
    p = pyaudio.PyAudio()
    cap = DualCapture(p, cfg)  # no engine -> capture-only
    folder = cap.start("Acme: Weekly/Sync?")
    time.sleep(0.8)
    res = cap.stop()
    p.terminate()
    base = os.path.basename(res["folder"])
    meta = meeting.read_meta(res["folder"])
    print(f"folder='{base}'  meta_name='{meta.get('name')}'  ts='{meta.get('ts')}'")
    ok &= base.endswith(" - Acme Weekly Sync") and meta.get("name") == "Acme: Weekly/Sync?"

    # --- rename ---
    new = meeting.rename(res["folder"], "Renamed Meeting")
    print(f"renamed -> '{new.name}'  exists={new.exists()}  "
          f"old_gone={not Path(res['folder']).exists()}")
    ok &= new.name.endswith(" - Renamed Meeting") and new.exists()
    ok &= meeting.read_meta(new).get("name") == "Renamed Meeting"

    # --- hotkey manager constructs + registers a combo ---
    fired = {"n": 0}
    hk = HotkeyManager(lambda: fired.__setitem__("n", fired["n"] + 1))
    registered = hk.start("<ctrl>+<alt>+r")
    hk.stop()
    print(f"hotkey registered={registered}")
    ok &= registered

    print("SMOKE NAMING:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
