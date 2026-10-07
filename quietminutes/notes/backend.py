"""Pluggable notes backend — summary / action items / Minutes-of-Meeting from a transcript.

**Text transcript only — raw audio NEVER leaves the machine.** Two backends:
  * local  — Ollama HTTP (offline, free, no admin) — default.
  * gemini — Google Gemini Flash-Lite via REST (urllib, no extra dependency). Cloud, so
             it is gated behind an explicit `cloud_allowed` toggle and an API key.

Everything degrades gracefully: any failure returns (None, reason) and never raises, so a
notes problem can never block the transcript.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

MAX_CHARS = 24000  # ~ plenty for a 1-hour meeting; keeps us well within token budgets

PROMPT = """You write concise, accurate Minutes of Meeting from a raw transcript.
Use GitHub-flavored Markdown with EXACTLY these sections (omit a section's bullets if it
has no content, but keep the heading):

## Summary
3–6 sentences capturing the meeting's purpose and outcomes.

## Action items
- [ ] <owner if clearly identifiable>: <task>

## Decisions
- <decision made>

## Open questions
- <unresolved question>

Do not invent facts. Speaker labels like "Speaker 3" mean the name is unknown.
Start your answer directly with "## Summary". Do not add a title, a date, code fences
or any text outside the four sections. Expand an acronym only if the transcript does.

Transcript:
---
{transcript}
---
"""


def build_prompt(transcript: str) -> str:
    return PROMPT.format(transcript=transcript[:MAX_CHARS])


# ---- long-meeting support (map-reduce) --------------------------------------
# Local models have small context windows (Ollama defaults to 2-4K tokens and silently
# TRUNCATES overflow), so a 1-hour transcript must be summarized in pieces, then merged.
LOCAL_CHUNK_CHARS = 10000     # ~2.5K tokens per piece (fits an 8K context with headroom)
LOCAL_NUM_CTX = 8192
CLOUD_CHUNK_CHARS = 300000    # Gemini's context is huge; only split truly giant transcripts

MAP_PROMPT = """This is part {i} of {n} of a meeting transcript. Extract, as terse bullets:
### Key points
### Decisions
### Action items (owner if clearly identifiable: task)
### Open questions
Only facts stated in this part. No preamble.

Transcript part {i}/{n}:
---
{chunk}
---
"""

REDUCE_PROMPT = """Below are notes extracted from consecutive parts of ONE meeting.
Merge them into final Minutes of Meeting in GitHub-flavored Markdown with EXACTLY these
sections (keep a heading even if empty):

## Summary
3-6 sentences on the meeting's purpose and outcomes.

## Action items
- [ ] <owner if identifiable>: <task>

## Decisions
- <decision>

## Open questions
- <question>

De-duplicate, keep only what the notes support, do not invent facts. Speaker labels like
"Speaker 3" mean the name is unknown. Start your answer directly with "## Summary"; no
title, no date, no code fences, nothing outside the four sections.

Notes from the parts:
---
{notes}
---
"""


def split_transcript(text: str, max_chars: int) -> list[str]:
    """Split on line boundaries into pieces of at most ~max_chars."""
    chunks, cur, size = [], [], 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > max_chars and cur:
            chunks.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def _ollama_base(cfg) -> str:
    url = getattr(cfg, "ollama_url", "http://localhost:11434/api/generate")
    return url.split("/api/")[0]


def ollama_alive(cfg, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(_ollama_base(cfg) + "/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_ollama(cfg, wait: float = 30.0) -> bool:
    """Start the per-user Ollama server if it isn't running (no admin, no window)."""
    if ollama_alive(cfg):
        return True
    import os
    import shutil
    import subprocess
    exe = shutil.which("ollama") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    if not exe or not os.path.exists(exe):
        return False
    try:
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=0x08000000 | 0x00000008)  # NO_WINDOW | DETACHED
    except Exception:  # noqa: BLE001
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ollama_alive(cfg, timeout=2.0):
            return True
        time.sleep(1.0)
    return False


def _opener(proxy: str):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()  # default opener honors any system proxy


def _err_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""


def api_message(body: str) -> str:
    try:
        return json.loads(body).get("error", {}).get("message", "")[:300]
    except Exception:  # noqa: BLE001
        return (body or "")[:200]


def _retry_delay(body: str) -> float | None:
    try:
        for det in json.loads(body).get("error", {}).get("details", []):
            rd = det.get("retryDelay", "")
            if isinstance(rd, str) and rd.endswith("s"):
                return float(rd[:-1])
    except Exception:  # noqa: BLE001
        pass
    return None


def _post_json(url: str, payload: dict, timeout: int = 45, headers: dict | None = None,
               retries: int = 2, proxy: str = "") -> dict:
    """POST JSON. Retries genuine timeouts and 429/500/503 (with the server's retryDelay
    when present). Other HTTP errors raise immediately with the response body attached."""
    data = json.dumps(payload).encode("utf-8")
    opener = _opener(proxy)
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data, headers=headers or {"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = _err_body(exc)
            if exc.code in (429, 500, 503) and attempt < retries:
                time.sleep(min((_retry_delay(body) or 2.0 * (attempt + 1)), 25))
                continue
            exc.ms_body = body  # surface to the caller for a clear message
            raise
        except urllib.error.URLError as exc:
            is_timeout = isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower()
            if attempt < retries and is_timeout:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise


def gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def gemini_headers(api_key: str) -> dict:
    # key travels in a header, not the URL, so it can't leak into proxy/access logs
    return {"Content-Type": "application/json", "x-goog-api-key": api_key}


def gemini_chat(prompt: str, api_key: str, model: str, proxy: str = "") -> str:
    """One-shot completion via Gemini REST. Sends TEXT ONLY."""
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = _post_json(gemini_url(model), payload, headers=gemini_headers(api_key), proxy=proxy)
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()


def ollama_chat(prompt: str, model: str, url: str, proxy: str = "", keep_alive: str = "2m",
                timeout: int = 180) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": keep_alive,
               # raise the context window so a whole chunk is actually seen (Ollama's
               # default silently truncates long prompts)
               "options": {"num_ctx": LOCAL_NUM_CTX}}
    resp = _post_json(url, payload, timeout=timeout, proxy=proxy)
    return resp["response"].strip()


def chat(prompt: str, cfg, timeout: int = 180) -> tuple[str | None, str]:
    """Generic LLM completion used by BOTH notes and speaker-name inference.
    Returns (text, status); never raises. Enforces the cloud-consent gate for Gemini."""
    backend = getattr(cfg, "notes_backend", "local")
    proxy = getattr(cfg, "https_proxy", "")
    try:
        if backend == "gemini":
            if not cfg.cloud_allowed:
                return None, "Cloud sending is OFF — enable 'I allow text to the cloud' in Settings."
            if not cfg.gemini_api_key.strip():
                return None, "No Gemini API key set in Settings."
            out = gemini_chat(prompt, cfg.gemini_api_key.strip(), cfg.gemini_model, proxy)
        else:
            out = ollama_chat(prompt, cfg.ollama_model, cfg.ollama_url, proxy,
                              getattr(cfg, "ollama_keep_alive", "2m"), timeout)
        return (out, "ok") if out else (None, "empty response")
    except urllib.error.HTTPError as exc:
        body = getattr(exc, "ms_body", "")
        msg = api_message(body)
        if exc.code == 429:
            return None, ("Gemini rate-limit / quota (HTTP 429) — the key is valid but throttled. "
                          "Free-tier limits are low: wait ~1 min and retry, enable billing on a "
                          "paid (no-train) project, or switch backend to 'local'."
                          + (f" [{msg}]" if msg else ""))
        if exc.code in (400, 403) and "API_KEY" in (body or "").upper():
            return None, "Gemini: API key problem — re-check the key in Settings."
        return None, f"{backend}: HTTP {exc.code} {exc.reason}" + (f" — {msg}" if msg else "")
    except urllib.error.URLError as exc:
        hint = " (is Ollama running?)" if backend == "local" else " — check network/VPN"
        return None, f"{backend}: unreachable{hint} ({exc.reason})"
    except Exception as exc:  # noqa: BLE001
        return None, f"{backend}: {exc}"


def generate(transcript: str, cfg, progress=None) -> tuple[str | None, str]:
    """Minutes-of-Meeting generation. Short transcripts: one call. Long ones: map-reduce
    (summarize each context-sized piece, then merge) so nothing is silently truncated."""
    local = getattr(cfg, "notes_backend", "local") != "gemini"
    limit = LOCAL_CHUNK_CHARS if local else CLOUD_CHUNK_CHARS
    if len(transcript) <= limit:
        return chat(PROMPT.format(transcript=transcript), cfg, timeout=300)

    pieces = split_transcript(transcript, limit)
    partial = []
    for i, piece in enumerate(pieces, 1):
        out, status = chat(MAP_PROMPT.format(i=i, n=len(pieces), chunk=piece), cfg, timeout=300)
        if out is None:
            return None, f"part {i}/{len(pieces)}: {status}"
        partial.append(f"[Part {i}]\n{out}")
        if progress:
            progress(i / (len(pieces) + 1))
    notes = "\n\n".join(partial)
    return chat(REDUCE_PROMPT.format(notes=notes[: max(limit * 2, 20000)]), cfg, timeout=300)


def test_connection(cfg) -> tuple[bool, str]:
    """Quick reachability/key check the user can run from Settings. Never raises."""
    backend = getattr(cfg, "notes_backend", "local")
    proxy = getattr(cfg, "https_proxy", "")
    try:
        if backend == "gemini":
            if not cfg.cloud_allowed:
                return False, "Cloud sending is OFF — enable 'I allow text to the cloud' first."
            if not cfg.gemini_api_key.strip():
                return False, "No API key set."
            _post_json(gemini_url(cfg.gemini_model),
                       {"contents": [{"parts": [{"text": "ping"}]}]}, timeout=20,
                       headers=gemini_headers(cfg.gemini_api_key.strip()), proxy=proxy)
            return True, f"Connected — {cfg.gemini_model} OK."
        _post_json(cfg.ollama_url, {"model": cfg.ollama_model, "prompt": "ping", "stream": False},
                   timeout=20, proxy=proxy)
        return True, f"Ollama OK — {cfg.ollama_model}."
    except urllib.error.HTTPError as exc:
        detail = getattr(exc, "ms_body", "") or _err_body(exc)  # _post_json already read it
        if exc.code in (400, 403) and "API_KEY_INVALID" in detail:
            return False, "API key not valid."
        if exc.code == 429:
            return False, ("Reached Gemini, key is VALID, but rate-limited (HTTP 429). "
                           "Wait ~1 min or use a billing-enabled (no-train) project.")
        if exc.code == 404:
            return False, f"Model '{cfg.gemini_model}' not found — try a different model id."
        return False, f"HTTP {exc.code} {exc.reason} — {api_message(detail)}"
    except urllib.error.URLError as exc:
        hint = " (is Ollama running?)" if backend == "local" else " — check network/VPN"
        return False, f"Unreachable{hint} ({exc.reason})"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def clean_notes(md: str) -> str:
    """Strip wrappers small models add: code fences, reasoning blocks, preambles/titles."""
    import re
    md = re.sub(r"<think>.*?</think>", "", md, flags=re.S)
    md = re.sub(r"^\s*```[a-zA-Z]*\s*\n", "", md)
    md = re.sub(r"\n```\s*$", "", md.rstrip())
    i = md.find("## Summary")
    if i > 0:
        md = md[i:]
    return md.strip() + "\n"


def write_notes(folder, transcript: str, cfg) -> tuple[str | None, str]:
    md, status = generate(transcript, cfg)
    if md:
        md = clean_notes(md)
        (Path(folder) / "notes.md").write_text(md, encoding="utf-8")
    return md, status
