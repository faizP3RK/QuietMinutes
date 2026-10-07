# QuietMinutes

**A free meeting transcriber for Windows that runs entirely on your own PC.**

QuietMinutes records what you hear in a meeting (Teams, Zoom, Google Meet, VDI/AVD, or anything
else playing through your speakers or headphones) along with your own microphone. Afterwards it
gives you a transcript with speaker labels and, if you want, AI-written meeting notes with action
items. It all runs locally, and the audio stays on your machine.

### Why I built this

I spend a lot of time in meetings, and writing up minutes and action items afterwards eats into
the day. The tools that do this well are mostly paid subscriptions, and many send your audio to
the cloud. That's a hard sell for work calls. I wanted something free that kept everything on
my own laptop and didn't need admin rights to install, so I built it. I'm sharing it in case
it's useful to someone else too.

- 🎙️ **Two-track capture.** "Others" comes from WASAPI loopback of your output device and "Me" from your mic, so your own lines are always labelled correctly.
- 📝 **On-device transcription** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Audio is transcribed while you talk, so the transcript is ready seconds after you press Stop.
- 👥 **Who said what.** Offline speaker diarization ([pyannote 3.1](https://github.com/pyannote/pyannote-audio), with a built-in fallback) assigns speakers **word by word**. You can optionally set how many people were on the call.
- 🧠 **It learns voices.** Name a speaker once and the app suggests that name automatically in later meetings. Only small numeric voiceprints are stored, never audio.
- ✨ **AI notes** (summary, action items, decisions, open questions) from a **local** LLM via [Ollama](https://ollama.com). Free and offline. Gemini cloud is available as an explicit opt-in.
- 🔒 **No admin needed** to install or run. There are no drivers and no virtual audio devices, which makes it suitable for locked-down work laptops.
- 🛟 **Safety nets.** It warns you about a silent mic, detects Bluetooth "phone-call mode" (HFP), reconnects dropped devices, and can recover a meeting from its saved audio.

> ⚠️ **Recording consent:** laws in many places require the consent of everyone being recorded, and
> many employers have their own policies. **Tell participants you're recording and follow your local laws
> and workplace rules.** See [Responsible use](#responsible-use).

## 🛡️ Local-first by design

I work in cybersecurity, so privacy was the starting point rather than an afterthought. The goal
was simple: your meetings should stay yours. Here's how the app tries to live up to that.

- **Your PC does the work.** Speech-to-text (Whisper), speaker detection (pyannote) and AI notes
  (Gemma via Ollama on `localhost`) all run on your own CPU. There's no server and no account to
  sign up for.
- **Network use is small, and you can check it yourself.** The code makes three kinds of outbound
  request, all of them in the source:
  1. a **one-time model download** when you click *Download model*. After that the models load with
     `HF_HUB_OFFLINE=1` and never contact the internet again,
  2. **`localhost`** calls to Ollama for notes, which never leave the machine,
  3. **Gemini notes, only if you opt in.** The code refuses to send unless you turn on *"I allow
     sending transcript TEXT to the cloud"*, and even then it sends text only, never audio.
- **No telemetry, analytics or auto-upload.**
- **API keys are encrypted.** If you use the optional Gemini key, it's stored with Windows **DPAPI**,
  so only your Windows account can read it, and it's kept out of the logs.
- **No admin rights needed.** There are no drivers or background services. Everything lives in your
  user profile.
- **It fails gently.** If notes or the cloud aren't available, you still get your transcript.

It's a personal project rather than an audited product, so if you spot something that could be
safer, issues and pull requests are very welcome.

---

## How it works

```
 Meeting app audio ──► WASAPI loopback ──► others.wav ─┐
 Your microphone ────► WASAPI capture ───► me.wav ─────┤
                                                       ▼
                      faster-whisper (live, rolling chunks, word timestamps)
                                                       ▼
          Transcript ready ──► speaker diarization of "Others" (pyannote / sherpa-onnx)
                                                       ▼
            word-level speaker labels ──► voiceprint matching (local SQLite)
                                                       ▼
          transcript.txt / .srt / .md  ──►  optional AI notes (Ollama or Gemini) ──► notes.md
```

| Layer | Tech |
|---|---|
| UI | HTML/CSS/JS in an Edge **WebView2** window via `pywebview`, plus a tray icon and a global hotkey |
| Capture | `PyAudioWPatch` (WASAPI loopback + mic), with a per-track watchdog that reconnects lost devices |
| Speech-to-text | `faster-whisper` `small.en` (int8, CPU) with a custom-vocabulary prompt |
| Speakers | `pyannote.audio` 3.1 (CPU, local weights) or `sherpa-onnx`, voice embeddings, cosine matching |
| Notes | Ollama (`gemma3:4b` recommended), map-reduce over long transcripts. Gemini REST is opt-in |
| Storage | Plain files per meeting in `Documents\MeetingTranscripts\`. Settings and voiceprints in `%LOCALAPPDATA%\QuietMinutes\` |

---

## Requirements

- **Windows 10 or 11** (64-bit). The Microsoft Edge WebView2 runtime comes preinstalled on Windows 11.
- About **4 GB** of free disk space for the libraries and models, and **8 GB+ RAM** (16 GB recommended for speaker detection on long calls).
- An internet connection **for setup only**. After that it runs fully offline.
- No admin rights, no GPU.

## Install (one time, about 5 minutes)

1. **Download the code.** On this GitHub page click the green **Code** button, then **Download ZIP**. Unzip it somewhere, for example `Documents\QuietMinutes`. (Or use `git clone`.)
2. **Double-click `setup.bat`.** It installs everything into the app's folder and your user profile:
   [uv](https://docs.astral.sh/uv/), a private Python 3.12, and all libraries. You don't need to install anything by hand.
3. **Double-click `QuietMinutes.bat`** to start the app. Tip: right-click it, then *Send to → Desktop (create shortcut)*.
4. On first launch, go to **Settings → Download model**. This is a one-time download of the speech and speaker models.

That's it. Press **Start** before your meeting and **Stop** after.

### Optional: the more accurate speaker engine (pyannote)

Out of the box, QuietMinutes uses its built-in speaker engine. pyannote 3.1 was noticeably more
accurate in my tests: on a few recordings from the public AMI meeting dataset, the error rate went
from about 38% to about 20%. One of its models is free but "gated", so it needs a one-time Hugging
Face sign-in:

1. Create a free account at [huggingface.co](https://huggingface.co) and open
   [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0). Accept the terms.
2. Create a **read** token under *Settings → Access Tokens*.
3. In the app folder, open PowerShell and run:
   ```powershell
   $env:HF_TOKEN = "hf_your_token_here"
   .venv\Scripts\python.exe -m quietminutes.diarize.pyannote_engine
   ```
4. Restart the app. pyannote is used automatically from then on. The token is not stored anywhere.

### Optional: free local AI notes

1. Install [Ollama](https://ollama.com/download). It installs per-user, no admin needed.
2. Open a terminal and run `ollama pull gemma3:4b` (about 3 GB).
3. In **Settings → AI notes**, turn notes on and choose **local**. Click **Test connection** to check it works.

On a typical laptop CPU, notes for an hour-long call take about 3–4 minutes in the background.

---

## Using it

- **Record:** optionally type a meeting name, then click **Start** (or press `Ctrl+Alt+R` from any app).
  Live meters show both tracks are being captured. The **Me** row has a mute switch.
- **Meeting details (optional):** enter how many people are on the call and paste the attendee names
  from the invite. The count locks the number of speakers. The names show up first when you label
  speakers and are passed to the notes model.
- **After the call:** the transcript appears first, then the speakers are identified. Open the meeting to:
  - ▶ listen to a short sample of each voice and type a name (or pick an attendee),
  - **✨ Suggest names**, which uses the local AI to infer names from how people address each other,
  - **🔄 Re-identify speakers** after changing the people count,
  - read, copy or regenerate the **AI notes** on their own tab.
- **Pick the output device** (for example Headphones or Speakers) in the Record page's pre-flight panel if
  "Others" stays silent. This matters on VDI/AVD.

**Best quality setup:** if you use Bluetooth headphones such as AirPods, use your laptop's built-in
mic in the meeting app and the headphones **for output only**. Using the headphone mic switches them
to low-quality "phone-call" mode (HFP), which hurts both transcription and speaker detection. The app
warns you when this happens.

### Output

Each meeting gets its own folder in `Documents\MeetingTranscripts\<date_time - name>\`:
`transcript.txt`, `transcript.srt`, `transcript.md`, `notes.md` (if notes are enabled), and, by default,
the raw audio (`others.wav`, `me.wav`) so you can re-run speaker detection later. You can turn off
keeping the raw audio in Settings.

---

## Privacy & security

- **Local by default.** Audio, transcripts, voiceprints and notes stay on your PC. The app has no
  telemetry and no accounts.
- **Cloud is opt-in only.** The Gemini notes backend sends **transcript text only**, and only after you
  turn on "I allow sending transcript TEXT to the cloud". Your API key is encrypted on disk with Windows
  DPAPI (per-user).
- **Network use** is limited to: one-time model downloads, `localhost` calls to Ollama, and Gemini
  if you enable it.
- Logs (`logs\`) contain app events (which can include speaker names), not transcripts or audio. Meeting data is excluded from this repository by `.gitignore`.

## Responsible use

QuietMinutes is a personal productivity tool. **You** are responsible for using it lawfully:

- **Get consent.** Many jurisdictions (for example several US states, the EU/UK under GDPR, and Canada)
  require the consent of everyone recorded. Announce that you are recording, or use your meeting
  platform's recording notice.
- **Follow your organisation's policies** on recording meetings and on handling confidential data.
- **Don't** use it to record people secretly or where recording is prohibited.

This software is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)). The authors are
not liable for how it is used. AI-generated notes and speaker labels can be wrong, so always review them
before relying on or sharing them.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Others" meter stays flat | Choose the device your meeting audio actually plays on in **Capture output**, then check the meter |
| Red "mic SILENT" banner | Check the Windows default microphone and that you aren't muted |
| Amber HFP warning | Your headset mic is in use. Switch the meeting app's mic to the laptop mic |
| Wrong number of speakers | Open the meeting, set **People on the call**, then click **🔄 Re-identify speakers** |
| Notes fail | **Settings → AI notes → Test connection**. For local notes, make sure Ollama is installed and `gemma3:4b` is pulled |
| Anything else | See `logs\quietminutes-YYYY-MM-DD.log` (one file per day) |

## Project structure

```
quietminutes/
  app.py              controller: recording lifecycle, background jobs, finalize pipeline
  audio/              WASAPI capture, device selection, meters, Bluetooth profile detection
  transcribe/         faster-whisper engine, rolling live transcription, batch fallback, writers
  diarize/            pyannote + sherpa engines, word-level labelling, voiceprint DB
  notes/              Ollama / Gemini backends, map-reduce summarisation
  naming.py           LLM name suggestions (restricted to attendees when provided)
  webui/              pywebview window, JS⇄Python bridge (api.py), static HTML/CSS/JS
  ui/                 tray, hotkey, classic fallback UI
setup.bat             one-click no-admin installer
QuietMinutes.bat      launcher (no console window)
smoke_*.py            headless smoke tests for each subsystem
```

**Developers:** run from source with `.venv\Scripts\python.exe -m quietminutes` (add `--classic` for the
fallback UI). Settings live in `%LOCALAPPDATA%\QuietMinutes\config.json`. For example,
`diar_engine` is `"pyannote"` or `"sherpa"`, `ollama_model` sets the notes model, and `vocabulary`
lists terms Whisper should spell correctly.

## Thanks

This app is mostly glue around some excellent open-source work: faster-whisper, pyannote,
sherpa-onnx, Ollama, pywebview and others (full list in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). I built it with a lot of help from
[Claude Code](https://claude.com/claude-code) as a pair programmer.

Feedback, bug reports and ideas are all welcome. Just open an issue.

## License

[MIT](LICENSE) © 2026 Faizan Shaikh. Third-party libraries and models keep their own licenses.
