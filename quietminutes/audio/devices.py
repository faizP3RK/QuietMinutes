"""WASAPI device enumeration + selection helpers.

Wraps a PyAudio (pyaudiowpatch) instance. Knows how to:
  - find the loopback device for the current default render endpoint,
  - pick the "Me" microphone for each capture mode,
  - flag virtual/processed endpoints (Krisp/VB-Cable/VoiceMeeter) so we never
    capture the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyaudiowpatch as pyaudio

VIRTUAL_MARKERS = ("krisp", "vb-audio", "vb audio", "cable", "voicemeeter", "virtual")
# Substrings that identify the laptop's built-in mic (quality-first "Me" source).
BUILTIN_MIC_MARKERS = ("microphone array", "realtek", "digital microphone", "smart sound")
# AirPods / Bluetooth headset mic markers (convenience mode).
HEADSET_MIC_MARKERS = ("airpod", "hands-free", "headset", "bluetooth")


def is_virtual(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in VIRTUAL_MARKERS)


@dataclass
class Dev:
    index: int
    name: str
    rate: int
    channels: int
    is_loopback: bool

    @property
    def virtual(self) -> bool:
        return is_virtual(self.name)


def _to_dev(info: dict, *, input_channels: bool) -> Dev:
    ch = info["maxInputChannels"] if input_channels else info["maxOutputChannels"]
    return Dev(
        index=info["index"],
        name=info["name"],
        rate=int(info["defaultSampleRate"]),
        channels=int(ch) or 1,
        is_loopback=bool(info.get("isLoopbackDevice", False)),
    )


def wasapi_info(p: pyaudio.PyAudio) -> dict:
    return p.get_host_api_info_by_type(pyaudio.paWASAPI)


def list_loopbacks(p: pyaudio.PyAudio) -> list[Dev]:
    return [_to_dev(d, input_channels=True) for d in p.get_loopback_device_info_generator()]


def list_input_mics(p: pyaudio.PyAudio) -> list[Dev]:
    out = []
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
            out.append(_to_dev(d, input_channels=True))
    return out


def default_output_name(p: pyaudio.PyAudio) -> str:
    return p.get_device_info_by_index(wasapi_info(p)["defaultOutputDevice"])["name"]


def loopback_label(dev: Dev) -> str:
    """Friendly name for the UI (strips the ' [Loopback]' suffix pyaudiowpatch adds)."""
    return dev.name.replace(" [Loopback]", "").strip()


def loopback_by_name(p: pyaudio.PyAudio, name: str) -> Dev | None:
    """Find a loopback device whose (friendly) name matches `name`. Name-based so it
    survives device-index changes across reconnects. Returns None if not present."""
    if not name:
        return None
    n = name.lower()
    for lb in list_loopbacks(p):
        if n in lb.name.lower() or n in loopback_label(lb).lower():
            return lb
    return None


def default_loopback(p: pyaudio.PyAudio) -> Dev | None:
    """Loopback device matching the current default render endpoint."""
    default_out = p.get_device_info_by_index(wasapi_info(p)["defaultOutputDevice"])
    if default_out.get("isLoopbackDevice", False):
        return _to_dev(default_out, input_channels=True)
    for lb in list_loopbacks(p):
        if default_out["name"] in lb.name:
            return lb
    loops = list_loopbacks(p)
    return loops[0] if loops else None


def default_input(p: pyaudio.PyAudio) -> Dev | None:
    try:
        info = p.get_device_info_by_index(wasapi_info(p)["defaultInputDevice"])
    except Exception:  # noqa: BLE001
        try:
            info = p.get_default_input_device_info()
        except Exception:  # noqa: BLE001
            return None
    return _to_dev(info, input_channels=True)


def _first_match(mics: list[Dev], markers: tuple[str, ...]) -> Dev | None:
    for m in mics:
        n = m.name.lower()
        if any(k in n for k in markers) and not m.virtual:
            return m
    return None


def pick_builtin_mic(p: pyaudio.PyAudio) -> Dev | None:
    """Quality-first 'Me' source: the real laptop built-in mic, never virtual."""
    mics = list_input_mics(p)
    return _first_match(mics, BUILTIN_MIC_MARKERS) or next(
        (m for m in mics if not m.virtual), None
    )


def pick_headset_mic(p: pyaudio.PyAudio) -> Dev | None:
    """Convenience mode 'Me' source: the AirPods/Bluetooth headset mic (HFP)."""
    mics = list_input_mics(p)
    return _first_match(mics, HEADSET_MIC_MARKERS) or default_input(p)


def mic_for_mode(p: pyaudio.PyAudio, mode: str, explicit_index: int | None) -> Dev | None:
    if explicit_index is not None:
        for m in list_input_mics(p):
            if m.index == explicit_index:
                return m
    if mode == "convenience":
        return pick_headset_mic(p)
    return pick_builtin_mic(p)
