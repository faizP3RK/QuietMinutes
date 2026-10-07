"""pywebview window host: wires Controller + Api + tray + hotkey together.

Threading model:
  * webview.start() owns the MAIN thread (like Tk's mainloop did).
  * The tray runs its own loop in a daemon thread (unchanged).
  * A small tray-sync thread mirrors capture state onto the tray icon/title.
Closing the window hides it to the tray; Quit (tray or UI) destroys it for real.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ..app import Controller
from ..logging_setup import log, setup_logging
from ..ui.hotkey import HotkeyManager
from ..ui.tray import Tray
from .api import Api

STATIC = Path(__file__).resolve().parent / "static"


def build_html() -> str:
    """Compose index.html with CSS/JS inlined.

    We pass a single self-contained HTML string to pywebview instead of a file URL:
    the project folder contains '&', which breaks relative file:// resource
    resolution — inlining removes every relative fetch.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="style.css">',
                        f"<style>\n{css}\n</style>")
    html = html.replace('<script src="app.js"></script>',
                        f"<script>\n{js}\n</script>")
    return html


class WebController(Controller):
    """Controller with the Tk-window plumbing rerouted to the webview window."""

    def __init__(self):
        super().__init__()
        self.window = None
        self._really_quit = False

    # ---- window/navigation plumbing (called from tray or engine threads) ----
    def _js(self, script: str) -> None:
        try:
            if self.window is not None:
                self.window.evaluate_js(script)
        except Exception as exc:  # noqa: BLE001
            log.debug("evaluate_js failed: %s", exc)

    def _show_window(self) -> None:
        try:
            if self.window is not None:
                self.window.show()
                self.window.restore()
        except Exception as exc:  # noqa: BLE001
            log.debug("window show failed: %s", exc)

    def show_dashboard(self) -> None:
        self._show_window()

    def show_settings(self) -> None:
        self._show_window()
        self._js("app.nav('settings')")

    def show_voices(self) -> None:
        self._show_window()
        self._js("app.nav('voices')")

    def open_viewer(self, folder) -> None:
        self._show_window()
        self._js(f"app.openMeeting({str(folder)!r})")

    def open_speaker_rename(self, folder, on_done=None) -> None:
        self.open_viewer(folder)

    def open_last_transcript(self) -> None:
        if self.last_transcript and Path(self.last_transcript).exists():
            self.open_viewer(str(Path(self.last_transcript).parent))
        else:
            self._notify("No transcript yet", "Record and stop a meeting first.")

    def _error(self, msg: str) -> None:
        log.error("%s", msg)
        if self.tray:
            self.tray.set_state("error", "QuietMinutes — error")
        safe = msg.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        self._js(f"app.toast('{safe}', 'error')")

    def quit(self) -> None:
        self._really_quit = True
        try:
            if self.cap.state != "idle":
                self.cap.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.tray:
            self.tray.stop()
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:  # noqa: BLE001
            pass


def _tray_sync(controller: WebController) -> None:
    """Mirror app state onto the tray icon/title (the Tk poll used to do this)."""
    last = None
    while not controller._really_quit:
        try:
            if controller.cap.state != "idle":
                s = controller.cap.status()
                el = int(s["elapsed"])
                state = s["state"]
                title = f"QuietMinutes — {state} {el // 3600:02d}:{(el % 3600) // 60:02d}:{el % 60:02d}"
            elif controller.transcribing:
                state, title = "transcribing", "QuietMinutes — transcribing…"
            else:
                state, title = "idle", "QuietMinutes — idle"
            if (state, title) != last:
                controller.tray.set_state(state, title)
                last = (state, title)
            elif state == "recording":  # keep the elapsed in the tooltip fresh
                controller.tray.set_state(state, title)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)


def main() -> None:
    import webview

    setup_logging()
    controller = WebController()
    api = Api(controller)
    tray = Tray(controller)
    controller.attach(tray, None)

    threading.Thread(target=tray.run, daemon=True).start()
    from .. import single_instance
    single_instance.listen_for_show(controller.show_dashboard)
    controller.hotkeys = HotkeyManager(controller.toggle_record)
    controller.hotkeys.start(controller.cfg.hotkey)
    threading.Thread(target=_tray_sync, args=(controller,), daemon=True).start()

    window = webview.create_window(
        "QuietMinutes",
        html=build_html(),
        js_api=api,
        width=1120, height=780, min_size=(900, 640),
        background_color="#0F1115",
    )
    controller.window = window

    def on_closing():
        if controller._really_quit:
            return True
        window.hide()  # minimize to tray instead of exiting
        return False

    window.events.closing += on_closing

    def on_loaded():
        if controller.needs_setup():
            controller._js("app.nav('settings'); app.toast('Download the Whisper model to enable transcription', 'warn')")

    window.events.loaded += on_loaded

    log.info("starting web UI (pywebview / WebView2)")
    try:
        webview.start(debug=False)
    finally:
        controller.shutdown()
