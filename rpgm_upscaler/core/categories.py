"""Folder rules: how each img/ folder is scaled and how its sprite sheets are laid out."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

EXACT, COVER, COPY = "exact", "cover", "copy"

# folder -> (policy, default resampler)
FOLDER_RULES: dict[str, tuple[str, str]] = {
    "animations": (EXACT, "default"),
    "battlebacks1": (COVER, "default"),
    "battlebacks2": (COVER, "default"),
    "characters": (EXACT, "default"),
    "enemies": (EXACT, "default"),
    "faces": (EXACT, "default"),
    "parallaxes": (EXACT, "default"),
    "pictures": (EXACT, "default"),
    "sv_actors": (EXACT, "default"),
    "sv_enemies": (EXACT, "default"),
    "system": (EXACT, "default"),
    "tilesets": (EXACT, "default"),
    "titles1": (COVER, "default"),
    "titles2": (COVER, "default"),
    "effects/Texture": (EXACT, "default"),
}
KNOWN_FOLDERS = list(FOLDER_RULES)

# system sheets: name -> (cell w, cell h) in source pixels
SYSTEM_CELLS = {
    "iconset": (32, 32), "balloon": (48, 48), "states": (96, 96),
    "weapons1": (96, 64), "weapons2": (96, 64), "weapons3": (96, 64),
    "buttonset": (48, 48),
}
# system images that must never be resized (hard-coded pixel coordinates in the engine)
WINDOWSKIN_NAMES = {"window"}


@dataclass(frozen=True)
class Category:
    name: str          # e.g. "characters"
    policy: str        # exact | cover | copy
    known: bool = True


def category_for(rel_under_web: PurePosixPath) -> Category | None:
    """rel path relative to the web root (e.g. img/faces/Actor1.png). None = not an image asset."""
    parts = rel_under_web.parts
    if len(parts) >= 3 and parts[0] == "img":
        folder = parts[1]
        if folder in FOLDER_RULES:
            return Category(folder, FOLDER_RULES[folder][0])
        return Category(folder, EXACT, known=False)
    if len(parts) >= 3 and parts[0] == "effects" and parts[1] == "Texture":
        return Category("effects/Texture", EXACT)
    return None


def tileset_kinds(tileset_names: list[str]) -> dict[str, str]:
    """Map tileset image stem -> slot name (A1..E) using Tilesets.json tilesetNames order."""
    slots = ["A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E"]
    kinds: dict[str, str] = {}
    for tileset in tileset_names:
        for slot, name in zip(slots, tileset):
            if name:
                kinds.setdefault(name, slot)
    return kinds


def _kind_from_name(stem: str) -> str | None:
    tail = stem.rsplit("_", 1)[-1].upper()
    return tail if tail in {"A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E"} else None


def cell_size(cat: str, stem: str, w: int, h: int, tile_kinds: dict[str, str]) -> tuple[int, int] | None:
    """Source cell size for sprite-sheet-aware resizing, or None to resize the image as a whole."""
    def fit(cw: int, ch: int) -> tuple[int, int] | None:
        return (cw, ch) if cw > 0 and ch > 0 and w % cw == 0 and h % ch == 0 else None

    if cat == "characters":
        if "$" in stem:
            return fit(w // 3, h // 4)
        return fit(w // 12, h // 8)
    if cat == "faces":
        return fit(w // 4, h // 2)
    if cat == "sv_actors":
        return fit(w // 9, h // 6)
    if cat == "animations":
        return fit(192, 192) if w % 192 == 0 and h % 192 == 0 else None
    if cat == "tilesets":
        kind = tile_kinds.get(stem) or _kind_from_name(stem)
        if kind in ("A1", "A2", "A3", "A4"):
            return fit(24, 24)
        return fit(48, 48)
    if cat == "system":
        low = stem.lower()
        if low in SYSTEM_CELLS:
            return fit(*SYSTEM_CELLS[low])
        if low == "damage":
            return fit(w // 10, h // 5)
    return None


def is_windowskin(cat: str, stem: str, w: int, h: int) -> bool:
    return cat == "system" and stem.lower() in WINDOWSKIN_NAMES and (w, h) == (192, 192)
