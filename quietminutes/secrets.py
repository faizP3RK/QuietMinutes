"""Windows DPAPI encryption for secrets at rest (e.g. the cloud API key).

Uses the OS CryptProtectData/CryptUnprotectData via ctypes — no dependency, NO ADMIN.
Ciphertext is tied to the current Windows user account: even if config.json is copied
to another machine or user, the key cannot be decrypted. Degrades gracefully: if DPAPI
is somehow unavailable, values pass through unchanged so the app never breaks.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt

PREFIX = "dpapi:"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


try:
    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _AVAILABLE = True
except Exception:  # noqa: BLE001 - non-Windows / no DPAPI
    _AVAILABLE = False


def _to_blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, int(blob.cbData))


def protect(text: str) -> str:
    """Return an encrypted, base64 'dpapi:...' token. Empty in -> empty out."""
    if not text or not _AVAILABLE:
        return text
    if text.startswith(PREFIX):
        return text  # already encrypted
    try:
        out = _Blob()
        src = _to_blob(text.encode("utf-8"))
        if not _crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0,
                                         ctypes.byref(out)):
            return text
        try:
            return PREFIX + base64.b64encode(_from_blob(out)).decode("ascii")
        finally:
            _kernel32.LocalFree(out.pbData)
    except Exception:  # noqa: BLE001
        return text


def unprotect(token: str) -> str:
    """Decrypt a 'dpapi:...' token; pass through anything else (legacy plaintext)."""
    if not token or not token.startswith(PREFIX) or not _AVAILABLE:
        return token
    try:
        raw = base64.b64decode(token[len(PREFIX):])
        out = _Blob()
        src = _to_blob(raw)
        if not _crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                           ctypes.byref(out)):
            return ""
        try:
            return _from_blob(out).decode("utf-8")
        finally:
            _kernel32.LocalFree(out.pbData)
    except Exception:  # noqa: BLE001
        return ""


def is_encrypted(token: str) -> bool:
    return bool(token) and token.startswith(PREFIX)
