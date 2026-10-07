# Third-party software and models

QuietMinutes' own code is MIT-licensed (see [LICENSE](LICENSE)). It does **not** bundle
any third-party code or model weights in this repository: libraries are installed from
PyPI by `setup.bat`, and models are downloaded by the user at first run. Each remains under
its own license, summarised below for convenience. Always check the upstream project for the
authoritative terms.

## Libraries (installed from PyPI)

| Project | Used for | License |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / CTranslate2 | on-device speech-to-text | MIT |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) | speaker diarization (default engine) | MIT |
| [PyTorch](https://pytorch.org) | runs the pyannote models (CPU) | BSD-3-Clause |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | built-in fallback diarization + voice embeddings | Apache-2.0 |
| [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) | WASAPI loopback + mic capture | MIT |
| [pywebview](https://github.com/r0x0r/pywebview) | desktop window (Edge WebView2) | BSD-3-Clause |
| [pystray](https://github.com/moses-palmer/pystray) | system tray icon | LGPL-3.0 |
| [pynput](https://github.com/moses-palmer/pynput) | global hotkey | LGPL-3.0 |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | classic fallback UI | MIT |
| NumPy, soundfile, Pillow, matplotlib, huggingface_hub | audio/arrays/images/model loading | BSD / BSD / HPND / PSF-based / Apache-2.0 |

LGPL libraries are used unmodified, as separately installed packages.

## Models (downloaded by the user, never redistributed here)

| Model | Used for | License / terms |
|---|---|---|
| OpenAI Whisper `small.en` (CTranslate2 conversion by Systran) | transcription | MIT |
| [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) | speaker turn detection | MIT (gated: accept terms on Hugging Face) |
| [pyannote/wespeaker-voxceleb-resnet34-LM](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM) | speaker embeddings | **CC-BY-4.0** — attribution: WeSpeaker (Wang et al., 2023), packaged by pyannote |
| pyannote speaker-diarization-3.1 pipeline settings | clustering parameters | MIT |
| sherpa-onnx pyannote-segmentation-3-0 + 3D-Speaker CAM++ | fallback diarization | MIT / Apache-2.0 |
| Optional: Gemma 3 4B via [Ollama](https://ollama.com) | local AI notes | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) (pulled by the user with Ollama) |
| Optional: Google Gemini API | cloud AI notes (opt-in) | Google API terms (user's own key) |

"Teams", "Zoom", "AirPods", "Windows" and other product names are trademarks of their
respective owners. This project is not affiliated with or endorsed by any of them.
