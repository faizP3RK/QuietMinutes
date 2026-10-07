"""Headless smoke test for the web UI bridge (no window).

Instantiates WebController + Api and calls every read path plus a short record/stop
cycle, verifying JSON-serializability of every payload (what pywebview requires).
"""

from __future__ import annotations

import json
import time

from quietminutes.webui.api import Api
from quietminutes.webui.window import WebController, build_html


def jsonable(name, v):
    json.dumps(v)  # raises if not serializable
    return v


def main() -> int:
    html = build_html()
    assert "<style>" in html and "const app" in html and "style.css" not in html.split("</style>")[1], "inline failed"
    print(f"build_html OK ({len(html)} chars, css+js inlined)")

    c = WebController()
    api = Api(c)
    print("engine warming in background; testing read paths ...")

    for name in ["status", "preflight", "recent", "voices", "get_settings",
                 "model_status", "app_info"]:
        v = jsonable(name, getattr(api, name)())
        print(f"  {name}(): OK ({type(v).__name__})")

    # meetings: exercise speakers/transcript on the most recent real meeting
    rec = api.recent()
    if rec:
        f = rec[0]["folder"]
        jsonable("speakers", api.speakers(f))
        t = api.transcript(f)
        print(f"  speakers()/transcript(): OK (transcript {len(t)} chars)")
        sp = api.speakers(f)
        if sp["rows"] and sp["rows"][0].get("snippet"):
            uri = api.snippet(f, sp["rows"][0]["snippet"])
            print(f"  snippet(): OK ({len(uri)} chars data-uri)" if uri else "  snippet(): empty")

    # record/stop cycle (2s)
    api.start("web api smoke")
    time.sleep(2.0)
    s = jsonable("status", api.status())
    print(f"  recording status: state={s['state']} me_muted={s['me_muted']}")
    api.set_mute(True); api.set_mute(False)
    api.stop()
    for _ in range(120):
        if not c.transcribing:
            break
        time.sleep(0.5)
    print(f"  stop+finalize: OK (transcribing={c.transcribing})")

    # clean up the smoke meeting
    import shutil
    for m in api.recent():
        if "web api smoke" in m["name"]:
            shutil.rmtree(m["folder"], ignore_errors=True)
    c._really_quit = True
    c.shutdown()
    print("SMOKE WEBAPI: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
