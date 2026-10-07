"""Headless construct/wiring test for the Phase 1 UI (window stays withdrawn).

Builds the dashboard + tray + controller, drives the idle pre-flight render and the
live recording render through several update cycles, then tears down. Proves the UI
constructs and both poll branches run without error. Run:

    .venv\\Scripts\\python.exe -u smoke_ui.py
"""

from __future__ import annotations

import os
import time

from quietminutes.app import Controller
from quietminutes.ui.dashboard import Dashboard
from quietminutes.ui.tray import Tray


def pump(dash, n=6, dt=0.05):
    for _ in range(n):
        dash._update()
        dash.update()
        time.sleep(dt)


def main() -> int:
    c = Controller()
    c.cfg.output_folder = os.path.join(os.path.dirname(__file__), "phase1_smoke_out")

    dash = Dashboard(c)
    dash.withdraw()  # never show during the test
    tray = Tray(c)   # construct only; do not run the message loop
    c.attach(tray, dash)

    print("waiting for engine ...", "ready" if c.engine.ready.wait(timeout=90) else "TIMEOUT")

    # --- idle / pre-flight branch ---
    pump(dash)
    pf = c.preflight()
    print(f"idle OK  | Others='{pf['others_device'][:40]}'  "
          f"profile={pf['profile'].status if pf['profile'] else None}  "
          f"mode={pf['mode']}  free={pf['free_gb']:.1f}GB")

    # --- recording branch ---
    c.start_recording()
    pump(dash, n=12)
    s = c.cap.status()
    print(f"rec  OK  | state={s['state']}  others_meter={s['others_meter']:.2f}  "
          f"me_meter={s['me_meter']:.2f}  degraded={s['degraded']}")

    # --- mute branch ---
    c.set_mute(True)
    pump(dash, n=4)
    ms = c.cap.status()
    print(f"mute OK  | me_muted={ms['me_muted']}  switch={dash.mute_var.get()}  "
          f"me_meter={ms['me_meter']:.2f}")
    c.set_mute(False)
    pump(dash, n=2)

    # --- pause branch ---
    c.toggle_pause()
    pump(dash, n=4)
    print(f"pause OK | state={c.cap.status()['state']}")
    c.toggle_pause()
    pump(dash, n=4)

    # --- view switching ---
    for view in ("recent", "voices", "settings", "record"):
        dash.show_view(view); dash.update()
    print("views OK | switched recent/voices/settings/record")

    # --- Phase 3 dialogs construct (use a finalize-test meeting folder if present) ---
    import glob
    from quietminutes.ui.speakers import RenameSpeakersDialog, TranscriptViewer
    fins = sorted(glob.glob("phase3_finalize_out/*/"))
    if fins:
        tv = TranscriptViewer(dash, c, fins[0]); tv.update(); tv.destroy()
        rd = RenameSpeakersDialog(dash, c, fins[0]); rd.update(); rd.destroy()
        print("dialogs OK | TranscriptViewer + RenameSpeakersDialog construct")
    else:
        print("dialogs SKIP | no phase3_finalize_out meeting (run smoke_finalize first)")

    c.stop_recording()
    # wait for background transcription to finish (drives the 'transcribing' UI state)
    saw_transcribing = False
    for _ in range(200):
        pump(dash, n=1)
        if c.transcribing:
            saw_transcribing = True
        if not c.transcribing and saw_transcribing:
            break
    print(f"stop OK  | state={c.cap.state}  saw_transcribing={saw_transcribing}  "
          f"transcript={'yes' if c.last_transcript else 'none'}  recent={len(c.recent_meetings())}")

    dash.destroy()
    c.shutdown()
    print("SMOKE UI: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
