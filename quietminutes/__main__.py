"""Entry point: modern web UI by default, classic Tk UI as automatic fallback.

    python -m quietminutes            -> web UI (pywebview / WebView2)
    python -m quietminutes --classic  -> old customtkinter dashboard

Only one copy may run: a second launch brings the running window forward and exits.
"""

import sys


def run() -> None:
    from . import single_instance
    if not single_instance.acquire():
        single_instance.signal_existing()
        return

    if "--classic" not in sys.argv:
        try:
            from .webui.window import main as main_web
            main_web()
            return
        except Exception as exc:  # noqa: BLE001 - never leave the user with nothing
            try:
                from .logging_setup import log
                log.exception("web UI failed to start, falling back to classic: %s", exc)
            except Exception:  # noqa: BLE001
                pass
    from .app import main as main_classic
    main_classic()


if __name__ == "__main__":
    run()
