"""
Phase 0 de-risk probe for QuietMinutes.

Throwaway diagnostic. Proves WASAPI loopback capture works on THIS machine and
reveals the live AirPods Bluetooth profile (A2DP vs HFP) before any real code is
written. Run it during or right after a real call.

What it does:
  1. Enumerates WASAPI render (loopback) + input (mic) devices, flagging virtual
     endpoints (Krisp / VB-Cable / VoiceMeeter) so we don't capture the wrong thing.
  2. Inspects the DEFAULT render endpoint's mix format (sample rate + channels)
     and decides A2DP (clean) vs HFP (narrowband phone-call) vs Wired/other.
  3. Records ~5s of loopback ("Others") and ~5s of the default mic ("Me") to WAVs.
  4. Prints per-track peak / RMS levels (silent capture is the worst failure mode).
  5. Prints a verdict block summarizing everything.

No admin. No driver. Writes only to ./phase0_out/.
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

RECORD_SECONDS = 5
CHUNK = 1024
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase0_out")

# Substrings that mark a virtual / processed endpoint we must NOT mistake for the
# real hardware render device.
VIRTUAL_MARKERS = ("krisp", "vb-audio", "vb audio", "cable", "voicemeeter", "virtual")


def is_virtual(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in VIRTUAL_MARKERS)


def dbfs(x: float) -> str:
    if x <= 0:
        return "-inf dBFS"
    return f"{20 * math.log10(x):6.1f} dBFS"


def classify_profile(sample_rate: int, channels: int) -> tuple[str, str]:
    """Return (label, marker) from the render endpoint mix format."""
    if sample_rate <= 16000 or channels < 2:
        return ("HFP (narrowband / phone-call)", "[!] AMBER")
    if sample_rate >= 44100 and channels >= 2:
        return ("A2DP / full-quality stereo", "[OK] GREEN")
    return ("unclear", "[?]")


def section(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def describe(dev: dict) -> str:
    tag = "  <-- VIRTUAL/PROCESSED" if is_virtual(dev["name"]) else ""
    return (
        f"  idx {dev['index']:>3} | {dev['name'][:48]:<48} | "
        f"in:{dev['maxInputChannels']} out:{dev['maxOutputChannels']} | "
        f"{int(dev['defaultSampleRate'])} Hz{tag}"
    )


def record_stream(p, device_index: int, channels: int, sample_rate: int, label: str):
    """Callback-mode capture for ~RECORD_SECONDS bounded by a WALL CLOCK, so it can
    never hang on an idle/silent WASAPI loopback endpoint (blocking read does).
    Returns np.ndarray (frames, ch) or None on failure."""
    frames: list[bytes] = []

    def callback(in_data, frame_count, time_info, status):  # noqa: ANN001
        frames.append(in_data)
        return (None, pyaudio.paContinue)

    try:
        stream = p.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=sample_rate,
            frames_per_buffer=CHUNK,
            input=True,
            input_device_index=device_index,
            stream_callback=callback,
        )
    except Exception as exc:  # noqa: BLE001 - probe must never hard-crash
        print(f"  !! could not open {label} stream: {exc}")
        return None

    print(f"  recording {label} for ~{RECORD_SECONDS}s (wall-clock bounded) ...")
    try:
        stream.start_stream()
        deadline = time.time() + RECORD_SECONDS
        while time.time() < deadline:
            time.sleep(0.1)
    except Exception as exc:  # noqa: BLE001
        print(f"  !! error while capturing {label}: {exc}")
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:  # noqa: BLE001
            pass

    if not frames:
        print(f"  (no callback buffers arrived for {label} -- endpoint was idle)")
        return None
    data = np.frombuffer(b"".join(frames), dtype=np.float32)
    if channels > 1:
        usable = (len(data) // channels) * channels
        data = data[:usable].reshape(-1, channels)
    return data


def play_test_tone(seconds: int, stop_evt: threading.Event) -> None:
    """Self-test only: play a quiet 440 Hz tone through the DEFAULT render endpoint
    (winsound, stdlib) so the loopback has something real to capture without a live
    call. Low amplitude on purpose. Runs in a thread until stop_evt is set."""
    try:
        import winsound  # Windows stdlib
    except Exception:  # noqa: BLE001
        print("  (winsound unavailable; skipping self-test tone)")
        return
    sr = 44100
    amp = 0.15  # gentle - this goes into your AirPods
    n = sr * seconds
    buf = bytearray()
    for i in range(n):
        s = int(amp * 32767 * math.sin(2 * math.pi * 440 * i / sr))
        buf += struct.pack("<h", s)
    path = os.path.join(tempfile.gettempdir(), "phase0_tone.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(buf))
    flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP
    winsound.PlaySound(path, flags)
    stop_evt.wait()
    winsound.PlaySound(None, 0)  # stop


def levels(data: np.ndarray) -> tuple[float, float]:
    if data is None or data.size == 0:
        return (0.0, 0.0)
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    return (peak, rms)


def main() -> int:
    ap = argparse.ArgumentParser(description="QuietMinutes Phase 0 de-risk probe")
    ap.add_argument(
        "--play-tone",
        action="store_true",
        help="self-test: play a quiet tone into the default output so loopback has "
        "signal, even without a live call (you'll hear it in your AirPods)",
    )
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    p = pyaudio.PyAudio()
    verdict_lines = []

    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        print("WASAPI host API not available on this machine. Aborting.")
        p.terminate()
        return 2

    # ----- 1. Enumerate -----------------------------------------------------
    section("1. WASAPI loopback (render) endpoints")
    loopbacks = list(p.get_loopback_device_info_generator())
    for d in loopbacks:
        print(describe(d))

    section("2. Input devices (microphones)")
    inputs = []
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
            inputs.append(d)
            print(describe(d))

    # ----- 2. Default render endpoint + profile -----------------------------
    section("3. Default render endpoint -> Bluetooth profile")
    default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    print(f"  default output: {default_out['name']}")

    loop_dev = None
    if default_out.get("isLoopbackDevice", False):
        loop_dev = default_out
    else:
        for lb in loopbacks:
            if default_out["name"] in lb["name"]:
                loop_dev = lb
                break
    if loop_dev is None and loopbacks:
        loop_dev = loopbacks[0]
        print("  (default not matched to a loopback; falling back to first loopback)")

    if loop_dev is None:
        print("  !! no loopback device found at all. Aborting capture test.")
        p.terminate()
        return 2

    lb_rate = int(loop_dev["defaultSampleRate"])
    lb_ch = int(loop_dev["maxInputChannels"])
    profile_label, profile_marker = classify_profile(lb_rate, lb_ch)
    print(f"  loopback device: {loop_dev['name']}")
    print(f"  mix format     : {lb_rate} Hz, {lb_ch} ch")
    print(f"  PROFILE        : {profile_marker}  {profile_label}")
    if is_virtual(loop_dev["name"]):
        print("  !! WARNING: default render endpoint looks VIRTUAL (e.g. Krisp).")
        print("     Real meeting audio may be routed through it; pick the hardware")
        print("     AirPods/Headphones endpoint for production capture.")

    # ----- 3. Record loopback ("Others") ------------------------------------
    section("4. Record loopback ('Others' track)")
    tone_thread = None
    tone_stop = threading.Event()
    if args.play_tone:
        print("  --play-tone: emitting a quiet 440 Hz self-test tone to default output")
        tone_thread = threading.Thread(
            target=play_test_tone, args=(RECORD_SECONDS + 1, tone_stop), daemon=True
        )
        tone_thread.start()
        time.sleep(0.3)  # let playback ramp up before capture
    others = record_stream(p, loop_dev["index"], lb_ch, lb_rate, "loopback")
    if tone_thread is not None:
        tone_stop.set()
        tone_thread.join(timeout=2)
    others_path = os.path.join(OUT_DIR, "phase0_others_loopback.wav")
    o_peak, o_rms = levels(others)
    print(f"  peak {o_peak:.4f} ({dbfs(o_peak)})   rms {o_rms:.4f} ({dbfs(o_rms)})")
    if others is not None:
        sf.write(others_path, others, lb_rate, subtype="PCM_16")
        print(f"  saved -> {others_path}")
    else:
        print("  (nothing captured -- no WAV written)")

    # ----- 4. Record default mic ("Me") -------------------------------------
    section("5. Record default mic ('Me' track)")
    try:
        default_in = p.get_device_info_by_index(wasapi["defaultInputDevice"])
    except Exception:  # noqa: BLE001
        default_in = p.get_default_input_device_info()
    mic_rate = int(default_in["defaultSampleRate"])
    mic_ch = min(int(default_in["maxInputChannels"]), 1)  # mono mic is enough
    print(f"  default mic    : {default_in['name']}  ({mic_rate} Hz)")
    if is_virtual(default_in["name"]):
        print("  !! NOTE: default mic looks VIRTUAL (Krisp). In quality-first mode")
        print("     we'll pin the real laptop built-in mic instead.")
    me = record_stream(p, default_in["index"], mic_ch, mic_rate, "mic")
    me_path = os.path.join(OUT_DIR, "phase0_me_mic.wav")
    m_peak, m_rms = levels(me)
    print(f"  peak {m_peak:.4f} ({dbfs(m_peak)})   rms {m_rms:.4f} ({dbfs(m_rms)})")
    if me is not None:
        sf.write(me_path, me, mic_rate, subtype="PCM_16")
        print(f"  saved -> {me_path}")
    else:
        print("  (nothing captured -- no WAV written)")

    # ----- 5. Verdict -------------------------------------------------------
    section("VERDICT")
    sig = lambda peak: "SIGNAL OK" if peak > 0.002 else "SILENT (!!)"
    verdict_lines.append(f"  Loopback device : {loop_dev['name']}")
    verdict_lines.append(f"  Bluetooth profile: {profile_marker}  {profile_label}")
    verdict_lines.append(f"  Others track    : {sig(o_peak)}  (peak {dbfs(o_peak)})")
    verdict_lines.append(f"  Me / mic device : {default_in['name']}")
    verdict_lines.append(f"  Me track        : {sig(m_peak)}  (peak {dbfs(m_peak)})")
    for line in verdict_lines:
        print(line)
    print("\n  Listen-check the two WAVs in ./phase0_out/ to confirm they sound right.")
    if o_peak <= 0.002:
        print("\n  >> Others track was SILENT. Make sure audio was actually playing")
        print("     (a YouTube video or a live call) during the 5s window, then re-run.")

    p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
