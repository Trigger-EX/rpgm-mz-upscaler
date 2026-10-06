"""Detect which RPG Maker engine a folder (or a file inside it) belongs to."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ENGINES = ("MV", "MZ", "ACE", "VX", "XP")
LABELS = {"MV": "RPG Maker MV", "MZ": "RPG Maker MZ", "ACE": "RPG Maker VX Ace", "VX": "RPG Maker VX", "XP": "RPG Maker XP"}


@dataclass
class EngineInfo:
    engine: str                      # MV | MZ | ACE | VX | XP
    root: Path                       # the game folder (contains index.html / Game.ini)
    web: str = ""                    # MV/MZ: web root relative to root ("" or "www")
    archive: Path | None = None      # RGSS encrypted archive, if present
    version: str = ""

    @property
    def label(self) -> str:
        return LABELS[self.engine]

    @property
    def is_html5(self) -> bool:
        return self.engine in ("MV", "MZ")

    @property
    def data_dir(self) -> Path:
        if self.is_html5:
            return (self.root / self.web if self.web else self.root) / "data"
        return ci_child(self.root, "Data") or self.root / "Data"


def ci_child(parent: Path, name: str) -> Path | None:
    """Case-insensitive child lookup (Windows games are routinely mis-cased on Linux)."""
    exact = parent / name
    if exact.exists():
        return exact
    try:
        for p in parent.iterdir():
            if p.name.lower() == name.lower():
                return p
    except OSError:
        pass
    return None


def _ini(root: Path) -> str:
    f = ci_child(root, "Game.ini")
    if f is None:
        return ""
    try:
        return f.read_bytes().decode("cp932", errors="replace")
    except OSError:
        return ""


def _rgss_info(root: Path) -> EngineInfo | None:
    ini = _ini(root)
    data = ci_child(root, "Data")
    archive = next((p for p in root.iterdir() if p.is_file() and p.suffix.lower() in (".rgss3a", ".rgss2a", ".rgssad")), None) \
        if root.is_dir() else None
    names = {p.name.lower() for p in data.iterdir()} if data and data.is_dir() else set()
    lib = re.search(r"(?im)^\s*Library\s*=\s*(.+)$", ini)
    lib = lib.group(1).lower() if lib else ""
    scripts = re.search(r"(?im)^\s*Scripts\s*=\s*(.+)$", ini)
    scripts = scripts.group(1).lower() if scripts else ""
    suffixes = {Path(n).suffix for n in names}
    if "rgss3" in lib or ".rvdata2" in suffixes or "rvdata2" in scripts or (archive and archive.suffix.lower() == ".rgss3a"):
        return EngineInfo("ACE", root, archive=archive)
    if "rgss2" in lib or ".rvdata" in suffixes or (archive and archive.suffix.lower() == ".rgss2a") or scripts.endswith(".rvdata"):
        return EngineInfo("VX", root, archive=archive)
    if "rgss1" in lib or "rgss10" in lib or ".rxdata" in suffixes or (archive and archive.suffix.lower() == ".rgssad") or scripts.endswith(".rxdata"):
        return EngineInfo("XP", root, archive=archive)
    return None


def detect_engine(path: str | Path) -> EngineInfo | None:
    """Accepts a game folder, its www/ folder, a save/ or Data/ folder or a file inside the game."""
    from .core.project import ProjectError, find_web_root, _detect_engine
    p = Path(path).expanduser().resolve()
    cur = p if p.is_dir() else p.parent
    for d in [cur, *list(cur.parents)[:4]]:
        try:
            web = find_web_root(d)
        except ProjectError:
            web = None
        if web is not None:
            engine, version = _detect_engine(d / web if web else d)
            return EngineInfo(engine, d, web, version=version)
        info = _rgss_info(d)
        if info is not None:
            return info
    return None
