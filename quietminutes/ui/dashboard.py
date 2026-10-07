"""Dashboard window — modern sidebar UI.

Left sidebar (nav + live status dot) and a content area that swaps between three
views: Record, Recent, Settings. A single Tk `after` poll loop (100 ms) drives the
meters, status pill, sidebar status, mute switch sync, AND the tray state/title — all
on the main thread.

Design language borrowed from TranscriptionSuite: sidebar nav, flat cards, status
dots, segmented/switch controls, restrained dark palette.
"""

from __future__ import annotations

import customtkinter as ctk

from .. import meeting

# ---- palette ----
BG = "#15161A"        # window base
SIDE = "#1B1D23"      # sidebar
CARD = "#202228"      # cards
CARD2 = "#262931"     # nested rows
ACCENT = "#5B8DEF"    # primary / active nav
TEXT = "#E8E9ED"
TEXT2 = "#9AA0AA"
TEXT3 = "#6B7280"
GREEN = "#22C55E"
RED = "#EF4444"
AMBER = "#F59E0B"
MONO = "Consolas"
UI = "Segoe UI"


def _fmt_elapsed(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


class Dashboard(ctk.CTk):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        ctk.set_appearance_mode("dark")
        self.title("QuietMinutes")
        self.geometry("780x620")
        self.minsize(720, 560)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self.hide)

        self.mute_var = ctk.BooleanVar(value=False)
        self.views: dict[str, ctk.CTkFrame] = {}
        self.nav: dict[str, ctk.CTkButton] = {}
        self.current_view = "record"

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=4)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._build_record_view()
        self._build_recent_view()
        self._build_voices_view()
        self._build_settings_view()
        self.show_view("record")

        self.after(100, self._poll)

    # =====================================================================
    # Sidebar
    # =====================================================================
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=SIDE, width=190, corner_radius=0)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)

        ctk.CTkLabel(bar, text="  ◉  QuietMinutes", font=(UI, 16, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(20, 18))

        for key, label, icon in [("record", "Record", "●"),
                                  ("recent", "Recent", "≣"),
                                  ("voices", "Voices", "☻"),
                                  ("settings", "Settings", "⚙")]:
            b = ctk.CTkButton(
                bar, text=f"  {icon}   {label}", anchor="w", height=40,
                corner_radius=8, fg_color="transparent", text_color=TEXT2,
                hover_color=CARD, font=(UI, 13),
                command=lambda k=key: self.show_view(k))
            b.pack(fill="x", padx=10, pady=2)
            self.nav[key] = b

        # bottom: live status block
        spacer = ctk.CTkFrame(bar, fg_color="transparent")
        spacer.pack(fill="both", expand=True)
        status = ctk.CTkFrame(bar, fg_color=CARD, corner_radius=10)
        status.pack(fill="x", padx=10, pady=12, side="bottom")
        row = ctk.CTkFrame(status, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 2))
        self.side_dot = ctk.CTkLabel(row, text="●", font=(UI, 14), text_color=TEXT3, width=14)
        self.side_dot.pack(side="left")
        self.side_state = ctk.CTkLabel(row, text="Idle", font=(UI, 12, "bold"),
                                       text_color=TEXT, anchor="w")
        self.side_state.pack(side="left", padx=4)
        self.side_elapsed = ctk.CTkLabel(status, text="00:00:00", font=(MONO, 18, "bold"),
                                         text_color=TEXT)
        self.side_elapsed.pack(anchor="w", padx=12, pady=(0, 10))

    # =====================================================================
    # Record view
    # =====================================================================
    def _card(self, parent, title: str) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12)
        f.pack(fill="x", padx=18, pady=(0, 14))
        if title:
            ctk.CTkLabel(f, text=title, font=(UI, 11, "bold"), text_color=TEXT3).pack(
                anchor="w", padx=16, pady=(12, 2))
        return f

    def _build_record_view(self) -> None:
        v = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self.views["record"] = v

        head = ctk.CTkFrame(v, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(8, 14))
        ctk.CTkLabel(head, text="Session", font=(UI, 22, "bold"), text_color=TEXT).pack(side="left")
        self.pill = ctk.CTkLabel(head, text="Idle", fg_color="#2A2D35", corner_radius=14,
                                 font=(UI, 12, "bold"), text_color=TEXT, width=150, height=30)
        self.pill.pack(side="right")

        # meeting name
        nm = self._card(v, "MEETING NAME")
        self.name_entry = ctk.CTkEntry(
            nm, height=36, font=(UI, 13),
            placeholder_text="Optional — e.g. Weekly team sync (or rename later)")
        self.name_entry.pack(fill="x", padx=16, pady=(0, 12))

        # pre-flight card
        pf = self._card(v, "PRE-FLIGHT")
        outrow = ctk.CTkFrame(pf, fg_color="transparent")
        outrow.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(outrow, text="Capture output:", font=(UI, 11, "bold"),
                     text_color=TEXT2).pack(side="left")
        self.output_menu = ctk.CTkOptionMenu(outrow, values=["Follow Windows default"], width=320,
                                             height=28, dynamic_resizing=False,
                                             fg_color=CARD2, button_color="#33363F",
                                             button_hover_color="#3d414c",
                                             command=self.c.set_output_device)
        self.output_menu.pack(side="left", padx=8)
        self._output_choices = []
        self.pf_profile = ctk.CTkLabel(pf, text="", font=(UI, 13, "bold"), anchor="w")
        self.pf_profile.pack(fill="x", padx=16, pady=(0, 4))
        self.pf_others = ctk.CTkLabel(pf, text="", font=(MONO, 11), anchor="w", text_color=TEXT2)
        self.pf_others.pack(fill="x", padx=16)
        self.pf_me = ctk.CTkLabel(pf, text="", font=(MONO, 11), anchor="w", text_color=TEXT2)
        self.pf_me.pack(fill="x", padx=16)
        self.pf_misc = ctk.CTkLabel(pf, text="", font=(MONO, 11), anchor="w", text_color=TEXT3)
        self.pf_misc.pack(fill="x", padx=16, pady=(0, 12))
        self.hfp_banner = ctk.CTkLabel(
            pf, text="", fg_color="#3A2E00", text_color="#FFD86B", corner_radius=8,
            font=(UI, 11, "bold"), wraplength=560, justify="left")

        # levels card
        lv = self._card(v, "LIVE LEVELS")
        self.m_others, self.m_others_sub, _ = self._meter_row(lv, "Others", mute=False)
        self.m_me, self.m_me_sub, self.mute_switch = self._meter_row(lv, "Me", mute=True)
        self.mic_banner = ctk.CTkLabel(
            lv, text="", fg_color="#3A1F22", text_color="#FF8A8A", corner_radius=8,
            font=(UI, 11, "bold"), wraplength=560, justify="left")
        self._me_silent_since = None
        ctk.CTkLabel(lv, text="", height=2).pack()

        # controls
        ctrl = ctk.CTkFrame(v, fg_color="transparent")
        ctrl.pack(fill="x", padx=18, pady=(0, 16))
        self.btn_start = ctk.CTkButton(ctrl, text="●  Start", fg_color=RED, hover_color="#C53030",
                                       height=48, corner_radius=10, font=(UI, 15, "bold"),
                                       command=self._start)
        self.btn_start.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.btn_pause = ctk.CTkButton(ctrl, text="❚❚  Pause", height=48, width=130, corner_radius=10,
                                       fg_color=CARD2, hover_color="#30343d", font=(UI, 13),
                                       command=self.c.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=8)
        self.btn_stop = ctk.CTkButton(ctrl, text="■  Stop", height=48, width=130, corner_radius=10,
                                      fg_color=CARD2, hover_color="#30343d", font=(UI, 13),
                                      command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))

    def _meter_row(self, parent, label, mute):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(2, 8))
        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=label, font=(UI, 13, "bold"), text_color=TEXT,
                     width=60, anchor="w").pack(side="left")
        sub = ctk.CTkLabel(top, text="—", font=(MONO, 11), text_color=TEXT3, anchor="e")
        sub.pack(side="right")
        sw = None
        if mute:
            sw = ctk.CTkSwitch(top, text="Mute", variable=self.mute_var,
                               command=lambda: self.c.set_mute(self.mute_var.get()),
                               font=(UI, 11), progress_color=AMBER, button_color=TEXT,
                               width=44)
            sw.pack(side="left", padx=12)
        bar = ctk.CTkProgressBar(row, height=14, corner_radius=7, progress_color=GREEN,
                                 fg_color=CARD2)
        bar.set(0.0)
        bar.pack(fill="x", pady=(6, 0))
        return bar, sub, sw

    # =====================================================================
    # Recent view
    # =====================================================================
    def _build_recent_view(self) -> None:
        v = ctk.CTkFrame(self.content, fg_color="transparent")
        self.views["recent"] = v
        ctk.CTkLabel(v, text="Recent meetings", font=(UI, 22, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(8, 14))
        self.recent = ctk.CTkScrollableFrame(v, fg_color=CARD, corner_radius=12)
        self.recent.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    @staticmethod
    def _fmt_dur(seconds) -> str:
        if not seconds:
            return "—"
        seconds = int(seconds)
        return f"{seconds // 60}m {seconds % 60:02d}s"

    def _refresh_recent(self):
        for w in self.recent.winfo_children():
            w.destroy()
        for folder in self.c.recent_meetings():
            meta = meeting.read_meta(folder)
            name = meta.get("name") or folder.name
            created = meta.get("created", folder.name[:16].replace("_", "  "))
            bits = [created, self._fmt_dur(meta.get("duration"))]
            if meta.get("segments") is not None:
                bits.append(f"{meta['segments']} segments")
            has_txt = (folder / "transcript.txt").exists()

            r = ctk.CTkFrame(self.recent, fg_color=CARD2, corner_radius=8)
            r.pack(fill="x", padx=8, pady=4)
            info = ctk.CTkFrame(r, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(info, text=name, font=(UI, 13, "bold"), text_color=TEXT,
                         anchor="w").pack(fill="x")
            ctk.CTkLabel(info, text="   ·   ".join(bits), font=(MONO, 10), text_color=TEXT3,
                         anchor="w").pack(fill="x")

            has_speakers = (folder / "speakers.json").exists()
            btns = ctk.CTkFrame(r, fg_color="transparent")
            btns.pack(side="right", padx=6)
            ctk.CTkButton(btns, text="✎", width=30, height=28, corner_radius=6, fg_color="#33363F",
                          hover_color="#3d414c",
                          command=lambda p=folder: self._rename(p)).pack(side="left", padx=3)
            if has_speakers:
                ctk.CTkButton(btns, text="Speakers", width=78, height=28, corner_radius=6,
                              fg_color="#33363F", hover_color="#3d414c",
                              command=lambda p=folder: self.c.open_speaker_rename(
                                  p, on_done=self._refresh_recent)).pack(side="left", padx=3)
            if has_txt:
                ctk.CTkButton(btns, text="Transcript", width=82, height=28, corner_radius=6,
                              fg_color=ACCENT, hover_color="#4A7BD8",
                              command=lambda p=folder: self.c.open_viewer(p)).pack(side="left", padx=3)
            ctk.CTkButton(btns, text="Folder", width=62, height=28, corner_radius=6,
                          fg_color="#33363F", hover_color="#3d414c",
                          command=lambda p=folder: self.c.open_path(p)).pack(side="left", padx=3)
        if not self.recent.winfo_children():
            ctk.CTkLabel(self.recent, text="No meetings yet", text_color=TEXT3).pack(pady=16)

    def _rename(self, folder):
        dlg = ctk.CTkInputDialog(text="New meeting name:", title="Rename meeting")
        new = dlg.get_input()
        if new is not None and new.strip():
            self.c.rename_meeting(folder, new)
            self._refresh_recent()

    # =====================================================================
    # Voices view (enrolled people)
    # =====================================================================
    def _build_voices_view(self) -> None:
        v = ctk.CTkFrame(self.content, fg_color="transparent")
        self.views["voices"] = v
        head = ctk.CTkFrame(v, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(8, 14))
        ctk.CTkLabel(head, text="Voices", font=(UI, 22, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(head, text="people the app has learned", font=(UI, 11),
                     text_color=TEXT3).pack(side="left", padx=10)
        self.voices = ctk.CTkScrollableFrame(v, fg_color=CARD, corner_radius=12)
        self.voices.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    def _refresh_voices(self):
        for w in self.voices.winfo_children():
            w.destroy()
        for person in self.c.list_voices():
            r = ctk.CTkFrame(self.voices, fg_color=CARD2, corner_radius=8)
            r.pack(fill="x", padx=8, pady=4)
            info = ctk.CTkFrame(r, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(info, text=person["name"], font=(UI, 13, "bold"), text_color=TEXT,
                         anchor="w").pack(fill="x")
            ctk.CTkLabel(info, text=f"seen in {person['meetings_seen']} meeting(s) · "
                         f"{person['samples']} voiceprint(s)", font=(MONO, 10),
                         text_color=TEXT3, anchor="w").pack(fill="x")
            ctk.CTkButton(r, text="Delete", width=68, height=28, corner_radius=6, fg_color="#5A2A2A",
                          hover_color="#6e3333",
                          command=lambda n=person["name"]: self._delete_voice(n)).pack(
                side="right", padx=6)
            ctk.CTkButton(r, text="Rename", width=70, height=28, corner_radius=6, fg_color="#33363F",
                          hover_color="#3d414c",
                          command=lambda n=person["name"]: self._rename_voice(n)).pack(
                side="right", padx=3)
        if not self.voices.winfo_children():
            ctk.CTkLabel(self.voices, text="No voices learned yet — name speakers after a "
                         "meeting to teach the app.", text_color=TEXT3, wraplength=440).pack(pady=16)

    def _rename_voice(self, name):
        dlg = ctk.CTkInputDialog(text=f"Rename '{name}' to:", title="Rename voice")
        new = dlg.get_input()
        if new and new.strip():
            self.c.rename_voice(name, new.strip())
            self._refresh_voices()

    def _delete_voice(self, name):
        self.c.delete_voice(name)
        self._refresh_voices()

    # =====================================================================
    # Settings view
    # =====================================================================
    def _build_settings_view(self) -> None:
        v = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self.views["settings"] = v
        ctk.CTkLabel(v, text="Settings", font=(UI, 22, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(8, 14))

        card = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        card.pack(fill="x", padx=18, pady=(0, 14))

        ctk.CTkLabel(card, text="\"Me\" name", font=(UI, 12, "bold"), text_color=TEXT).pack(
            anchor="w", padx=16, pady=(14, 2))
        self.set_me_name = ctk.CTkEntry(card, height=34)
        self.set_me_name.pack(fill="x", padx=16)

        ctk.CTkLabel(card, text="Capture mode", font=(UI, 12, "bold"), text_color=TEXT).pack(
            anchor="w", padx=16, pady=(14, 2))
        self.set_mode = ctk.CTkSegmentedButton(
            card, values=["quality", "convenience"], height=34,
            selected_color=ACCENT, selected_hover_color="#4A7BD8")
        self.set_mode.pack(fill="x", padx=16)
        ctk.CTkLabel(
            card, justify="left", font=(UI, 10), text_color=TEXT3,
            text="quality = laptop mic + AirPods output-only (A2DP, recommended)\n"
                 "convenience = AirPods mic (HFP, narrowband — accuracy drops)").pack(
            anchor="w", padx=16, pady=(4, 0))

        ctk.CTkLabel(card, text="Output folder", font=(UI, 12, "bold"), text_color=TEXT).pack(
            anchor="w", padx=16, pady=(14, 2))
        self.set_out = ctk.CTkEntry(card, height=34)
        self.set_out.pack(fill="x", padx=16)

        ctk.CTkLabel(card, text="Global start/stop hotkey", font=(UI, 12, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        self.set_hotkey = ctk.CTkEntry(card, height=34)
        self.set_hotkey.pack(fill="x", padx=16)
        ctk.CTkLabel(card, justify="left", font=(UI, 10), text_color=TEXT3,
                     text="pynput syntax, e.g. <ctrl>+<alt>+r — toggles recording from any app").pack(
            anchor="w", padx=16, pady=(4, 10))

        self.diar_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(card, text="Separate speakers (diarization → Speaker 1, 2, …)",
                      variable=self.diar_var, font=(UI, 11), progress_color=ACCENT).pack(
            anchor="w", padx=16, pady=4)
        self.keepraw_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(card, text="Keep raw audio after transcript (uses disk; for tuning)",
                      variable=self.keepraw_var, font=(UI, 11), progress_color=ACCENT).pack(
            anchor="w", padx=16, pady=(4, 14))

        mcard = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        mcard.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkLabel(mcard, text="Whisper model", font=(UI, 12, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        self.set_model_status = ctk.CTkLabel(mcard, text="", font=(MONO, 11), text_color=TEXT2,
                                             anchor="w")
        self.set_model_status.pack(anchor="w", padx=16)
        self.btn_download = ctk.CTkButton(mcard, text="Download model", height=32, corner_radius=8,
                                          fg_color=ACCENT, hover_color="#4A7BD8",
                                          command=self._download_model)
        self.btn_download.pack(anchor="w", padx=16, pady=(8, 14))

        # AI notes (Phase 4)
        ncard = ctk.CTkFrame(v, fg_color=CARD, corner_radius=12)
        ncard.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkLabel(ncard, text="AI notes (summary · action items · MoM)", font=(UI, 12, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 2))
        self.notes_enabled_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(ncard, text="Auto-generate notes.md after each meeting",
                      variable=self.notes_enabled_var, font=(UI, 11),
                      progress_color=ACCENT).pack(anchor="w", padx=16, pady=4)
        self.notes_backend = ctk.CTkSegmentedButton(
            ncard, values=["local", "gemini"], height=32,
            selected_color=ACCENT, selected_hover_color="#4A7BD8")
        self.notes_backend.pack(fill="x", padx=16, pady=(6, 2))
        ctk.CTkLabel(ncard, justify="left", font=(UI, 10), text_color=TEXT3,
                     text="local = Ollama (offline, free).  gemini = cloud Flash-Lite "
                          "(cheapest; sends TEXT ONLY, never audio).").pack(anchor="w", padx=16)
        self.cloud_allowed_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(ncard, text="I allow sending transcript TEXT to the cloud (Gemini)",
                      variable=self.cloud_allowed_var, font=(UI, 11),
                      progress_color=AMBER).pack(anchor="w", padx=16, pady=(8, 4))
        ctk.CTkLabel(ncard, text="Gemini API key", font=(UI, 11), text_color=TEXT2).pack(
            anchor="w", padx=16, pady=(6, 0))
        self.set_gemini_key = ctk.CTkEntry(ncard, height=32, show="•",
                                           placeholder_text="from aistudio.google.com → Get API key")
        self.set_gemini_key.pack(fill="x", padx=16, pady=(0, 6))
        testrow = ctk.CTkFrame(ncard, fg_color="transparent")
        testrow.pack(fill="x", padx=16, pady=(0, 14))
        self.btn_test_notes = ctk.CTkButton(testrow, text="Test connection", width=130, height=30,
                                            fg_color=CARD2, hover_color="#30343d",
                                            command=self._test_notes)
        self.btn_test_notes.pack(side="left")
        self.notes_test_lbl = ctk.CTkLabel(testrow, text="", font=(UI, 11), text_color=TEXT2,
                                           anchor="w", wraplength=380, justify="left")
        self.notes_test_lbl.pack(side="left", padx=10)

        bar = ctk.CTkFrame(v, fg_color="transparent")
        bar.pack(fill="x", padx=18)
        self.set_saved = ctk.CTkLabel(bar, text="", font=(UI, 11), text_color=GREEN)
        self.set_saved.pack(side="left")
        ctk.CTkButton(bar, text="Save", width=110, height=36, corner_radius=8, fg_color=ACCENT,
                      hover_color="#4A7BD8", font=(UI, 13, "bold"),
                      command=self._save_settings).pack(side="right")

    def _refresh_model_status(self):
        self.set_model_status.configure(text=self.c.model_status())
        ready = self.c.engine.is_ready()
        self.btn_download.configure(state="disabled" if ready else "normal",
                                    text="✓ Installed" if ready else "Download model (one-time)")

    def _download_model(self):
        self.btn_download.configure(state="disabled", text="Downloading… (one-time)")
        self.c.download_model(on_done=lambda ok: self.after(0, self._refresh_model_status))

    def _load_settings_fields(self):
        cfg = self.c.cfg
        self.set_me_name.delete(0, "end"); self.set_me_name.insert(0, cfg.me_name)
        self.set_mode.set(cfg.capture_mode)
        self.set_out.delete(0, "end"); self.set_out.insert(0, cfg.output_folder)
        self.set_hotkey.delete(0, "end"); self.set_hotkey.insert(0, cfg.hotkey)
        self.diar_var.set(cfg.diarization_enabled)
        self.keepraw_var.set(not cfg.delete_raw_audio)
        self.notes_enabled_var.set(cfg.notes_enabled)
        self.notes_backend.set(cfg.notes_backend)
        self.cloud_allowed_var.set(cfg.cloud_allowed)
        self.set_gemini_key.delete(0, "end"); self.set_gemini_key.insert(0, cfg.gemini_api_key)
        self.set_saved.configure(text="")
        self._refresh_model_status()

    def _save_settings(self):
        self.c.save_settings(self.set_me_name.get(), self.set_mode.get(), self.set_out.get(),
                             self.set_hotkey.get(),
                             notes_enabled=self.notes_enabled_var.get(),
                             notes_backend=self.notes_backend.get(),
                             cloud_allowed=self.cloud_allowed_var.get(),
                             gemini_api_key=self.set_gemini_key.get(),
                             diarization_enabled=self.diar_var.get(),
                             delete_raw_audio=not self.keepraw_var.get())
        self.set_saved.configure(text="✓ Saved")

    def _test_notes(self):
        self._save_settings()  # use whatever is typed right now
        self.btn_test_notes.configure(state="disabled")
        self.notes_test_lbl.configure(text="Testing…", text_color=TEXT2)
        self.c.test_notes_connection(self._test_notes_done)

    def _test_notes_done(self, ok, msg):
        def finish():
            self.btn_test_notes.configure(state="normal")
            self.notes_test_lbl.configure(text=("✓ " if ok else "✗ ") + msg,
                                          text_color=GREEN if ok else RED)
        try:
            self.after(0, finish)
        except Exception:  # noqa: BLE001
            pass

    # =====================================================================
    # View switching + window show/hide
    # =====================================================================
    def show_view(self, name: str):
        for n, f in self.views.items():
            f.grid_forget()
        self.views[name].grid(row=0, column=0, sticky="nsew")
        for n, b in self.nav.items():
            active = n == name
            b.configure(fg_color=ACCENT if active else "transparent",
                        text_color="#FFFFFF" if active else TEXT2)
        self.current_view = name
        if name == "recent":
            self._refresh_recent()
        elif name == "voices":
            self._refresh_voices()
        elif name == "settings":
            self._load_settings_fields()

    def show(self):
        self.deiconify(); self.lift(); self.focus_force()

    def hide(self):
        self.withdraw()

    def _start(self):
        self.c.start_recording(self.name_entry.get())
        self._pending_clear = True

    def _stop(self):
        self.c.stop_recording()
        self.after(400, self._refresh_recent)

    # =====================================================================
    # Poll loop
    # =====================================================================
    def _poll(self):
        try:
            self._update()
        except Exception as exc:  # noqa: BLE001
            print(f"[dashboard] poll error: {exc}")
        self.after(100, self._poll)

    def _update(self):
        recording = self.c.cap.state != "idle"
        if recording:
            s = self.c.cap.status()
            state = s["state"]
            self._render_meters(s)
            self.pf_others.configure(text=f"Others : {s['others_device'][:52]}")
            self.pf_me.configure(text=f"Me     : {s['me_device'][:52]}")
            self._set_profile(s["profile"])
            self._set_status(state, s["elapsed"])
            self.btn_start.configure(state="disabled")
            self.btn_pause.configure(state="normal",
                                     text="▶  Resume" if state == "paused" else "❚❚  Pause")
            self.btn_stop.configure(state="normal")
            self.name_entry.configure(state="disabled")
            self.output_menu.configure(state="disabled")
            self.c.tray.set_state(state, f"QuietMinutes — {state} {_fmt_elapsed(s['elapsed'])}")
        elif getattr(self.c, "transcribing", False):
            self.m_others.set(0.0); self.m_me.set(0.0)
            self.m_others_sub.configure(text="—", text_color=TEXT3)
            self.m_me_sub.configure(text="—", text_color=TEXT3)
            self._set_status("transcribing", 0)
            self.btn_start.configure(state="disabled")
            self.btn_pause.configure(state="disabled", text="❚❚  Pause")
            self.btn_stop.configure(state="disabled")
            self.c.tray.set_state("transcribing", "QuietMinutes — transcribing…")
        else:
            pf = self.c.preflight()
            self.m_others.set(0.0); self.m_me.set(0.0)
            self.m_others_sub.configure(text="—", text_color=TEXT3)
            self.m_me_sub.configure(text="muted" if self.c.cap.me_muted else "—",
                                    text_color=AMBER if self.c.cap.me_muted else TEXT3)
            self.pf_others.configure(text=f"Others : {pf['others_device'][:52]}")
            self.pf_me.configure(text=f"Me     : {pf['me_device'][:52]}  [{pf['mode']}]")
            self.pf_misc.configure(text=f"Model  : {pf['model']}     Free disk: {pf['free_gb']:.1f} GB")
            self._set_profile(pf["profile"])
            self._set_status("idle", 0)
            choices = pf.get("output_choices", [])
            if choices and choices != self._output_choices:
                self.output_menu.configure(values=choices)
                self._output_choices = choices
            cur = pf.get("output_current", "Follow Windows default")
            if self.output_menu.get() != cur:
                self.output_menu.set(cur)
            self.output_menu.configure(state="normal")
            self.btn_start.configure(state="normal")
            self.btn_pause.configure(state="disabled", text="❚❚  Pause")
            self.btn_stop.configure(state="disabled")
            if self.name_entry.cget("state") == "disabled":
                self.name_entry.configure(state="normal")
            if getattr(self, "_pending_clear", False):
                self.name_entry.delete(0, "end")
                self._pending_clear = False
            self.c.tray.set_state("idle", "QuietMinutes — idle")

        # keep the mute switch in sync with capture (it can change from the tray)
        if self.mute_var.get() != self.c.cap.me_muted:
            self.mute_var.set(self.c.cap.me_muted)

        # refresh Recent when a transcription just finished
        if getattr(self.c, "transcribing", False):
            self._was_transcribing = True
        elif getattr(self, "_was_transcribing", False):
            self._was_transcribing = False
            self._refresh_recent()

        # keep the model status/button live while Settings is open
        if self.current_view == "settings":
            self._refresh_model_status()

    def _update_mic_banner(self, s):
        """Red banner when the mic is delivering silence mid-recording (not muted)."""
        import time as _t
        silent = s["state"] == "recording" and not s["me_muted"] and s["me_silent"]
        if silent:
            if self._me_silent_since is None:
                self._me_silent_since = _t.monotonic()
            elapsed = _t.monotonic() - self._me_silent_since
            if elapsed >= 20:
                self.mic_banner.configure(
                    text=f"⚠  Your mic has been SILENT for {int(elapsed)}s — your words are "
                         "not being recorded. Check Windows default mic / unmute.")
                self.mic_banner.pack(fill="x", padx=16, pady=(4, 8))
                return
        else:
            self._me_silent_since = None
        self.mic_banner.pack_forget()

    def _render_meters(self, s):
        self._update_mic_banner(s)
        self.m_others.set(s["others_meter"])
        self.m_others_sub.configure(
            text="SILENT" if s["others_silent"] else f"{s['others_peak']:.3f}",
            text_color=RED if s["others_silent"] else TEXT3)
        if s["me_muted"]:
            self.m_me.set(0.0)
            self.m_me_sub.configure(text="MUTED", text_color=AMBER)
        else:
            self.m_me.set(s["me_meter"])
            self.m_me_sub.configure(
                text="SILENT" if s["me_silent"] else f"{s['me_peak']:.3f}",
                text_color=RED if s["me_silent"] else TEXT3)

    def _set_profile(self, prof):
        if prof is None:
            self.pf_profile.configure(text="● Profile: (unknown)", text_color=TEXT3)
            self.hfp_banner.pack_forget()
            return
        self.pf_profile.configure(text=f"●  {prof.label}", text_color=prof.color)
        if prof.is_warning:
            self.hfp_banner.configure(
                text="⚠  HFP detected — AirPods are in phone-call mode. Audio is narrowband; "
                     "transcription accuracy drops and many-speaker separation will suffer. "
                     "Switch your meeting app's microphone to the laptop mic (quality-first).")
            self.hfp_banner.pack(fill="x", padx=16, pady=(0, 12))
        else:
            self.hfp_banner.pack_forget()

    def _set_status(self, state, elapsed):
        color = {"recording": RED, "paused": AMBER,
                 "transcribing": ACCENT, "idle": TEXT3}[state]
        side_text = {"recording": "Recording", "paused": "Paused",
                     "transcribing": "Transcribing", "idle": "Idle"}[state]
        self.side_dot.configure(text_color=color)
        self.side_state.configure(text=side_text)
        self.side_elapsed.configure(text=_fmt_elapsed(elapsed))
        if state == "recording":
            self.pill.configure(text=f"● Recording {_fmt_elapsed(elapsed)}", fg_color="#3A1F22",
                                text_color=RED)
        elif state == "paused":
            self.pill.configure(text=f"❚❚ Paused {_fmt_elapsed(elapsed)}", fg_color="#3A2E00",
                                text_color=AMBER)
        elif state == "transcribing":
            self.pill.configure(text="⏳ Transcribing…", fg_color="#1F2A3A", text_color=ACCENT)
        else:
            self.pill.configure(text="Idle", fg_color="#2A2D35", text_color=TEXT2)
