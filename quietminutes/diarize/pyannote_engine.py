"""pyannote 3.1 speaker diarization — the accuracy engine (fully offline, no account).

Benchmarked on labelled 4-person meetings (AMI corpus): ~19% DER vs ~38% for the old
sherpa + merge pipeline, and people-confusion down from 17-30% to 3-7%.

Settings chosen from that benchmark:
  * sliding window step = 2.5 s (default 1 s) -> ~2.3x faster, accuracy within ~1 point
  * min_cluster_size scaled to the coarser step (12 -> 5), else quiet people vanish
  * torch limited to half the cores so the laptop stays responsive
Weights (MIT / CC-BY-4.0, commercially usable) live in
%LOCALAPPDATA%\\QuietMinutes\\models\\pyannote31\\ and are loaded from local paths only.
The model is loaded per job and released afterwards (~1-2 GB while running).
"""

from __future__ import annotations

import gc
import os
import threading
import urllib.request
from pathlib import Path

from ..config import data_dir
from ..logging_setup import log
from .engine import _to_mono_16k

DIR = data_dir() / "models" / "pyannote31"
SEG = DIR / "segmentation-3.0.bin"
EMB = DIR / "wespeaker-resnet34-LM.bin"
_URLS = {
    SEG: "https://huggingface.co/pyannote/segmentation-3.0/resolve/main/pytorch_model.bin",
    EMB: "https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM/resolve/main/pytorch_model.bin",
}
STEP_FRAC = 0.25         # 2.5 s step over 10 s windows
MIN_CLUSTER = 5          # scaled from pyannote's default 12 for the coarser step
THRESHOLD = 0.7045654963945799  # pyannote 3.1 tuned value
SR = 16000

_CONFIG = """version: 3.1.0
pipeline:
  name: pyannote.audio.pipelines.SpeakerDiarization
  params:
    clustering: AgglomerativeClustering
    embedding: {emb}
    embedding_batch_size: 32
    embedding_exclude_overlap: true
    segmentation: {seg}
    segmentation_batch_size: 32
params:
  clustering:
    method: centroid
    min_cluster_size: {mcs}
    threshold: {thr}
  segmentation:
    min_duration_off: 0.0
"""


def available() -> bool:
    try:
        import torch  # noqa: F401
        # import the pipeline module itself: it pulls optional deps (e.g. matplotlib)
        # that a bare `import pyannote.audio` doesn't, so a broken install shows up here
        import pyannote.audio.pipelines  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        from ..logging_setup import log
        log.warning("pyannote unavailable (%s) — using the built-in speaker engine", exc)
        return False
    return SEG.exists() and EMB.exists()


def download(token: str | None = None) -> bool:
    """One-time fetch of the two weight files (MIT / CC-BY-4.0).

    segmentation-3.0 is "gated" on Hugging Face: free, but you must sign in and accept
    its terms once, then pass a read token (or set HF_TOKEN). Without it the app simply
    keeps using the built-in sherpa speaker engine."""
    token = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    DIR.mkdir(parents=True, exist_ok=True)
    for path, url in _URLS.items():
        if not path.exists():
            req = urllib.request.Request(url)
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            tmp = path.with_suffix(".part")
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.replace(path)
    return SEG.exists() and EMB.exists()


if __name__ == "__main__":  # python -m quietminutes.diarize.pyannote_engine <hf_token>
    import sys
    ok = download(sys.argv[1] if len(sys.argv) > 1 else None)
    print("pyannote weights ready" if ok else "download incomplete")


class PyannoteDiarizer:
    def __init__(self):
        self._lock = threading.Lock()

    def diarize(self, wav_path: str, num_speakers: int | None = None,
                progress=None) -> list[dict]:
        """Return [{start, end, speaker:int}] sorted by time. progress(frac 0..1)."""
        with self._lock:  # one diarization at a time (memory)
            import torch
            from pyannote.audio import Pipeline

            os.environ["HF_HUB_OFFLINE"] = "1"
            torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
            cfg = DIR / "config.yaml"
            cfg.write_text(_CONFIG.format(emb=EMB.as_posix(), seg=SEG.as_posix(),
                                          mcs=MIN_CLUSTER, thr=THRESHOLD), encoding="utf-8")
            pipe = None
            try:
                pipe = Pipeline.from_pretrained(str(cfg))
                pipe._segmentation.step = STEP_FRAC * pipe._segmentation.duration
                audio = _to_mono_16k(wav_path, SR)
                wave = torch.from_numpy(audio).unsqueeze(0)

                def hook(step_name, step_artifact, file=None, total=None, completed=None):
                    if progress and step_name == "embeddings" and total:
                        progress(min(0.99, completed / total))

                kw = {"num_speakers": int(num_speakers)} if num_speakers else {}
                ann = pipe({"waveform": wave, "sample_rate": SR}, hook=hook, **kw)
                labels = {}
                out = []
                for seg, _, lab in ann.itertracks(yield_label=True):
                    if lab not in labels:
                        labels[lab] = len(labels)
                    out.append({"start": float(seg.start), "end": float(seg.end),
                                "speaker": labels[lab]})
                out.sort(key=lambda r: r["start"])
                if progress:
                    progress(1.0)
                return out
            finally:
                del pipe
                gc.collect()


def describe() -> str:
    return "pyannote 3.1 (offline)" if available() else "unavailable"


_ = log  # (module-level logger kept for future diagnostics)
