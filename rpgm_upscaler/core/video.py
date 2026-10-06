"""Movie scaling via ffmpeg."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

VIDEO_EXTS = {".webm", ".mp4", ".m4v", ".ogv"}


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe_size(path: Path) -> tuple[int, int] | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        s = json.loads(r.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except Exception:  # noqa: BLE001
        return None


def _encoders() -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


def target_size(w: int, h: int, target: tuple[int, int]) -> tuple[int, int]:
    f = min(target[0] / w, target[1] / h)
    if f <= 1:
        return w, h
    return int(w * f) // 2 * 2, int(h * f) // 2 * 2


def scale_video(src: Path, dst: Path, target: tuple[int, int], proc_holder: list | None = None,
                cancel: threading.Event | None = None) -> None:
    size = probe_size(src)
    if not size:
        raise RuntimeError(f"cannot read video size of {src.name}")
    tw, th = target_size(*size, target)
    enc = _encoders()
    ext = src.suffix.lower()
    if ext == ".webm":
        codec = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-cpu-used", "4"] \
            if "libvpx-vp9" in enc else ["-c:v", "libvpx", "-crf", "10", "-b:v", "4M"]
    elif ext == ".ogv":
        codec = ["-c:v", "libtheora", "-q:v", "8"]
    else:
        codec = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp" + ext)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-vf", f"scale={tw}:{th}:flags=lanczos", *codec, "-c:a", "copy", str(tmp)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc_holder is not None:
        proc_holder.append(p)
    err = p.communicate()[1]
    if cancel is not None and cancel.is_set():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("cancelled")
    if p.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed for {src.name}: {err.strip()[:300]}")
    tmp.replace(dst)
