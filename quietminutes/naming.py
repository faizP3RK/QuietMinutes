"""LLM speaker-name inference from transcript context (opt-in, on-demand).

Reads the finished transcript and, from how people address each other ("Thanks, Mike",
"Sarah, can you…"), proposes a real name for each unlabeled `Speaker N`. Suggestions
ONLY — never auto-applied; they surface in the rename panel next to voiceprint matches.
Text only; obeys the same cloud-consent gate as notes (see notes.backend.chat).
"""

from __future__ import annotations

import json
import re

from .logging_setup import log
from .notes import backend

MAX_CHARS = 16000

_PROMPT = """You are given a meeting transcript. Speakers are labelled "Speaker 1",
"Speaker 2", etc. Using ONLY how people address each other in the dialogue (e.g.
"Thanks, Mike", "Sarah, could you…", "This is Priya"), infer each speaker's real first
name. Rules:
- Guess a name ONLY when the transcript makes it clear; otherwise use null.
- Never invent names that don't appear in the text.
- Return STRICT JSON only, mapping each label to a name or null. No prose, no code fences.

Labels present: {labels}
{attendees}
Transcript:
---
{transcript}
---
JSON:"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def infer_names(transcript: str, labels: list[str], cfg,
                attendees: list[str] | None = None) -> tuple[dict, str]:
    """Return ({label: name}, status). Only labels with a confident name are included.
    With an attendee list, guesses are restricted to those people (far more accurate)."""
    if not transcript.strip() or not labels:
        return {}, "nothing to infer"
    att = ""
    if attendees:
        att = ("Attendees (the speakers are among these people; choose names ONLY from "
               "this list): " + ", ".join(attendees) + "\n")
    prompt = _PROMPT.format(labels=", ".join(labels), transcript=transcript[:MAX_CHARS],
                            attendees=att)
    out, status = backend.chat(prompt, cfg, timeout=300)  # on-demand; user is waiting
    if out is None:
        return {}, status
    data = _extract_json(out)
    names = {}
    allowed = {a.strip().lower(): a.strip() for a in (attendees or [])}
    for label in labels:
        v = data.get(label)
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ("null", "none", "unknown"):
            v = v.strip()
            if allowed:  # snap to the attendee spelling; drop names not on the list
                hit = allowed.get(v.lower()) or next(
                    (full for low, full in allowed.items() if low.split()[0] == v.lower().split()[0]), None)
                if not hit:
                    continue
                v = hit
            names[label] = v
    log.info("LLM name inference: %s", names or "(none)")
    return names, "ok"
