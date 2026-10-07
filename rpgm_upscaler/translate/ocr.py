"""Find Japanese text in game images (OpenCV + Tesseract), erase it and overlay the English translation.

Everything is local. `Tesseract` needs the `tesseract` binary plus its `jpn` (and optionally `jpn_vert`) language data;
OpenCV is used to prepare images, to build the text mask and to inpaint the old lettering.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .detect import is_japanese, normalize

try:                                   # optional dependency; reported by `ocr_available`
    import cv2
except ImportError:                    # pragma: no cover
    cv2 = None


class OcrError(Exception):
    pass


@dataclass
class TextRegion:
    box: tuple[int, int, int, int]     # x, y, w, h in image pixels
    text: str                          # Japanese text, lines joined
    conf: float = 0.0
    vertical: bool = False
    lines: int = 1
    en: str = ""


class OcrBackend(Protocol):
    def detect(self, img: Image.Image) -> list[TextRegion]: ...


def ocr_available(langs: tuple[str, ...] = ("jpn",)) -> tuple[bool, str]:
    if cv2 is None:
        return False, "OpenCV is missing. Install with: pip install opencv-python-headless"
    exe = shutil.which("tesseract")
    if not exe:
        return False, "the tesseract binary is missing. Install it with your package manager (apt install tesseract-ocr tesseract-ocr-jpn)"
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"tesseract does not run: {e}"
    have = set(out.stdout.split())
    missing = [lang for lang in langs if lang not in have]
    if missing:
        return False, f"tesseract is missing language data: {', '.join(missing)} (apt install tesseract-ocr-jpn)"
    return True, ""


# ---------------------------------------------------------------------------------------------------------------------
# detection


def _flatten(img: Image.Image) -> np.ndarray:
    """RGBA -> grayscale with transparent pixels shown as mid-grey so both light and dark lettering stand out."""
    rgba = np.asarray(img.convert("RGBA"), dtype=np.float32)
    a = rgba[..., 3:4] / 255.0
    rgb = rgba[..., :3] * a + 128.0 * (1 - a)
    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)


def _variants(gray: np.ndarray) -> list[np.ndarray]:
    """Dark-on-light renderings of the same picture: as is, inverted, and an adaptive threshold for busy backgrounds."""
    inv = 255 - gray
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12)
    return [gray, inv, thr, 255 - thr]


class Tesseract:
    """Horizontal (and optionally vertical) Japanese text via the tesseract CLI, with image preprocessing."""

    def __init__(self, min_conf: float = 55.0, vertical: bool = True, scale: float | None = None, timeout: int = 60):
        ok, why = ocr_available(("jpn",))
        if not ok:
            raise OcrError(why)
        self.exe = shutil.which("tesseract")
        self.min_conf, self.vertical, self.fixed_scale, self.timeout = min_conf, vertical, scale, timeout
        self.have_vert = ocr_available(("jpn_vert",))[0]

    def _run(self, gray: np.ndarray, lang: str, psm: int) -> list[dict]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "in.png"
            cv2.imwrite(str(p), gray)
            r = subprocess.run([self.exe, str(p), "stdout", "-l", lang, "--psm", str(psm), "-c", "preserve_interword_spaces=0", "tsv"],
                               capture_output=True, text=True, timeout=self.timeout)
        rows = []
        for rec in csv.DictReader(io.StringIO(r.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
            try:
                if int(rec["level"]) == 5 and rec["text"].strip() and float(rec["conf"]) >= 0:
                    rows.append({"b": int(rec["block_num"]), "p": int(rec["par_num"]), "l": int(rec["line_num"]),
                                 "x": int(rec["left"]), "y": int(rec["top"]), "w": int(rec["width"]), "h": int(rec["height"]),
                                 "t": rec["text"], "c": float(rec["conf"])})
            except (KeyError, ValueError):
                continue
        return rows

    def _lines(self, rows: list[dict], s: float) -> list[TextRegion]:
        by: dict[tuple, list[dict]] = {}
        for r in rows:
            by.setdefault((r["b"], r["p"], r["l"]), []).append(r)
        out: list[TextRegion] = []
        for ws in by.values():
            text = normalize("".join(w["t"] for w in sorted(ws, key=lambda w: w["x"]))).replace(" ", "")
            ja = sum(1 for ch in text if is_japanese(ch))
            if ja == 0 or ja < 0.5 * len(text):
                continue
            conf = sum(w["c"] for w in ws) / len(ws)
            if conf < self.min_conf:
                continue
            x0, y0 = min(w["x"] for w in ws), min(w["y"] for w in ws)
            x1, y1 = max(w["x"] + w["w"] for w in ws), max(w["y"] + w["h"] for w in ws)
            out.append(TextRegion((int(x0 / s), int(y0 / s), max(1, int((x1 - x0) / s)), max(1, int((y1 - y0) / s))), text, conf))
        return out

    def detect(self, img: Image.Image) -> list[TextRegion]:
        gray = _flatten(img)
        h, w = gray.shape
        if min(h, w) < 12:
            return []
        s = self.fixed_scale or (3.0 if max(h, w) <= 400 else 2.0 if max(h, w) <= 1400 else 1.0)
        found: list[TextRegion] = []
        for v in _variants(gray):
            big = cv2.resize(v, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s != 1.0 else v
            big = cv2.copyMakeBorder(big, 20, 20, 20, 20, cv2.BORDER_REPLICATE)
            rows = self._run(big, "jpn", 11)
            for r in rows:
                r["x"] -= 20; r["y"] -= 20
            found += self._lines(rows, s)
        if self.vertical and self.have_vert and h > w * 1.2 and not found:
            big = cv2.copyMakeBorder(cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC), 20, 20, 20, 20, cv2.BORDER_REPLICATE)
            rows = self._run(big, "jpn_vert", 5)
            for r in rows:
                r["x"] -= 20; r["y"] -= 20
            for reg in self._lines(rows, s):
                reg.vertical = True
                found.append(reg)
        return merge_regions(dedupe(found))


def _iou(a, b) -> float:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix, iy = max(0, min(ax + aw, bx + bw) - max(ax, bx)), max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter) if inter else 0.0


def dedupe(regions: list[TextRegion]) -> list[TextRegion]:
    """The same lettering is usually found by several image variants; keep the most confident reading of each spot."""
    keep: list[TextRegion] = []
    for r in sorted(regions, key=lambda r: -r.conf):
        if all(_iou(r.box, k.box) < 0.4 for k in keep):
            keep.append(r)
    return keep


def merge_regions(regions: list[TextRegion]) -> list[TextRegion]:
    """Join text lines that stack into one paragraph, so the translator sees whole sentences."""
    regs = sorted([r for r in regions], key=lambda r: (r.box[1], r.box[0]))
    out: list[TextRegion] = []
    for r in regs:
        for g in out:
            gx, gy, gw, gh = g.box; x, y, w, h = r.box
            lh = gh / max(1, g.lines)
            vgap = y - (gy + gh)
            aligned = abs(x - gx) <= 1.5 * lh or abs((x + w / 2) - (gx + gw / 2)) <= 1.5 * lh
            if not g.vertical and not r.vertical and -0.3 * lh <= vgap <= 0.9 * lh and aligned and abs(h - lh) <= 0.6 * lh:
                nx, ny = min(gx, x), gy
                g.box = (nx, ny, max(gx + gw, x + w) - nx, max(gy + gh, y + h) - ny)
                g.text += r.text
                g.conf = (g.conf * g.lines + r.conf) / (g.lines + 1)
                g.lines += 1
                break
        else:
            out.append(TextRegion(r.box, r.text, r.conf, r.vertical, r.lines))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# fonts and drawing

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
]


def find_font(preferred: str | Path | None = None) -> str | None:
    for c in ([str(preferred)] if preferred else []) + _FONT_CANDIDATES:
        if Path(c).is_file():
            return c
    return None


def _font(path: str | None, size: int) -> ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                    # old Pillow
        return ImageFont.load_default()


def _wrap_px(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}" if cur else word
            if cur and draw.textlength(trial, font=font) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines


def fit_text(draw: ImageDraw.ImageDraw, text: str, box_w: int, box_h: int, font_path: str | None, max_size: int) -> tuple[list[str], ImageFont.ImageFont]:
    size = max(8, min(max_size, box_h))
    while True:
        f = _font(font_path, size)
        lines = _wrap_px(draw, text, f, box_w)
        asc, desc = f.getmetrics() if hasattr(f, "getmetrics") else (size, 0)
        total = int(len(lines) * (asc + desc) * 1.05)
        widest = max((draw.textlength(ln, font=f) for ln in lines), default=0)
        if (total <= box_h and widest <= box_w) or size <= 8:
            return lines, f
        size -= 1


def _fg_mask(crop_gray: np.ndarray) -> np.ndarray:
    """Pixels belonging to the lettering inside a crop: Otsu, picking the polarity with the smaller foreground."""
    _, a = cv2.threshold(crop_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b = 255 - a
    m = a if (a > 0).mean() <= (b > 0).mean() else b
    k = max(2, min(crop_gray.shape) // 12)
    return cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))


def overlay_translation(img: Image.Image, regions: list[TextRegion], font_path: str | None = None) -> Image.Image:
    """Erase each region's Japanese text (inpainting) and draw its `.en` in the same place."""
    img = img.convert("RGBA")
    arr = np.array(img)
    W, H = img.size
    plans = []
    for r in regions:
        if not r.en:
            continue
        pad = max(2, r.box[3] // 8 // max(1, r.lines))
        x0, y0 = max(0, r.box[0] - pad), max(0, r.box[1] - pad)
        x1, y1 = min(W, r.box[0] + r.box[2] + pad), min(H, r.box[1] + r.box[3] + pad)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        crop = arr[y0:y1, x0:x1]
        rgb = np.ascontiguousarray(crop[..., :3])
        alpha = crop[..., 3]
        gray = _flatten(Image.fromarray(crop))
        mask = _fg_mask(gray)
        ring = np.ones(mask.shape, bool)
        ring[mask > 0] = False
        bg_alpha = int(np.median(alpha[ring])) if ring.any() else 255
        bg_rgb = np.median(rgb[ring], axis=0) if ring.any() else np.array([0, 0, 0])
        fg = mask > 0
        fg_rgb = np.median(rgb[fg], axis=0) if fg.any() else np.array([255, 255, 255])
        fixed = cv2.inpaint(rgb, mask, 3, cv2.INPAINT_TELEA)
        crop[..., :3] = fixed
        crop[..., 3] = np.where(fg, bg_alpha, alpha)
        arr[y0:y1, x0:x1] = crop
        plans.append((r, fg_rgb, bg_rgb))
    out = Image.fromarray(arr)
    for r, fg_rgb, bg_rgb in plans:
        x, y, w, h = r.box
        layer_w, layer_h = (h, w) if r.vertical else (w, h)
        grow = int(layer_w * 0.25)                                  # English runs longer than Japanese
        lw = min(layer_w + grow, (H if r.vertical else W))
        layer = Image.new("RGBA", (lw, layer_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        lines, f = fit_text(d, r.en, lw, layer_h, font_path, max_size=max(10, int(layer_h / max(1, r.lines) * 1.0)))
        lum = 0.299 * fg_rgb[0] + 0.587 * fg_rgb[1] + 0.114 * fg_rgb[2]
        stroke = (0, 0, 0, 255) if lum > 128 else (255, 255, 255, 255)
        asc, desc = f.getmetrics() if hasattr(f, "getmetrics") else (f.size, 0)
        lh = int((asc + desc) * 1.05)
        ty = max(0, (layer_h - lh * len(lines)) // 2)
        centered = abs((x + w / 2) - W / 2) < W * 0.08
        for ln in lines:
            tw = d.textlength(ln, font=f)
            tx = (lw - tw) / 2 if centered else 0
            d.text((tx, ty), ln, font=f, fill=tuple(int(c) for c in fg_rgb) + (255,), stroke_width=max(1, f.size // 14), stroke_fill=stroke)
            ty += lh
        if r.vertical:
            layer = layer.rotate(-90, expand=True)
            out.alpha_composite(layer, (max(0, min(W - layer.width, x + w // 2 - layer.width // 2)), max(0, y)))
        else:
            px = x - (lw - w) // 2 if centered else x
            out.alpha_composite(layer, (max(0, min(W - lw, px)), y))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# which images to scan

_TEXT_LIKELY_MV = {"pictures", "titles1", "titles2", "system"}
_TEXT_LIKELY_RGSS = {"pictures", "titles1", "titles2", "system"}
_SKIP_NAMES = ("window", "iconset", "balloon", "shadow", "loading", "gameover_bg", "damage")
IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".png_", ".rpgmvp"}


def iter_images(root: Path, kind: str, scope: str = "likely") -> list[Path]:
    """Image files to scan. kind: 'html5' (img/) or 'rgss' (Graphics/); scope: 'likely' (pictures, titles, system) or 'all'."""
    base = root / "img" if kind == "html5" else next((p for p in root.iterdir() if p.is_dir() and p.name.lower() == "graphics"), root / "Graphics")
    if not base.is_dir():
        return []
    likely = _TEXT_LIKELY_MV if kind == "html5" else _TEXT_LIKELY_RGSS
    out: list[Path] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMG_EXTS:
            continue
        top = p.relative_to(base).parts[0].lower() if len(p.relative_to(base).parts) > 1 else "system"
        if scope != "all" and (top not in likely or p.stem.lower().startswith(_SKIP_NAMES)):
            continue
        out.append(p)
    return out
