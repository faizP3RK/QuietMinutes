"""Timestamped file logging into the project's `logs/` folder — built to never go silent.

Why this design (learned the hard way):
  * The app launches via `pythonw` (no console), so `print()` output normally vanishes.
    We redirect stdout/stderr into the log, so every print from capture / whisper /
    diarization is recorded too.
  * The old TimedRotatingFileHandler RENAMED the log at midnight. On Windows a rename
    fails if any other process has the file open (e.g. a second app instance), and after
    that failure every later write was silently dropped — logging died for months.
    Now each day simply writes to its own file (quietminutes-YYYY-MM-DD.log): no rename,
    nothing to fail. Files older than KEEP_DAYS are pruned at startup.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sys
import threading
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"  # <project>/logs
KEEP_DAYS = 30
log = logging.getLogger("quietminutes")
_configured = False


def _today_path() -> Path:
    return LOG_DIR / f"quietminutes-{_dt.date.today():%Y-%m-%d}.log"


def current_log_path() -> Path:
    return _today_path()


class DailyFileHandler(logging.Handler):
    """Append to logs/quietminutes-<date>.log; switches file when the date changes."""

    def __init__(self):
        super().__init__()
        self._date = None
        self._fh = None

    def _ensure(self):
        today = _dt.date.today()
        if today != self._date or self._fh is None:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    pass
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(_today_path(), "a", encoding="utf-8")
            self._date = today

    def emit(self, record):
        try:
            msg = self.format(record)
            with self.lock:
                self._ensure()
                self._fh.write(msg + "\n")
                self._fh.flush()
        except Exception:  # noqa: BLE001 - logging must never crash the app
            pass

    def close(self):
        try:
            if self._fh:
                self._fh.close()
        finally:
            super().close()


class _StreamToLog:
    """File-like object: lines written to it become log records (captures print())."""

    def __init__(self, level: int):
        self.level = level
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s):
        if not s:
            return 0
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.rstrip()
                if line:
                    logging.getLogger("quietminutes.console").log(self.level, line)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def _prune_old():
    cutoff = _dt.date.today() - _dt.timedelta(days=KEEP_DAYS)
    for p in LOG_DIR.glob("quietminutes*.log*"):
        try:
            if _dt.date.fromtimestamp(p.stat().st_mtime) < cutoff:
                p.unlink()
        except Exception:  # noqa: BLE001
            pass


def setup_logging() -> logging.Logger:
    global _configured
    if _configured:
        return log
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # handleError() would print to sys.stderr, which we redirect into the log below —
    # a recursion trap. Handler errors are swallowed instead.
    logging.raiseExceptions = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    fh = DailyFileHandler()
    fh.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(fh)
    orig_err = sys.__stderr__ if sys.__stderr__ is not None else None
    if orig_err is not None:  # console mirror when there is a console
        sh = logging.StreamHandler(orig_err)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.propagate = False

    # Route print()/library output into the log (otherwise lost under pythonw).
    sys.stdout = _StreamToLog(logging.INFO)
    sys.stderr = _StreamToLog(logging.WARNING)

    def _hook(exc_type, exc, tb):
        log.error("UNCAUGHT", exc_info=(exc_type, exc, tb))
    sys.excepthook = _hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = lambda a: log.error(
            "UNCAUGHT(thread %s)", getattr(a.thread, "name", "?"),
            exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    _prune_old()
    _configured = True
    log.info("=== QuietMinutes started (pid via %s); logging to %s ===",
             Path(sys.executable).name, _today_path())
    return log
