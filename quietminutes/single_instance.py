"""Single-instance guard (Windows named mutex + event; per-user, no admin).

Two running copies caused real damage: duplicate global hotkeys (one keypress toggled
BOTH), competing audio captures, and a locked log file that silently killed logging.
The first instance owns a mutex and waits on a named event; a second launch just sets
that event (so the first shows its window) and exits.
"""

from __future__ import annotations

import ctypes
import threading

_MUTEX = "Local\\QuietMinutes_SingleInstance_v1"
_EVENT = "Local\\QuietMinutes_ShowWindow_v1"
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000
_INFINITE = 0xFFFFFFFF

_handles = []  # keep kernel handles alive for the process lifetime

try:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.restype = ctypes.c_void_p
    _k32.CreateEventW.restype = ctypes.c_void_p
    _k32.OpenEventW.restype = ctypes.c_void_p
    _k32.SetEvent.argtypes = [ctypes.c_void_p]
    _k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
except Exception:  # noqa: BLE001 - non-Windows: guard is a no-op
    _k32 = None


def acquire() -> bool:
    """True if we are the first instance (and now own the guard)."""
    if _k32 is None:
        return True
    h = _k32.CreateMutexW(None, False, _MUTEX)
    err = ctypes.get_last_error()
    if not h:
        return True  # could not create — fail open rather than block the user
    if err == _ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(h)
        return False
    _handles.append(h)
    return True


def signal_existing() -> bool:
    """Ask the already-running instance to show its window."""
    if _k32 is None:
        return False
    h = _k32.OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, _EVENT)
    if not h:
        return False
    try:
        return bool(_k32.SetEvent(h))
    finally:
        _k32.CloseHandle(h)


def listen_for_show(callback) -> None:
    """In the first instance: call `callback()` whenever a second launch signals."""
    if _k32 is None:
        return
    h = _k32.CreateEventW(None, False, False, _EVENT)  # auto-reset
    if not h:
        return
    _handles.append(h)

    def loop():
        while True:
            if _k32.WaitForSingleObject(h, _INFINITE) == 0:
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=loop, name="show-listener", daemon=True).start()
