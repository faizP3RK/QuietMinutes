"""System-tray icon with state-coloured glyph and menu.

Runs its own message loop in a background thread. All window-affecting callbacks are
marshalled onto the Tk main thread by the controller; capture controls are thread-safe.
"""

from __future__ import annotations

import pystray
from PIL import Image, ImageDraw

STATE_COLORS = {
    "idle": (136, 136, 136, 255),       # gray
    "recording": (224, 69, 69, 255),    # red
    "paused": (224, 161, 0, 255),       # amber
    "transcribing": (60, 130, 220, 255),  # blue (Phase 2)
    "error": (192, 57, 43, 255),        # dark red
}


def _icon_image(state: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 54, 54), fill=STATE_COLORS.get(state, STATE_COLORS["idle"]))
    if state == "recording":
        d.ellipse((26, 26, 38, 38), fill=(255, 255, 255, 255))
    elif state == "paused":
        d.rectangle((24, 22, 30, 42), fill=(255, 255, 255, 255))
        d.rectangle((34, 22, 40, 42), fill=(255, 255, 255, 255))
    return img


class Tray:
    def __init__(self, controller):
        self.c = controller
        self._state = "idle"
        self.icon = pystray.Icon(
            "QuietMinutes", _icon_image("idle"), "QuietMinutes — idle", menu=self._menu()
        )

    def _menu(self) -> pystray.Menu:
        idle = lambda i: self._state == "idle"  # noqa: E731
        live = lambda i: self._state in ("recording", "paused")  # noqa: E731
        return pystray.Menu(
            pystray.MenuItem("Open dashboard", lambda: self.c.show_dashboard(), default=True),
            pystray.MenuItem("Start", lambda: self.c.start_recording(), enabled=idle),
            pystray.MenuItem(
                lambda i: "Resume" if self._state == "paused" else "Pause",
                lambda: self.c.toggle_pause(), enabled=live,
            ),
            pystray.MenuItem("Stop", lambda: self.c.stop_recording(), enabled=live),
            pystray.MenuItem(
                lambda i: "Unmute me" if self.c.cap.me_muted else "Mute me",
                lambda: self.c.toggle_mute(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open last transcript", lambda: self.c.open_last_transcript()),
            pystray.MenuItem("Open output folder", lambda: self.c.open_output_folder()),
            pystray.MenuItem("Manage voices", lambda: self.c.show_voices()),
            pystray.MenuItem("Settings", lambda: self.c.show_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.c.quit()),
        )

    def set_state(self, state: str, title: str | None = None) -> None:
        if state != self._state:
            self._state = state
            try:
                self.icon.icon = _icon_image(state)
                self.icon.update_menu()
            except Exception:  # noqa: BLE001
                pass
        if title is not None:
            try:
                self.icon.title = title
            except Exception:  # noqa: BLE001
                pass

    def run(self) -> None:
        self.icon.run()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
