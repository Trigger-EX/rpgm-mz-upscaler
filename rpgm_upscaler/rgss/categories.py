"""Graphics/ folder rules and sprite-sheet grids for XP / VX / VX Ace."""
from __future__ import annotations

from pathlib import PurePosixPath

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
FOLDERS = {"animations", "battlebacks1", "battlebacks2", "battlers", "characters", "faces", "parallaxes", "pictures",
           "system", "tilesets", "titles1", "titles2", "titles"}
XP_FOLDERS = {"animations", "autotiles", "battlebacks", "battlers", "characters", "fogs", "gameovers", "icons", "panoramas", "pictures",
              "tilesets", "titles", "transitions", "windowskins"}
SYSTEM_CELLS = {"iconset": (24, 24), "balloon": (32, 32)}
WINDOWSKINS = {"window"}
_VX_TILES = {f"tile{k.lower()}": k for k in ("A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E")}


def category(rel: PurePosixPath) -> str | None:
    """rel is relative to the game folder, e.g. Graphics/Characters/Actor1.png. None = not a graphic."""
    parts = rel.parts
    if len(parts) >= 3 and parts[0].lower() == "graphics" and rel.suffix.lower() in IMAGE_EXTS:
        return parts[1].lower()
    return None


def tile_kind(cat: str, stem: str, kinds: dict[str, str]) -> str | None:
    if cat == "tilesets":
        k = kinds.get(stem)
        if k:
            return k
        tail = stem.rsplit("_", 1)[-1].upper()
        return tail if tail in {"A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E"} else None
    if cat == "system":
        return _VX_TILES.get(stem.lower())
    return None


def cell_size(cat: str, stem: str, w: int, h: int, kinds: dict[str, str], engine: str = "") -> tuple[int, int] | None:
    def fit(cw: int, ch: int):
        return (cw, ch) if cw > 0 and ch > 0 and w % cw == 0 and h % ch == 0 else None

    if engine == "XP":                                  # RGSS1: 4x4 character sheets, 32 px tiles and autotile frames, 192 px animations
        if cat == "characters":
            return fit(w // 4, h // 4)
        if cat in ("tilesets", "autotiles"):
            return fit(32, 32)
        if cat == "animations":
            return fit(192, 192)
        return None

    if cat == "characters":
        return fit(w // 3, h // 4) if "$" in stem else fit(w // 12, h // 8)
    if cat == "faces":
        return fit(w // 4, h // 2)
    if cat == "animations":
        return fit(192, 192)
    if cat == "tilesets" or (cat == "system" and stem.lower() in _VX_TILES):
        kind = tile_kind(cat, stem, kinds)
        if kind in ("A1", "A2", "A3", "A4"):
            return fit(16, 16)
        return fit(32, 32) if kind else None
    if cat == "system" and stem.lower() in SYSTEM_CELLS:
        return fit(*SYSTEM_CELLS[stem.lower()])
    return None


def is_windowskin(cat: str, stem: str) -> bool:
    return (cat == "system" and stem.lower() in WINDOWSKINS) or cat == "windowskins"
