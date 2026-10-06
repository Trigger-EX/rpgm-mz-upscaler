"""Image loading (with decryption), sprite-sheet aware resizing, saving (with re-encryption)."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from . import crypto

GUTTER = 2


def load_image(path: Path, key: bytes | None = None) -> Image.Image:
    data = path.read_bytes()
    if path.suffix.lower() in crypto.ENCRYPTED_IMAGE_EXTS:
        if key is None:
            raise crypto.CryptoError(f"{path.name}: encrypted but no key available")
        data = crypto.decrypt(data, key)
        if not crypto.looks_like_png(data):
            raise crypto.CryptoError(f"{path.name}: decrypted data is not a PNG (wrong key?)")
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGBA")


def save_image(img: Image.Image, dst: Path, key: bytes | None = None, encrypt: bool = False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    data = buf.getvalue()
    if encrypt:
        if key is None:
            raise crypto.CryptoError("cannot encrypt without key")
        data = crypto.encrypt(data, key)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dst)


def bleed_rgb(img: Image.Image, iterations: int = 8) -> Image.Image:
    """Fill RGB of fully transparent pixels from neighbours so AI engines see no black fringes."""
    a = np.asarray(img.convert("RGBA")).copy()
    alpha = a[..., 3]
    known = alpha > 0
    if known.all() or not known.any():
        return Image.fromarray(a, "RGBA")
    rgb = a[..., :3].astype(np.float32)
    for _ in range(iterations):
        if known.all():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape, np.float32)
        kf = known.astype(np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            sk = np.roll(kf, (dy, dx), (0, 1))
            acc += np.roll(rgb * kf[..., None], (dy, dx), (0, 1))
            cnt += sk
        fill = (~known) & (cnt > 0)
        rgb[fill] = acc[fill] / cnt[fill][:, None]
        known = known | fill
    a[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return Image.fromarray(a, "RGBA")


def upscale_image(img: Image.Image, engine, target: tuple[int, int] | None, n: float,
                  cell: tuple[int, int] | None, resampler: str | None = None) -> Image.Image:
    """Resize `img`. With `cell`, each grid cell is resized independently to round(cell*n) so that
    no colour bleeds across cells and cols*cell' stays exact. `engine.enlarge` (AI engines) is run
    once over a gutter-padded atlas of all cells."""
    from .scaling import scaled
    if cell is None:
        tw, th = target if target else (scaled(img.width, n), scaled(img.height, n))
        return engine.resize(img, tw, th, resampler)
    cw, ch = cell
    cols, rows = img.width // cw, img.height // ch
    tcw, tch = scaled(cw, n), scaled(ch, n)
    out = Image.new("RGBA", (cols * tcw, rows * tch))
    if hasattr(engine, "enlarge") and (resampler in (None, "default")):
        g = GUTTER
        # atlas of padded cells laid out in the same grid
        pw, ph = cw + 2 * g, ch + 2 * g
        atlas = np.zeros((rows * ph, cols * pw, 4), np.uint8)
        arr = np.asarray(img)
        for r in range(rows):
            for c in range(cols):
                cellarr = np.pad(arr[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw], ((g, g), (g, g), (0, 0)), mode="edge")
                atlas[r * ph:(r + 1) * ph, c * pw:(c + 1) * pw] = cellarr
        big, s = engine.enlarge(Image.fromarray(atlas, "RGBA"))
        for r in range(rows):
            for c in range(cols):
                box = ((c * pw + g) * s, (r * ph + g) * s, (c * pw + g + cw) * s, (r * ph + g + ch) * s)
                piece = big.crop(box).resize((tcw, tch), Image.LANCZOS)
                out.paste(piece, (c * tcw, r * tch))
    else:
        for r in range(rows):
            for c in range(cols):
                piece = img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
                out.paste(engine.resize(piece, tcw, tch, resampler), (c * tcw, r * tch))
    return out
