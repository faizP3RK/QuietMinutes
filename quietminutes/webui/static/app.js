/* QuietMinutes web UI. Talks to Python via window.pywebview.api.
   If the bridge is absent (opened in a plain browser), a demo shim renders
   sample data so the design can be previewed. */

"use strict";

/* ---------------- bridge ---------------- */
let API = null;
const DEMO = {
  status: async () => ({ state: "idle", elapsed: 0, others_meter: 0, me_meter: 0,
    me_muted: false, transcribing: false, others_silent: false, me_silent: false,
    others_device: "", me_device: "", profile: null, degraded: false }),
  preflight: async () => ({
    others_device: "Headphones (AirPods) [Loopback]",
    me_device: "Microphone Array (Intel Smart Sound)", mode: "quality",
    profile: { status: "A2DP", label: "A2DP — 44100 Hz / 2ch (full quality)", color: "#2FBF71", warning: false },
    model: "small.en (ready)", free_gb: 276.4,
    output_choices: ["Follow Windows default", "Headphones (AirPods)", "Speakers (Realtek(R) Audio)"],
    output_current: "Headphones (AirPods)" }),
  recent: async () => ([
    { folder: "demo1", name: "Project kickoff", created: "2026-07-23 14:01", duration: 1740,
      segments: 150, speakers: 2, has_txt: true, has_speakers: true, has_notes: false },
    { folder: "demo2", name: "Weekly status", created: "2026-07-14 15:00",
      duration: 2110, segments: 210, speakers: 5, has_txt: true, has_speakers: true, has_notes: true },
  ]),
  speakers: async () => ({ known: ["Jordan Lee", "Sarah K"], labels: ["Speaker 1", "Speaker 2"],
    rows: [
      { label: "Speaker 1", sample: "Let's review the rollout status and firewall approvals.",
        suggested: null, confidence: 0.29, snippet: "x.wav" },
      { label: "Speaker 2", sample: "Yeah. We'll reopen it and then we will kick it off again.",
        suggested: "Jordan Lee", confidence: 0.87, snippet: "y.wav" },
    ] }),
  transcript: async () => "[00:00:12] Speaker 1: Let's review the rollout status.\n[00:00:19] Speaker 2: Yeah. We'll reopen it and kick it off again.\n[00:00:26] Me: Sounds good, I'll update the ticket.",
  voices: async () => ([{ name: "Jordan Lee", meetings_seen: 2, samples: 3 },
                        { name: "Sarah K", meetings_seen: 1, samples: 1 }]),
  get_settings: async () => ({ me_name: "Me", capture_mode: "quality",
    output_folder: "C:\\Users\\me\\Documents\\MeetingTranscripts", hotkey: "<ctrl>+<alt>+r",
    diarization_enabled: true, keep_raw: true, notes_enabled: false, notes_backend: "local",
    cloud_allowed: true, gemini_api_key: "", model: "small.en" }),
  model_status: async () => ({ text: "small.en (ready)", ready: true, downloading: false }),
  app_info: async () => ({ version: "demo", needs_setup: false }),
  notes_status: async () => ({ running: false, ok: null, exists: true, backend: "local", model: "gemma3:4b" }),
  notes_text: async () => "## Summary\nDemo notes.\n\n## Action items\n- [ ] **Me** — update the ticket\n",
  copy_text: async () => true,
};
function api(name, ...args) {
  if (API && API[name]) return API[name](...args);
  if (DEMO[name]) return DEMO[name](...args);
  return Promise.resolve(null);
}

/* ---------------- helpers ---------------- */
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const fmtT = (s) => {
  s = Math.max(0, Math.floor(s));
  const h = String(Math.floor(s / 3600)).padStart(2, "0");
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  return `${h}:${m}:${String(s % 60).padStart(2, "0")}`;
};
const fmtDur = (s) => (s ? `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s` : "—");

/* tiny, safe markdown renderer for the notes (escape first, then format) */
function renderMd(md) {
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
  const out = []; let inList = false;
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  md.split(/\r?\n/).forEach((line) => {
    let m;
    if ((m = line.match(/^\s*#{1,2}\s+(.*)/))) { closeList(); out.push(`<h2>${inline(m[1])}</h2>`); }
    else if ((m = line.match(/^\s*#{3,6}\s+(.*)/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); }
    else if ((m = line.match(/^\s*[-*+]\s+\[( |x|X)\]\s+(.*)/))) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li class="task">${m[1].trim() ? "☑" : "☐"} ${inline(m[2])}</li>`);
    } else if ((m = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.*)/))) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (!line.trim()) closeList();
    else { closeList(); out.push(`<p>${inline(line)}</p>`); }
  });
  closeList();
  return out.join("");
}

/* ---------------- app ---------------- */
const app = {
  view: "record",
  meeting: null,          // folder of the open meeting
  meSilentSince: null,

  /* ---- navigation ---- */
  nav(view) {
    this.view = view;
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    ($(`view-${view}`) || $("view-record")).classList.add("active");
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view ||
        (view === "meeting" && b.dataset.view === "meetings")));
    if (view === "meetings") this.loadMeetings();
    if (view === "voices") this.loadVoices();
    if (view === "settings") this.loadSettings();
  },

  toast(msg, kind = "") {
    const t = el("div", `toast ${kind}`, msg);
    $("toasts").appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 4200);
    setTimeout(() => t.remove(), 4700);
  },

  /* ---- record view / status loop ---- */
  async poll() {
    try {
      const s = await api("status");
      if (!s) return;
      this.renderStatus(s);
      if (this.view === "meeting") this.renderMeetingBanner(s.job);
      if (s.state === "idle" && this.view === "record") {
        const pf = await api("preflight");
        if (pf) this.renderPreflight(pf);
      }
    } catch (e) { /* bridge briefly unavailable during startup */ }
  },

  renderStatus(s) {
    const rec = s.state === "recording", paused = s.state === "paused";
    const busy = s.transcribing;
    const job = s.job;
    const busyTxt = job
      ? (job.stage === "transcribing" ? `Transcribing ${Math.round(job.pct || 0)}%`
         : job.stage.charAt(0).toUpperCase() + job.stage.slice(1) + "…")
      : "Transcribing…";
    // sidebar
    const dot = $("sideDot");
    dot.className = "dot" + (rec ? " rec" : paused ? " paused" : busy ? " busy" : "");
    $("sideState").textContent = rec ? "Recording" : paused ? "Paused" : busy ? busyTxt : "Idle";
    $("sideTimer").textContent = fmtT(s.elapsed);
    // hero
    $("bigTimer").textContent = fmtT(s.elapsed);
    const pillDot = document.querySelector("#pill .dot");
    pillDot.className = "dot" + (rec ? " rec" : paused ? " paused" : busy ? " busy" : "");
    $("pillText").textContent = rec ? "Recording" : paused ? "Paused" : busy ? busyTxt : "Idle";
    // controls
    $("btnStart").disabled = rec || paused;  // recording allowed while a job runs in background
    $("btnPause").disabled = !(rec || paused);
    $("btnPause").textContent = paused ? "Resume" : "Pause";
    $("btnStop").disabled = !(rec || paused);
    $("meetingName").disabled = rec || paused;
    $("outputSelect").disabled = rec || paused;
    // meters
    $("meterOthers").style.width = `${Math.round((s.others_meter || 0) * 100)}%`;
    $("meterMe").style.width = s.me_muted ? "0%" : `${Math.round((s.me_meter || 0) * 100)}%`;
    const vo = $("valOthers"), vm = $("valMe");
    if (rec || paused) {
      vo.textContent = s.others_silent ? "SILENT" : "live";
      vo.className = "meter-val" + (s.others_silent ? " warn" : "");
      vm.textContent = s.me_muted ? "MUTED" : s.me_silent ? "SILENT" : "live";
      vm.className = "meter-val" + (s.me_silent && !s.me_muted ? " warn" : "");
    } else { vo.textContent = vm.textContent = "—"; vo.className = vm.className = "meter-val"; }
    if ($("muteSwitch").checked !== !!s.me_muted) $("muteSwitch").checked = !!s.me_muted;
    this.renderBanners(s);
    if (rec || paused) this.renderRecordingDevices(s);
  },

  renderBanners(s) {
    const wrap = $("banners");
    const items = [];
    if (s.profile && s.profile.warning)
      items.push(["amber", "⚠ HFP detected — AirPods are in phone-call mode. Audio is narrowband; " +
        "switch your meeting app's microphone to the laptop mic (quality-first)."]);
    // mic-silent tracking (20s of silence while recording & unmuted)
    if (s.state === "recording" && !s.me_muted && s.me_silent) {
      if (!this.meSilentSince) this.meSilentSince = Date.now();
      const secs = Math.floor((Date.now() - this.meSilentSince) / 1000);
      if (secs >= 20)
        items.push(["red", `⚠ Your mic has been SILENT for ${secs}s — your words are not being ` +
          "recorded. Check Windows default mic / unmute."]);
    } else this.meSilentSince = null;
    const html = items.map(([k, t]) => `<div class="banner ${k}">${t}</div>`).join("");
    if (wrap.innerHTML !== html) wrap.innerHTML = html;
  },

  renderPreflight(pf) {
    const prof = pf.profile;
    $("pfProfile").innerHTML = prof
      ? `<span style="color:${prof.color};font-weight:600">● ${prof.label}</span>` : "—";
    $("pfOthers").textContent = pf.others_device || "—";
    $("pfMe").textContent = `${pf.me_device || "—"}  [${pf.mode}]`;
    $("pfModel").textContent = pf.model || "—";
    $("pfDisk").textContent = pf.free_gb ? `${pf.free_gb.toFixed(1)} GB` : "—";
    const sel = $("outputSelect");
    const choices = pf.output_choices || [];
    if (sel.dataset.sig !== choices.join("|")) {
      sel.dataset.sig = choices.join("|");
      sel.innerHTML = "";
      choices.forEach((c) => sel.appendChild(el("option", "", c)));
    }
    if (sel.value !== pf.output_current && choices.includes(pf.output_current))
      sel.value = pf.output_current;
  },

  renderRecordingDevices(s) {
    $("pfOthers").textContent = s.others_device || "—";
    $("pfMe").textContent = s.me_device || "—";
    const prof = s.profile;
    $("pfProfile").innerHTML = prof
      ? `<span style="color:${prof.color};font-weight:600">● ${prof.label}</span>` : "—";
  },

  /* ---- meetings ---- */
  async loadMeetings() {
    const list = $("meetingList");
    const items = (await api("recent")) || [];
    list.innerHTML = "";
    if (!items.length) { list.appendChild(el("div", "hint", "No meetings yet — record one!")); return; }
    items.forEach((m) => {
      const c = el("div", "mt-card");
      const info = el("div", "mt-info");
      info.appendChild(el("div", "mt-name", m.name));
      const bits = [m.created, fmtDur(m.duration)];
      if (m.segments != null) bits.push(`${m.segments} segments`);
      info.appendChild(el("div", "mt-meta", bits.join("   ·   ")));
      c.appendChild(info);
      if (m.busy) c.appendChild(el("span", "chip", m.has_txt ? "identifying speakers…" : "transcribing…"));
      else if (!m.has_txt && m.has_audio) c.appendChild(el("span", "chip amber", "not transcribed"));
      if (m.speakers) c.appendChild(el("span", "chip", `${m.speakers} speakers`));
      if (m.has_notes) c.appendChild(el("span", "chip green", "notes"));
      c.onclick = () => this.openMeeting(m.folder, m.name);
      list.appendChild(c);
    });
  },

  /* banner on the meeting page: offer recovery / show live progress */
  renderMeetingBanner(job) {
    const b = $("mtBanner");
    if (!b || this.view !== "meeting" || !this.meeting) return;
    const mine = job && job.folder === this.meeting;
    $("btnMtSave").disabled = $("btnReident").disabled = !!mine;
    if (mine) {
      const speakersPhase = /speaker/.test(job.stage);
      const txt = job.stage === "transcribing"
        ? `Transcribing this meeting… ${Math.round(job.pct || 0)}%`
        : speakersPhase ? `Transcript ready — identifying who said what… ${Math.round(job.pct || 0)}%`
        : `${job.stage}…`;
      b.innerHTML = `<div class="banner blue"><span>${txt}</span>
        <div class="progress"><div style="width:${Math.round(job.pct || 0)}%"></div></div></div>`;
      this.wasBusy = true;
      // phase 1 done: show the transcript right away (speaker names fill in after)
      if (speakersPhase && !this.partialShown) {
        this.partialShown = true;
        api("transcript", this.meeting).then((t) => { if (t) $("mtText").value = t; });
      }
      return;
    }
    if (this.wasBusy) {  // just finished -> reload everything for this meeting
      this.wasBusy = false;
      this.partialShown = false;
      this.refreshMeeting();
      return;
    }
    const info = this.mtInfo;
    if (info && !info.has_txt && info.has_audio) {
      b.innerHTML = `<div class="banner amber"><span>This meeting's audio is saved but it hasn't been
        transcribed yet.</span><button class="btn btn-primary sm" id="btnTranscribeNow">Transcribe now</button></div>`;
      $("btnTranscribeNow").onclick = async () => {
        const ok = await api("transcribe_meeting", this.meeting);
        this.toast(ok ? "Transcription started — you can keep using the app" : "Already running", ok ? "ok" : "warn");
      };
    } else b.innerHTML = "";
  },

  async refreshMeeting() {
    const items = (await api("recent")) || [];
    this.mtInfo = items.find((m) => m.folder === this.meeting) || null;
    $("mtText").value = (await api("transcript", this.meeting)) || "";
    const info = this.mtInfo || {};
    $("mtPeople").value = info.people || "";
    $("mtAttendees").value = (info.attendees || []).join(", ");
    $("btnReident").classList.toggle("hidden", !info.can_reidentify);
    this.loadSpeakers();
    this.loadNotes();
    this.renderMeetingBanner(null);
  },

  async openMeeting(folder, name) {
    this.meeting = folder;
    this.wasBusy = false;
    this.partialShown = false;
    this.showTab("transcript");
    $("mtTitle").textContent = name || folder.split("\\").pop();
    this.nav("meeting");
    await this.refreshMeeting();
    if (!$("mtText").value) $("mtText").placeholder = "(no transcript yet)";
  },

  async loadSpeakers() {
    const data = (await api("speakers", this.meeting)) || { rows: [], known: [], labels: [] };
    const wrap = $("speakerRows");
    wrap.innerHTML = "";
    $("spHint").textContent = data.rows.length ? `${data.rows.length} detected` : "none detected";
    const dl = el("datalist"); dl.id = "nameOptions";
    data.known.forEach((n) => { const o = el("option"); o.value = n; dl.appendChild(o); });
    data.labels.forEach((l) => { const o = el("option"); o.value = l; o.label = `merge into ${l}`; dl.appendChild(o); });
    wrap.appendChild(dl);
    data.rows.forEach((r) => {
      const row = el("div", "sp-row");
      const top = el("div", "sp-top");
      top.appendChild(el("span", "sp-label", r.label));
      if (r.snippet) {
        const p = el("button", "play-btn", "▶");
        p.title = "Play a sample of this voice";
        p.onclick = async () => {
          const uri = await api("snippet", this.meeting, r.snippet);
          if (uri) new Audio(uri).play();
        };
        top.appendChild(p);
      }
      if (r.suggested)
        top.appendChild(el("span", "sp-sugg", `${r.suggested} · ${Math.round((r.confidence || 0) * 100)}%`));
      row.appendChild(top);
      row.appendChild(el("div", "sp-sample", `“${r.sample || ""}”`));
      const input = el("input", "input");
      input.placeholder = "Name — or type another Speaker N to merge";
      input.setAttribute("list", "nameOptions");
      input.value = r.named || r.suggested || "";
      input.dataset.label = r.label;
      row.appendChild(input);
      wrap.appendChild(row);
    });
  },

  async suggestNames() {
    const btn = $("btnSuggest"), folder = this.meeting;
    btn.disabled = true; btn.textContent = "✨ Thinking… (local model, ~30-90s)";
    await api("suggest_names", folder);
    const timer = setInterval(async () => {
      const s = await api("suggest_status", folder);
      if (!s || s.running) return;
      clearInterval(timer);
      btn.disabled = false; btn.textContent = "✨ Suggest names from transcript";
      if (this.meeting !== folder) return;  // user navigated away
      if (s.status === "ok")
        this.toast(s.found ? `Suggested ${s.found} name(s) from context` : "No clear names in the dialogue", s.found ? "ok" : "warn");
      else this.toast(s.status || "Name suggestion unavailable", "error");
      this.loadSpeakers();
    }, 2000);
  },

  async applyNames() {
    const mapping = {};
    document.querySelectorAll("#speakerRows input[data-label]").forEach((i) => {
      if (i.value.trim()) mapping[i.dataset.label] = i.value.trim();
    });
    if (!Object.keys(mapping).length) { this.toast("Nothing to apply", "warn"); return; }
    await api("apply_names", this.meeting, mapping);
    this.toast("Names applied — voices remembered for next time", "ok");
    $("mtText").value = (await api("transcript", this.meeting)) || "";
    this.loadSpeakers();
  },

  /* ---- notes (shown in-app) ---- */
  showTab(tab) {
    document.querySelectorAll("#view-meeting .tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.tab === tab));
    $("mtText").classList.toggle("hidden", tab !== "transcript");
    $("notesPane").classList.toggle("hidden", tab !== "notes");
    if (tab === "notes") this.loadNotes();
  },

  async loadNotes() {
    const folder = this.meeting;
    if (!folder) return;
    const st = (await api("notes_status", folder)) || {};
    const text = st.exists ? ((await api("notes_text", folder)) || "") : "";
    if (folder !== this.meeting) return;
    this.notesText = text;
    const body = $("notesBody"), btn = $("btnNotesGen");
    const who = st.backend === "local" ? `local AI (${st.model})` : `Gemini (${st.model})`;
    $("notesBadge").textContent = st.running ? "●" : text ? "✓" : "";
    btn.disabled = !!st.running;
    btn.textContent = st.running ? "Generating…" : text ? "Regenerate" : "Generate notes";
    $("btnNotesCopy").disabled = $("btnNotesOpen").disabled = !text;
    if (st.running) {
      $("notesInfo").textContent = `Writing notes with ${who}… usually 2–4 min for an hour-long call`;
      if (!text) body.innerHTML = `<div class="notes-empty">Generating notes… you can keep using the app.</div>`;
      this.watchNotes(folder);
    } else if (text) {
      $("notesInfo").textContent = `Generated by ${who}`;
    } else {
      $("notesInfo").textContent = st.ok === false ? "Last attempt failed — check Settings → Test connection." : "";
      body.innerHTML = `<div class="notes-empty">No notes yet. Click <b>Generate notes</b> to summarise this meeting.</div>`;
    }
    if (text) body.innerHTML = renderMd(text);
  },

  watchNotes(folder) {
    if (this.notesTimer) return;
    this.notesTimer = setInterval(async () => {
      const n = await api("notes_status", folder);
      if (n && n.running) return;
      clearInterval(this.notesTimer); this.notesTimer = null;
      if (n && (n.ok || n.exists)) this.toast("Notes ready", "ok");
      else this.toast("Notes failed — see Settings → Test connection / logs", "error");
      if (this.meeting === folder) this.loadNotes();
    }, 2000);
  },

  async generateNotes() {
    if (this.notesText && !confirm("Replace the current notes with a fresh version?")) return;
    const ok = await api("generate_notes", this.meeting);
    if (!ok) { this.toast("Notes are already generating…", "warn"); return; }
    this.showTab("notes");
  },

  async copy(text, what) {
    const ok = await api("copy_text", text || "");
    this.toast(ok ? `${what} copied to clipboard` : "Couldn't access the clipboard — try again", ok ? "ok" : "error");
  },

  async saveDetails(silent) {
    const people = parseInt($("mtPeople").value, 10) || 0;
    await api("set_meeting_details", this.meeting, people, $("mtAttendees").value);
    if (!silent) this.toast("Meeting details saved", "ok");
    const items = (await api("recent")) || [];
    this.mtInfo = items.find((m) => m.folder === this.meeting) || null;
    this.loadSpeakers();
  },

  async reidentify() {
    await this.saveDetails(true);
    const people = parseInt($("mtPeople").value, 10) || 0;
    const ok = await api("reidentify", this.meeting, people);
    this.toast(ok ? "Re-identifying speakers… (re-apply names afterwards)"
                  : "Can't re-identify (no saved audio, or a job is already running)", ok ? "" : "warn");
  },

  /* ---- voices ---- */
  async loadVoices() {
    const list = $("voiceList");
    const people = (await api("voices")) || [];
    list.innerHTML = "";
    if (!people.length) {
      list.appendChild(el("div", "hint", "No voices learned yet — name speakers after a meeting to teach the app."));
      return;
    }
    people.forEach((p) => {
      const c = el("div", "voice-card");
      const initials = p.name.split(/[\s,]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
      c.appendChild(el("div", "avatar", initials || "?"));
      const info = el("div", "mt-info");
      info.appendChild(el("div", "mt-name", p.name));
      info.appendChild(el("div", "mt-meta", `seen in ${p.meetings_seen} meeting(s) · ${p.samples} voiceprint(s)`));
      c.appendChild(info);
      const ren = el("button", "btn btn-ghost sm", "Rename");
      ren.onclick = async () => {
        const n = prompt(`Rename "${p.name}" to:`, p.name);
        if (n && n.trim() && n !== p.name) { await api("rename_voice", p.name, n.trim()); this.loadVoices(); }
      };
      const del = el("button", "btn btn-ghost sm danger", "Delete");
      del.onclick = async () => {
        if (confirm(`Forget ${p.name}'s voice?`)) { await api("delete_voice", p.name); this.loadVoices(); }
      };
      c.appendChild(ren); c.appendChild(del);
      list.appendChild(c);
    });
  },

  /* ---- settings ---- */
  async loadSettings() {
    const s = await api("get_settings");
    if (!s) return;
    $("setMe").value = s.me_name; $("setHotkey").value = s.hotkey;
    $("setOut").value = s.output_folder; $("setVocab").value = s.vocabulary || "";
    $("setDiar").checked = s.diarization_enabled;
    $("setKeepRaw").checked = s.keep_raw; $("setNotes").checked = s.notes_enabled;
    $("setCloud").checked = s.cloud_allowed; $("setKey").value = s.gemini_api_key;
    this.setSeg("segMode", s.capture_mode);
    this.setSeg("segBackend", s.notes_backend);
    this.refreshModel();
  },

  setSeg(id, val) {
    document.querySelectorAll(`#${id} button`).forEach((b) =>
      b.classList.toggle("active", b.dataset.v === val));
  },
  getSeg(id) {
    const a = document.querySelector(`#${id} button.active`);
    return a ? a.dataset.v : null;
  },

  async saveSettings() {
    await api("save_settings", {
      me_name: $("setMe").value, hotkey: $("setHotkey").value,
      output_folder: $("setOut").value, vocabulary: $("setVocab").value,
      capture_mode: this.getSeg("segMode"),
      diarization_enabled: $("setDiar").checked, keep_raw: $("setKeepRaw").checked,
      notes_enabled: $("setNotes").checked, notes_backend: this.getSeg("segBackend"),
      cloud_allowed: $("setCloud").checked, gemini_api_key: $("setKey").value,
    });
    $("saveMsg").textContent = "✓ Saved";
    setTimeout(() => ($("saveMsg").textContent = ""), 2500);
  },

  async refreshModel() {
    const m = await api("model_status");
    if (!m) return;
    $("modelStatus").textContent = m.text;
    const b = $("btnModel");
    b.disabled = m.ready || m.downloading;
    b.textContent = m.ready ? "✓ Installed" : m.downloading ? "Downloading…" : "Download model";
    if (m.downloading) setTimeout(() => this.refreshModel(), 2000);
  },
};

/* ---------------- wiring ---------------- */
function wire() {
  document.querySelectorAll(".nav-item").forEach((b) =>
    (b.onclick = () => app.nav(b.dataset.view)));

  $("btnStart").onclick = async () => {
    const ok = await api("start", $("meetingName").value.trim(),
                         parseInt($("recPeople").value, 10) || 0, $("recAttendees").value.trim());
    if (ok) {
      $("meetingName").value = $("recPeople").value = $("recAttendees").value = "";
      $("recDetails").open = false;
    }
  };
  $("btnPause").onclick = () => api("toggle_pause");
  $("btnStop").onclick = () => api("stop");
  $("muteSwitch").onchange = (e) => api("set_mute", e.target.checked);
  $("outputSelect").onchange = (e) => api("set_output_device", e.target.value);
  // re-scan devices when the user reaches for the dropdown (AirPods paired after launch)
  let lastScan = 0;
  $("outputSelect").onmousedown = async () => {
    if (Date.now() - lastScan < 3000) return;
    lastScan = Date.now();
    const pf = await api("refresh_devices");
    if (pf) app.renderPreflight(pf);
  };

  $("btnOpenFolder").onclick = () => api("open_output_folder");
  $("btnBack").onclick = () => app.nav("meetings");
  $("btnMtFolder").onclick = () => api("open_path", app.meeting);
  $("btnMtCopy").onclick = () => app.copy($("mtText").value, "Transcript");
  $("btnMtSave").onclick = async () => {
    await api("save_transcript", app.meeting, $("mtText").value);
    app.toast("Transcript saved", "ok");
  };
  $("btnMtRename").onclick = async () => {
    const n = prompt("New meeting name:", $("mtTitle").textContent);
    if (n && n.trim()) {
      app.meeting = await api("rename_meeting", app.meeting, n.trim());
      $("mtTitle").textContent = n.trim();
      app.toast("Meeting renamed", "ok");
    }
  };
  $("btnMtNotes").onclick = () => app.showTab("notes");
  document.querySelectorAll("#view-meeting .tab").forEach((t) => (t.onclick = () => app.showTab(t.dataset.tab)));
  $("btnNotesGen").onclick = () => app.generateNotes();
  $("btnNotesCopy").onclick = () => app.copy(app.notesText, "Notes");
  $("btnNotesOpen").onclick = () => api("open_path", app.meeting + "\\notes.md");
  $("btnSaveDetails").onclick = () => app.saveDetails(false);
  $("btnReident").onclick = () => app.reidentify();
  $("btnSuggest").onclick = () => app.suggestNames();
  $("btnApplyNames").onclick = () => app.applyNames();

  document.querySelectorAll(".seg").forEach((seg) =>
    seg.querySelectorAll("button").forEach((b) =>
      (b.onclick = () => { seg.querySelectorAll("button").forEach((x) => x.classList.remove("active")); b.classList.add("active"); })));
  $("btnSaveSettings").onclick = () => app.saveSettings();
  $("btnModel").onclick = async () => { await api("download_model"); app.refreshModel(); };
  $("btnTestNotes").onclick = async () => {
    await app.saveSettings();
    $("testResult").textContent = "Testing…";
    const r = await api("test_notes");
    $("testResult").textContent = r ? (r.ok ? `✓ ${r.msg}` : `✗ ${r.msg}`) : "✗ no bridge";
    $("testResult").style.color = r && r.ok ? "var(--green)" : "var(--red)";
  };
}

function boot() {
  API = window.pywebview ? window.pywebview.api : null;
  wire();
  app.poll();
  setInterval(() => app.poll(), 300);
  api("preflight").then((pf) => pf && app.renderPreflight(pf));
  api("app_info");  // boot beacon (logged Python-side)
}

window.addEventListener("pywebviewready", boot);
// plain-browser preview (no bridge): boot with demo data
setTimeout(() => { if (!API && !window.pywebview) boot(); }, 400);
