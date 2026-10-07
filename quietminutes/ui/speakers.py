"""Many-speaker rename dialog + transcript viewer/editor (Phase 3).

The rename dialog is built for 4–10+ speakers: a scrollable table, one row per detected
speaker with a sample utterance, a ▶ play-snippet button, a suggested name + confidence
(from the voiceprint DB), and an editable name field. Apply re-renders the transcript and
enrolls the named voices (implicit enrollment).
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from ..diarize import pipeline

# palette (kept local to avoid a hard dependency on dashboard internals)
CARD = "#202228"
CARD2 = "#262931"
ACCENT = "#5B8DEF"
GREEN = "#22C55E"
AMBER = "#F59E0B"
TEXT = "#E8E9ED"
TEXT2 = "#9AA0AA"
TEXT3 = "#6B7280"
UI = "Segoe UI"
MONO = "Consolas"


def _play(path: Path) -> None:
    try:
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as exc:  # noqa: BLE001
        print(f"[snippet] {exc}")


class RenameSpeakersDialog(ctk.CTkToplevel):
    def __init__(self, master, controller, folder, on_done=None):
        super().__init__(master)
        self.c = controller
        self.folder = Path(folder)
        self.on_done = on_done
        self.title("Name speakers")
        self.geometry("640x560")
        self.configure(fg_color="#15161A")
        self.transient(master)
        self.after(80, self.grab_set)

        speakers, _embs = pipeline.load_speakers(self.folder)
        self.rows: list[tuple[str, ctk.CTkComboBox]] = []
        self._labels = [speakers[c]["label"] for c in sorted(speakers, key=lambda k: speakers[k]["label"])]
        try:
            self._known = [p["name"] for p in controller.list_voices()]
        except Exception:  # noqa: BLE001
            self._known = []

        ctk.CTkLabel(self, text="Who's who?", font=(UI, 20, "bold"), text_color=TEXT).pack(
            anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(self, text="Play a sample, then type a name — or pick another Speaker from "
                     "the dropdown to MERGE the two (same voice split in half). Same name on two "
                     "rows also merges them. Names are remembered for next time.", font=(UI, 11),
                     text_color=TEXT2, wraplength=600, justify="left").pack(anchor="w", padx=18)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)

        if not speakers:
            ctk.CTkLabel(body, text="No diarized speakers for this meeting.",
                         text_color=TEXT3).pack(pady=20)
        for cid in sorted(speakers, key=lambda k: speakers[k]["label"]):
            self._build_row(body, speakers[cid])

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(bar, text="Cancel", width=90, height=36, fg_color=CARD2,
                      hover_color="#30343d", command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(bar, text="Apply", width=120, height=36, fg_color=ACCENT,
                      hover_color="#4A7BD8", font=(UI, 13, "bold"),
                      command=self._apply).pack(side="right")

    def _build_row(self, parent, sp: dict):
        row = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10)
        row.pack(fill="x", padx=4, pady=5)

        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(top, text=sp["label"], font=(UI, 13, "bold"), text_color=TEXT,
                     width=90, anchor="w").pack(side="left")
        snippet = sp.get("snippet")
        if snippet and (self.folder / snippet).exists():
            ctk.CTkButton(top, text="▶  Play", width=80, height=28, fg_color=CARD2,
                          hover_color="#30343d",
                          command=lambda p=self.folder / snippet: _play(p)).pack(side="left")
        if sp.get("suggested"):
            conf = int(sp.get("confidence", 0) * 100)
            ctk.CTkLabel(top, text=f"suggested: {sp['suggested']} ({conf}%)", font=(UI, 11),
                         text_color=GREEN).pack(side="right")

        ctk.CTkLabel(row, text=f"“{sp.get('sample', '')}”", font=(MONO, 11), text_color=TEXT2,
                     wraplength=560, justify="left", anchor="w").pack(fill="x", padx=12, pady=2)

        # combobox: type a real name, pick a known person, or pick another Speaker
        # label to merge this cluster into that one.
        others = [l for l in self._labels if l != sp["label"]]
        choices = self._known + (["─ merge into ─"] if self._known and others else []) + others
        box = ctk.CTkComboBox(row, height=32, values=choices or [""],
                              fg_color=CARD2, border_color="#33363F",
                              button_color="#33363F", button_hover_color="#3d414c")
        box.set(sp.get("named") or sp.get("suggested") or "")
        box.pack(fill="x", padx=12, pady=(2, 12))
        self.rows.append((sp["label"], box))

    def _apply(self):
        mapping = {label: e.get().strip() for label, e in self.rows
                   if e.get().strip() and e.get().strip() != "─ merge into ─"}
        if mapping:
            self.c.apply_speaker_names(self.folder, mapping)
        if self.on_done:
            self.on_done()
        self.destroy()


class TranscriptViewer(ctk.CTkToplevel):
    def __init__(self, master, controller, folder):
        super().__init__(master)
        self.c = controller
        self.folder = Path(folder)
        self.txt_path = self.folder / "transcript.txt"
        self.title(self.folder.name)
        self.geometry("720x620")
        self.configure(fg_color="#15161A")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(bar, text="Transcript", font=(UI, 18, "bold"), text_color=TEXT).pack(side="left")
        if (self.folder / "speakers.json").exists():
            ctk.CTkButton(bar, text="Name speakers", height=30, fg_color=ACCENT,
                          hover_color="#4A7BD8", command=self._rename).pack(side="right", padx=4)
        self.btn_notes = ctk.CTkButton(bar, text="Generate notes", height=30, fg_color=CARD2,
                                       hover_color="#30343d", command=self._notes)
        self.btn_notes.pack(side="right", padx=4)
        ctk.CTkButton(bar, text="Copy", width=64, height=30, fg_color=CARD2, hover_color="#30343d",
                      command=self._copy).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="Folder", width=70, height=30, fg_color=CARD2, hover_color="#30343d",
                      command=lambda: self.c.open_path(self.folder)).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="Save", width=64, height=30, fg_color=CARD2, hover_color="#30343d",
                      command=self._save).pack(side="right", padx=4)

        self.box = ctk.CTkTextbox(self, font=(MONO, 12), wrap="word", fg_color=CARD)
        self.box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._load()

    def _load(self):
        text = self.txt_path.read_text(encoding="utf-8") if self.txt_path.exists() else ""
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)

    def _save(self):
        self.txt_path.write_text(self.box.get("1.0", "end-1c"), encoding="utf-8")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.box.get("1.0", "end-1c"))

    def _rename(self):
        RenameSpeakersDialog(self, self.c, self.folder, on_done=self._load)

    def _notes(self):
        notes_md = self.folder / "notes.md"
        if notes_md.exists():
            self.c.open_path(notes_md)
            return
        self.btn_notes.configure(state="disabled", text="Generating…")
        self.c.generate_notes(self.folder, on_done=self._notes_done)

    def _notes_done(self, ok):
        # called from a worker thread — marshal UI work back to Tk
        def finish():
            self.btn_notes.configure(state="normal", text="Generate notes")
            if ok:
                self.c.open_path(self.folder / "notes.md")
        try:
            self.after(0, finish)
        except Exception:  # noqa: BLE001 - window may be closed
            pass
