"""JS <-> Python bridge for the web UI.

Every public method is callable from JS as `window.pywebview.api.<name>(...)` and must
accept/return JSON-serializable values only. Long-running work (notes, model download)
runs on controller threads; JS polls the matching *_status method.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from .. import meeting
from ..logging_setup import log


def _profile_dict(p) -> dict | None:
    if p is None:
        return None
    return {"status": p.status, "label": p.label, "color": p.color,
            "warning": p.is_warning}


class Api:
    def __init__(self, controller):
        self._c = controller
        self._notes: dict[str, dict] = {}     # folder -> {running, ok, msg}
        self._suggest: dict[str, dict] = {}   # folder -> {running, status, found}
        self._model = {"running": False, "ok": None}

    # ---------- live status (polled ~4x/s) ----------
    def status(self) -> dict:
        c = self._c
        if c.cap.state != "idle":
            s = c.cap.status()
            return {
                "state": s["state"], "elapsed": s["elapsed"],
                "others_meter": s["others_meter"], "me_meter": s["me_meter"],
                "others_silent": s["others_silent"], "me_silent": s["me_silent"],
                "me_muted": s["me_muted"], "transcribing": False,
                "others_device": s["others_device"], "me_device": s["me_device"],
                "profile": _profile_dict(s["profile"]), "degraded": s["degraded"],
                "job": c.job_info(), "engine_ready": c.engine.is_ready(),
            }
        return {"state": "transcribing" if c.transcribing else "idle",
                "job": c.job_info(), "engine_ready": c.engine.is_ready(),
                "notes_running": len(c._notes_running),
                "elapsed": 0, "others_meter": 0, "me_meter": 0,
                "me_muted": c.cap.me_muted, "transcribing": c.transcribing,
                "others_silent": False, "me_silent": False,
                "others_device": "", "me_device": "", "profile": None,
                "degraded": False}

    def preflight(self) -> dict:
        pf = dict(self._c.preflight())
        pf["profile"] = _profile_dict(pf.get("profile"))
        return pf

    # ---------- recording controls ----------
    def start(self, name: str = "", people=None, attendees: str = "") -> bool:
        """people = headcount INCLUDING you (optional); attendees = pasted names (optional)."""
        p = None
        try:
            p = int(people) if people not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            p = None
        self._c.start_recording(name or "", people=p, attendees=attendees or None)
        return self._c.cap.state != "idle"

    def stop(self) -> bool:
        self._c.stop_recording()
        return True

    def toggle_pause(self) -> str:
        self._c.toggle_pause()
        return self._c.cap.state

    def set_mute(self, value: bool) -> bool:
        self._c.set_mute(bool(value))
        return self._c.cap.me_muted

    def refresh_devices(self) -> dict:
        """Re-scan audio devices now (called when the user opens the dropdown)."""
        self._c.refresh_audio_devices(force=True)
        pf = dict(self._c.preflight())
        pf["profile"] = _profile_dict(pf.get("profile"))
        return pf

    def set_output_device(self, label: str) -> bool:
        self._c.set_output_device(label)
        return True

    # ---------- meetings ----------
    def recent(self) -> list:
        out = []
        for folder in self._c.recent_meetings():
            meta = meeting.read_meta(folder)
            out.append({
                "folder": str(folder),
                "name": meta.get("name") or folder.name,
                "created": meta.get("created", folder.name[:16].replace("_", " ")),
                "duration": meta.get("duration"),
                "segments": meta.get("segments"),
                "speakers": meta.get("speakers"),
                "has_txt": (folder / "transcript.txt").exists(),
                "has_speakers": (folder / "speakers.json").exists(),
                "has_notes": (folder / "notes.md").exists(),
                "has_audio": (folder / "others.wav").exists() or (folder / "me.wav").exists(),
                "can_reidentify": (folder / "others.wav").exists() and (folder / "segments.json").exists(),
                "busy": str(folder) in self._c._jobs,
                "speakers_pending": bool(meta.get("speakers_pending")),
                "people": meta.get("people") or 0,
                "attendees": meta.get("attendees") or [],
            })
        return out

    def meeting_info(self, folder: str) -> dict:
        for m in self.recent():
            if m["folder"] == folder:
                return m
        return {}

    def set_meeting_details(self, folder: str, people=None, attendees=None) -> bool:
        self._c.set_meeting_details(folder, people, attendees)
        return True

    def reidentify(self, folder: str, people=None) -> bool:
        """Re-run speaker identification (optionally with a corrected headcount)."""
        p = None
        if people not in (None, ""):
            try:
                p = int(people)
            except (TypeError, ValueError):
                p = None
        return self._c.reidentify_speakers(folder, p)

    def copy_text(self, text: str) -> bool:
        from ..clipboard import copy_text
        return copy_text(text or "")

    def transcribe_meeting(self, folder: str) -> bool:
        """Recovery: transcribe a meeting that has audio but no transcript (or redo one)."""
        return self._c.transcribe_existing(folder)

    def rename_meeting(self, folder: str, new_name: str) -> str:
        return self._c.rename_meeting(folder, new_name)

    def transcript(self, folder: str) -> str:
        p = Path(folder) / "transcript.txt"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def save_transcript(self, folder: str, text: str) -> bool:
        (Path(folder) / "transcript.txt").write_text(text, encoding="utf-8")
        return True

    def notes_text(self, folder: str) -> str:
        p = Path(folder) / "notes.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # ---------- speakers ----------
    def speakers(self, folder: str) -> dict:
        sp_p = Path(folder) / "speakers.json"
        speakers = json.loads(sp_p.read_text(encoding="utf-8")) if sp_p.exists() else {}
        def order(k):
            try:
                return (0, int(k), "")
            except ValueError:
                return (1, 0, speakers[k].get("label", ""))
        rows = [speakers[c] for c in sorted(speakers, key=order)]
        attendees = self._attendees(folder)
        known = attendees + [p["name"] for p in self._c.list_voices() if p["name"] not in attendees]
        return {"rows": rows, "known": known, "attendees": attendees,
                "labels": [r["label"] for r in rows]}

    def _attendees(self, folder: str) -> list:
        """Attendees for naming OTHER speakers (you are excluded — you're on your own track)."""
        me = (self._c.cfg.me_name or "").strip().lower().split()
        me_first = me[0] if me else ""
        out = []
        for a in meeting.read_meta(folder).get("attendees") or []:
            words = str(a).strip().lower().split()
            if words and not (me_first and words[0] == me_first):
                out.append(str(a).strip())
        return out

    def snippet(self, folder: str, file: str) -> str:
        """Return the snippet WAV as a data URI for <audio> playback."""
        p = Path(folder) / Path(file).name  # sandbox to the meeting folder
        if not p.exists() or p.suffix.lower() != ".wav":
            return ""
        return "data:audio/wav;base64," + base64.b64encode(p.read_bytes()).decode()

    def apply_names(self, folder: str, mapping: dict) -> bool:
        self._c.apply_speaker_names(folder, mapping or {})
        return True

    def suggest_names(self, folder: str) -> bool:
        """Kick off LLM name-inference in the background (may take ~30-120s on a local
        model). JS polls suggest_status()."""
        import threading
        st = self._suggest.get(folder)
        if st and st.get("running"):
            return False
        self._suggest[folder] = {"running": True, "status": "", "found": 0}

        def run():
            from .. import naming
            p = Path(folder)
            sp_p, txt_p = p / "speakers.json", p / "transcript.txt"
            if not sp_p.exists() or not txt_p.exists():
                self._suggest[folder] = {"running": False, "status": "no transcript/speakers", "found": 0}
                return
            speakers = json.loads(sp_p.read_text(encoding="utf-8"))
            labels = [speakers[c]["label"] for c in speakers]
            names, status = naming.infer_names(txt_p.read_text(encoding="utf-8"), labels,
                                               self._c.cfg, attendees=self._attendees(folder))
            for sp in speakers.values():
                nm = names.get(sp["label"])
                if nm and not sp.get("named"):
                    sp["suggested"] = nm
                    sp["from_llm"] = True
            sp_p.write_text(json.dumps(speakers, indent=2), encoding="utf-8")
            self._suggest[folder] = {"running": False, "status": status, "found": len(names)}

        threading.Thread(target=run, daemon=True).start()
        return True

    def suggest_status(self, folder: str) -> dict:
        return dict(self._suggest.get(folder, {"running": False, "status": "", "found": 0}))

    # ---------- voices ----------
    def voices(self) -> list:
        return self._c.list_voices()

    def rename_voice(self, old: str, new: str) -> bool:
        self._c.rename_voice(old, new)
        return True

    def delete_voice(self, name: str) -> bool:
        self._c.delete_voice(name)
        return True

    # ---------- settings ----------
    def get_settings(self) -> dict:
        cfg = self._c.cfg
        return {"me_name": cfg.me_name, "capture_mode": cfg.capture_mode,
                "output_folder": cfg.output_folder, "hotkey": cfg.hotkey,
                "diarization_enabled": cfg.diarization_enabled,
                "keep_raw": not cfg.delete_raw_audio, "vocabulary": cfg.vocabulary,
                "notes_enabled": cfg.notes_enabled, "notes_backend": cfg.notes_backend,
                "cloud_allowed": cfg.cloud_allowed, "gemini_api_key": cfg.gemini_api_key,
                "model": cfg.model}

    def save_settings(self, s: dict) -> bool:
        self._c.save_settings(
            s.get("me_name", self._c.cfg.me_name),
            s.get("capture_mode", self._c.cfg.capture_mode),
            s.get("output_folder", self._c.cfg.output_folder),
            s.get("hotkey"),
            notes_enabled=s.get("notes_enabled"),
            notes_backend=s.get("notes_backend"),
            cloud_allowed=s.get("cloud_allowed"),
            gemini_api_key=s.get("gemini_api_key"),
            diarization_enabled=s.get("diarization_enabled"),
            delete_raw_audio=(not s["keep_raw"]) if "keep_raw" in s else None,
            vocabulary=s.get("vocabulary"))
        return True

    def test_notes(self) -> dict:
        """Synchronous connectivity/key check (runs on its own bridge thread)."""
        from ..notes import backend
        ok, msg = backend.test_connection(self._c.cfg)
        return {"ok": ok, "msg": msg}

    # ---------- model ----------
    def model_status(self) -> dict:
        return {"text": self._c.model_status(), "ready": self._c.engine.is_ready(),
                "downloading": self._model["running"]}

    def download_model(self) -> bool:
        if self._model["running"]:
            return False
        self._model = {"running": True, "ok": None}

        def done(ok):
            self._model = {"running": False, "ok": ok}
        self._c.download_model(on_done=done)
        return True

    # ---------- notes generation ----------
    def generate_notes(self, folder: str) -> bool:
        """(Re)generate notes in the background; JS polls notes_status()."""
        st = self._notes.get(folder)
        if (st and st.get("running")) or folder in self._c._notes_running:
            return False
        self._notes[folder] = {"running": True, "ok": None, "msg": ""}

        def done(ok):
            self._notes[folder] = {"running": False, "ok": ok, "msg": ""}
        self._c.generate_notes(folder, on_done=done)
        return True

    def notes_status(self, folder: str) -> dict:
        st = dict(self._notes.get(folder, {"running": False, "ok": None}))
        # auto-notes started by the controller after a meeting count as running too
        st["running"] = bool(st.get("running")) or folder in self._c._notes_running
        st["exists"] = (Path(folder) / "notes.md").exists()
        st["backend"] = self._c.cfg.notes_backend
        st["model"] = (self._c.cfg.ollama_model if self._c.cfg.notes_backend == "local"
                       else self._c.cfg.gemini_model)
        return st

    # ---------- misc ----------
    def open_path(self, path: str) -> bool:
        self._c.open_path(path)
        return True

    def open_output_folder(self) -> bool:
        self._c.open_output_folder()
        return True

    def app_info(self) -> dict:
        from .. import __version__
        log.info("web UI booted (bridge alive)")  # beacon: HTML+JS+bridge all working
        return {"version": __version__, "needs_setup": self._c.needs_setup(),
                "ts": time.time()}

    def quit_app(self) -> bool:
        log.info("quit requested from web UI")
        self._c.quit()
        return True
