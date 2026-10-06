"""VX / VX Ace project metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..detect import EngineInfo, ci_child, detect_engine
from . import marshal as m

SCREEN = {"ACE": (544, 416), "VX": (544, 416), "XP": (640, 480)}


class RgssProjectError(Exception):
    pass


@dataclass
class RgssProject:
    """Duck-types the parts of core.project.Project that the shared runner needs."""
    base: Path                       # folder holding Game.ini / Graphics / Data (extracted if the game was archived)
    engine: str                      # ACE | VX
    screen: tuple[int, int] = (544, 416)
    tile_size: int = 32
    title: str = ""
    scripts_path: str = "Data/Scripts.rvdata2"
    tile_kinds: dict[str, str] = field(default_factory=dict)       # tileset image stem -> A1..E
    warnings: list[str] = field(default_factory=list)
    key = None
    web = ""
    has_encrypted_images = False

    @property
    def web_root(self) -> Path:
        return self.base


def _ini_value(text: str, key: str) -> str:
    mt = re.search(rf"(?im)^\s*{key}\s*=\s*(.*?)\s*$", text)
    return mt.group(1) if mt else ""


def tileset_kinds(data_dir: Path, suffix: str) -> dict[str, str]:
    f = ci_child(data_dir, "Tilesets" + suffix)
    if f is None or not f.is_file():
        return {}
    try:
        arr = m.loads(f.read_bytes())
    except (m.MarshalError, OSError):
        return {}
    slots = ["A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E"]
    kinds: dict[str, str] = {}
    for ts in arr if isinstance(arr, list) else []:
        names = ts.ivars.get("@tileset_names") if isinstance(ts, m.RObject) else None
        if isinstance(names, list):
            for slot, n in zip(slots, names):
                if isinstance(n, m.RString) and n.text:
                    kinds.setdefault(n.text, slot)
    return kinds


def load_rgss_project(path: str | Path, base: Path | None = None, info: EngineInfo | None = None) -> RgssProject:
    """`base` overrides the folder to read (the extracted tree of an encrypted game)."""
    info = info or detect_engine(path)
    if info is None or info.engine not in ("ACE", "VX"):
        raise RgssProjectError("not a VX / VX Ace project")
    root = Path(base) if base else info.root
    ini_file = ci_child(root, "Game.ini")
    ini = ini_file.read_bytes().decode("cp932", errors="replace") if ini_file else ""
    default_scripts = "Data/Scripts.rvdata2" if info.engine == "ACE" else "Data/Scripts.rvdata"
    scripts = _ini_value(ini, "Scripts").replace("\\", "/") or default_scripts
    suffix = ".rvdata2" if info.engine == "ACE" else ".rvdata"
    data = ci_child(root, "Data") or root / "Data"
    p = RgssProject(root, info.engine, SCREEN[info.engine], 32, _ini_value(ini, "Title"), scripts,
                    tileset_kinds(data, suffix))
    if info.archive is not None and base is None:
        p.warnings.append(f"{info.archive.name} is encrypted; it has to be extracted first.")
    return p
