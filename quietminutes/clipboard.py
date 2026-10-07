"""Copy text to the Windows clipboard (ctypes, no dependency, no admin).

The web page's navigator.clipboard is blocked inside the app window (the page has no
secure origin), which is why the Copy / Notes buttons silently did nothing. Copying
from the Python side always works.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.OpenClipboard.argtypes = [wt.HWND]
_u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
_u32.SetClipboardData.restype = wt.HANDLE
_k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_k32.GlobalAlloc.restype = wt.HGLOBAL
_k32.GlobalLock.argtypes = [wt.HGLOBAL]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
_k32.GlobalFree.argtypes = [wt.HGLOBAL]


def copy_text(text: str) -> bool:
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):  # another app may hold the clipboard briefly
        if _u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(_CF_UNICODETEXT, h):
            _k32.GlobalFree(h)
            return False
        return True  # the clipboard now owns the memory
    finally:
        _u32.CloseClipboard()
