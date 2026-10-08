"""Upscaling engines: Pillow resamplers (always available) and optional ncnn-vulkan AI binaries."""
from __future__ import annotations

import hashlib
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
# Models each AI engine ships with, default first. Pillow engines have no models.
NCNN_MODELS = {
    "realesrgan": ("realesr-animevideov3-x4", "realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-general-x4v3"),
    "waifu2x": ("models-cunet", "models-upconv_7_anime_style_art_rgb", "models-upconv_7_photo"),
}
RESAMPLERS = ("default",) + PILLOW_ENGINES


class EngineError(RuntimeError):
    pass


def _runnable(p: Path) -> str | None:
    return str(p) if p.is_file() and os.access(p, os.X_OK) else None


def find_binary(binary: str, explicit: str | None = None) -> str | None:
    """Locate an executable. Besides a plain PATH lookup this copes with what a GUI launched from a desktop menu sees
    differently from a terminal: `~` entries in PATH, a PATH that only the user's login/interactive shell sets up, and
    the usual install folders. `explicit` may be the binary itself or the folder holding it."""
    if explicit:
        p = Path(explicit).expanduser()
        return _runnable(p / binary) if p.is_dir() else _runnable(p)
    found = shutil.which(binary)
    if found:
        return found
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and (hit := _runnable(Path(d).expanduser() / binary)):
            return hit
    home = Path.home()
    for d in (home / ".local/bin", home / "bin", Path("/usr/local/bin"), Path("/opt/homebrew/bin"), Path("/snap/bin"),
              home / ".local/share/flatpak/exports/bin", Path("/var/lib/flatpak/exports/bin")):
        if hit := _runnable(d / binary):
            return hit
    shell = os.environ.get("SHELL")
    if shell and os.name == "posix":
        try:
            r = subprocess.run([shell, "-l", "-i", "-c", f"command -v {binary}"], capture_output=True, text=True,
                               timeout=5, stdin=subprocess.DEVNULL)
            for line in reversed(r.stdout.splitlines()):
                if line.startswith("/") and (hit := _runnable(Path(line.strip()))):
                    return hit
        except (OSError, subprocess.SubprocessError):
            pass
    return None


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
    """realesrgan-ncnn-vulkan / waifu2x-ncnn-vulkan wrapper. Alpha is scaled separately.

    Starting the binary costs more than a small image takes (it initialises Vulkan and loads the model), so `prefetch` runs a
    whole batch of images through one process in directory mode, and each image only uses the smallest model scale that
    reaches the size it needs instead of always x4 followed by a downscale."""

    def __init__(self, kind: str, exe: str | None = None, model: str | None = None, gpu: int | None = None):
        if kind not in NCNN_ENGINES:
            raise EngineError(f"unknown engine {kind}")
        self.kind = kind
        self.exe = find_binary(NCNN_ENGINES[kind], exe)
        if not self.exe:
            raise EngineError(f"{NCNN_ENGINES[kind]} not found")
        self.model = model or ("realesr-animevideov3-x4" if kind == "realesrgan" else "models-cunet")
        self.gpu = gpu
        if kind == "waifu2x":
            self.scales = (2, 4)
        elif "animevideov3" in self.model or "general" in self.model:      # these ship x2, x3 and x4 networks
            self.scales = (2, 3, 4)
        else:                                                               # realesrgan-x4plus(-anime) is x4 only
            self.scales = (4,)
        self.scale = max(self.scales)
        self.fallback = PillowEngine("lanczos")
        self._ready: dict[tuple[bytes, int], Image.Image] = {}

    def pick_scale(self, want: float | None) -> int:
        """Smallest network scale that is at least `want` (the biggest one if none is)."""
        if not want:
            return self.scale
        return next((s for s in self.scales if s >= want - 1e-6), self.scale)

    def _run(self, src: Path, dst: Path, scale: int) -> None:
        """`src` and `dst` are both files or both directories (the binary then converts every image in it)."""
        if self.kind == "realesrgan":
            cmd = [self.exe, "-i", str(src), "-o", str(dst), "-s", str(scale), "-n", self.model]
        else:
            cmd = [self.exe, "-i", str(src), "-o", str(dst), "-s", str(scale), "-n", "0", "-m", self.model]
        if self.gpu is not None:
            cmd += ["-g", str(self.gpu)]
        if src.is_dir():
            cmd += ["-f", "png"]
        cwd = str(Path(self.exe).parent)
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        if r.returncode != 0 or not dst.exists():
            raise EngineError(f"{self.kind} failed: {(r.stderr or r.stdout).strip()[:300]}")

    @staticmethod
    def _solid(img: Image.Image) -> Image.Image:
        return bleed_rgb(img).convert("RGB")

    @staticmethod
    def _key(solid: Image.Image) -> bytes:
        return hashlib.blake2b(solid.tobytes() + repr(solid.size).encode(), digest_size=16).digest()

    def prefetch(self, items: list[tuple[Image.Image, float | None]]) -> int:
        """Enlarge many images in one process. Results wait in memory until `enlarge` asks for the same image; anything
        that fails here is simply done one by one later. Returns how many images were enlarged."""
        by_scale: dict[int, list[tuple[bytes, Image.Image]]] = {}
        for img, want in items:
            solid = self._solid(img)
            key, s = self._key(solid), self.pick_scale(want)
            if (key, s) not in self._ready and all(key != k for k, _ in by_scale.get(s, [])):
                by_scale.setdefault(s, []).append((key, solid))
        n = 0
        for s, group in by_scale.items():
            with tempfile.TemporaryDirectory(prefix="rpgmup_") as td:
                src, dst = Path(td, "in"), Path(td, "out")
                src.mkdir(); dst.mkdir()
                for i, (_, solid) in enumerate(group):
                    solid.save(src / f"{i}.png")
                try:
                    self._run(src, dst, s)
                except EngineError:
                    continue
                for i, (key, solid) in enumerate(group):
                    f = dst / f"{i}.png"
                    if f.is_file():
                        big = Image.open(f).convert("RGB")
                        big.load()
                        self._ready[(key, s)] = big
                        n += 1
        return n

    def enlarge(self, img: Image.Image, want: float | None = None) -> tuple[Image.Image, int]:
        """Returns (image enlarged by the returned integer factor, factor). `want` is the factor the caller needs."""
        s = self.pick_scale(want)
        alpha = img.getchannel("A")
        solid = self._solid(img)
        big = self._ready.pop((self._key(solid), s), None)
        if big is None:
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

    def would_enlarge(self, img: Image.Image, w: int, h: int, resampler: str | None = None) -> float | None:
        """The factor `resize` would ask `enlarge` for, or None when it does not use the network (a named resampler, or
        shrinking, where enlarging first would only waste time)."""
        if resampler in PILLOW_ENGINES or (w <= img.width and h <= img.height):
            return None
        return max(w / img.width, h / img.height)

    def resize(self, img: Image.Image, w: int, h: int, resampler: str | None = None) -> Image.Image:
        want = self.would_enlarge(img, w, h, resampler)
        if want is None:
            return self.fallback.resize(img, w, h, resampler)
        big, _ = self.enlarge(img, want)
        return big if big.size == (w, h) else big.resize((w, h), Image.LANCZOS)

    def probe(self) -> None:
        test = Image.new("RGBA", (16, 16), (200, 50, 50, 255))
        self.enlarge(test)


def detect_engines(extra_paths: dict[str, str] | None = None) -> dict[str, tuple[bool, str]]:
    """Return {engine: (available, detail)}."""
    out: dict[str, tuple[bool, str]] = {e: (True, "built in") for e in PILLOW_ENGINES}
    for kind, binary in NCNN_ENGINES.items():
        exe = find_binary(binary, (extra_paths or {}).get(kind))
        if exe:
            out[kind] = (True, exe)
        else:
            out[kind] = (False, f"{binary} not found on PATH")
    return out


def make_engine(name: str, path: str | None = None, model: str | None = None):
    if name in PILLOW_ENGINES:
        return PillowEngine(name)
    return NcnnEngine(name, path, model)
