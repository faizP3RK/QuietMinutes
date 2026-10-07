"""Headless smoke test for the Phase 1 capture core (no GUI).

Records a few seconds with a quiet self-test tone playing to the default output so
the 'Others' loopback has signal, exercises pause/resume, then stops and checks both
WAVs are wall-clock aligned and non-silent. Run:

    .venv\\Scripts\\python.exe -u smoke_capture.py
"""

from __future__ import annotations

import math
import os
import struct
import tempfile
import threading
import time
import wave

import pyaudiowpatch as pyaudio
import soundfile as sf

from quietminutes.audio.capture import DualCapture
from quietminutes.config import Config


def play_tone(stop_evt: threading.Event) -> None:
    try:
        import winsound
    except Exception:  # noqa: BLE001
        return
    sr, amp = 44100, 0.15
    buf = bytearray()
    for i in range(sr):  # 1s loopable
        buf += struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * 440 * i / sr)))
    path = os.path.join(tempfile.gettempdir(), "smoke_tone.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(buf))
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
    stop_evt.wait()
    winsound.PlaySound(None, 0)


def main() -> int:
    cfg = Config.load()
    cfg.output_folder = os.path.join(os.path.dirname(__file__), "phase1_smoke_out")
    p = pyaudio.PyAudio()
    cap = DualCapture(p, cfg)

    stop_tone = threading.Event()
    threading.Thread(target=play_tone, args=(stop_tone,), daemon=True).start()
    time.sleep(0.3)

    folder = cap.start()
    print(f"recording -> {folder}")
    print(f"  Others: {cap.others.dev.name}  ({cap.others.sr} Hz / {cap.others.ch}ch)")
    print(f"  Me    : {cap.me.dev.name}  ({cap.me.sr} Hz / {cap.me.ch}ch)")
    print(f"  Profile: {cap.others_profile.status} - {cap.others_profile.label}")

    for _ in range(30):  # ~3s recording
        time.sleep(0.1)
        s = cap.status()
        print(f"  t={s['elapsed']:4.1f}s  others_meter={s['others_meter']:.2f} "
              f"me_meter={s['me_meter']:.2f}  state={s['state']}", end="\r")
    print()

    print("  pausing 1s ...")
    cap.toggle_pause()
    time.sleep(1.0)
    cap.toggle_pause()
    print("  resumed; recording ~2s more ...")
    time.sleep(2.0)

    stop_tone.set()
    result = cap.stop()
    p.terminate()

    print("\nRESULT:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    ok = True
    for label, path in [("others", result["others_wav"]), ("me", result["me_wav"])]:
        info = sf.info(path)
        data, _ = sf.read(path)
        peak = float(abs(data).max()) if data.size else 0.0
        print(f"  [{label}] {info.samplerate} Hz {info.channels}ch "
              f"{info.duration:.2f}s  peak={peak:.4f}")
        if info.duration < 4.0:  # ~3s + ~2s active (pause excluded) => >=4s
            print(f"    !! {label} duration too short"); ok = False
    o_peak = float(abs(sf.read(result["others_wav"])[0]).max())
    if o_peak < 0.01:
        print("    !! Others track silent (tone not captured)"); ok = False

    print("\nSMOKE TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
