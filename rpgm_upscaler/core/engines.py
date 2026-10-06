"""Upscaling engines: Pillow resamplers (always available) and optional ncnn-vulkan AI binaries."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .imageops import bleed_rgb

PILLOW_ENGINES = ("lanczos", "nearest", "sharp")
NCNN_ENGINES = {"realesrgan": "realesrgan-ncnn-vulkan", "waifu2x": "waifu2x-ncnn-vulkan"}
RESAMPLERS = ("default",) + PILLOW_ENGINES


class EngineError(RuntimeError):
    pass


def _resize_alpha_safe(img: Image.Image, w: int, h: int, method: int) -> Image.Image:
    return img.resize((w, h), method)  # Pillow premultiplies RGBA internally for non-NEAREST


class PillowEngine:
    def __init__(self, mode: str = "lanczos"):
        if mode not in PILLOW_ENGINES:
            raise EngineError(f"unknown resampler {mode}")
        self.mode = mode

    def resize(self, img: Image.Image, w: int, h: int, resampler: str | None = None) -> Image.Image:
        mode = resampler if resampler in PILLOW_ENGINES else self.mode
        if (w, h) == img.size:
            return img.copy()
        if mode == "nearest":
            return img.resize((w, h), Image.NEAREST)
        if mode == "sharp":
            f = max(math.ceil(w / img.width), math.ceil(h / img.height), 1)
            big = img.resize((img.width * f, img.height * f), Image.NEAREST)
            return big if (w, h) == big.size else _resize_alpha_safe(big, w, h, Image.BOX if f > 1 else Image.LANCZOS)
        return _resize_alpha_safe(img, w, h, Image.LANCZOS)


class NcnnEngine:
    """realesrgan-ncnn-vulkan / waifu2x-ncnn-vulkan wrapper. Alpha is scaled separately."""

    def __init__(self, kind: str, exe: str | None = None, model: str | None = None, gpu: int | None = None):
        if kind not in NCNN_ENGINES:
            raise EngineError(f"unknown engine {kind}")
        self.kind = kind
        self.exe = exe or shutil.which(NCNN_ENGINES[kind])
        if not self.exe:
            raise EngineError(f"{NCNN_ENGINES[kind]} not found")
        self.model = model or ("realesr-animevideov3-x4" if kind == "realesrgan" else "models-cunet")
        self.gpu = gpu
        self.scale = 4 if kind == "realesrgan" else 2
        self.fallback = PillowEngine("lanczos")

    def _run(self, src: Path, dst: Path, scale: int) -> None:
        if self.kind == "realesrgan":
            cmd = [self.exe, "-i", str(src), "-o", str(dst), "-s", str(scale), "-n", self.model]
        else:
            cmd = [self.exe, "-i", str(src), "-o", str(dst), "-s", str(scale), "-n", "0", "-m", self.model]
        if self.gpu is not None:
            cmd += ["-g", str(self.gpu)]
        cwd = str(Path(self.exe).parent)
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        if r.returncode != 0 or not dst.is_file():
            raise EngineError(f"{self.kind} failed: {(r.stderr or r.stdout).strip()[:300]}")

    def enlarge(self, img: Image.Image) -> tuple[Image.Image, int]:
        s = self.scale
        alpha = img.getchannel("A")
        solid = bleed_rgb(img).convert("RGB")
        with tempfile.TemporaryDirectory(prefix="rpgmup_") as td:
            a, b = Path(td, "in.png"), Path(td, "out.png")
            solid.save(a)
            self._run(a, b, s)
            big = Image.open(b).convert("RGB")
            big.load()
        if big.size != (img.width * s, img.height * s):
            big = big.resize((img.width * s, img.height * s), Image.LANCZOS)
        a_big = alpha.resize(big.size, Image.NEAREST if set(alpha.tobytes()) <= {0, 255} else Image.LANCZOS)
        out = big.convert("RGBA")
        out.putalpha(a_big)
        return out, s

    def resize(self, img: Image.Image, w: int, h: int, resampler: str | None = None) -> Image.Image:
        if resampler in PILLOW_ENGINES:
            return self.fallback.resize(img, w, h, resampler)
        big, _ = self.enlarge(img)
        return big.resize((w, h), Image.LANCZOS)

    def probe(self) -> None:
        test = Image.new("RGBA", (16, 16), (200, 50, 50, 255))
        self.enlarge(test)


def detect_engines(extra_paths: dict[str, str] | None = None) -> dict[str, tuple[bool, str]]:
    """Return {engine: (available, detail)}."""
    out: dict[str, tuple[bool, str]] = {e: (True, "built in") for e in PILLOW_ENGINES}
    for kind, binary in NCNN_ENGINES.items():
        exe = (extra_paths or {}).get(kind) or shutil.which(binary)
        if exe and os.access(exe, os.X_OK):
            out[kind] = (True, exe)
        else:
            out[kind] = (False, f"{binary} not found on PATH")
    return out


def make_engine(name: str, path: str | None = None, model: str | None = None):
    if name in PILLOW_ENGINES:
        return PillowEngine(name)
    return NcnnEngine(name, path, model)
