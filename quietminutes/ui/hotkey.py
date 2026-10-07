"""Global (system-wide) hotkey to start/stop recording without focusing the app.

Wraps pynput's GlobalHotKeys. Defensive: an invalid combo is logged and ignored rather
than crashing the app. The callback runs on the listener thread; it only calls into
thread-safe controller methods.
"""

from __future__ import annotations


class HotkeyManager:
    def __init__(self, on_toggle):
        self._on_toggle = on_toggle
        self._listener = None
        self.active_combo: str | None = None

    def start(self, combo: str) -> bool:
        self.stop()
        if not combo:
            return False
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({combo: self._fire})
            self._listener.start()
            self.active_combo = combo
            print(f"[hotkey] registered '{combo}' (toggle start/stop)")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey] could not register '{combo}': {exc}")
            self._listener = None
            self.active_combo = None
            return False

    def _fire(self) -> None:
        try:
            self._on_toggle()
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey] toggle error: {exc}")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
            self.active_combo = None
