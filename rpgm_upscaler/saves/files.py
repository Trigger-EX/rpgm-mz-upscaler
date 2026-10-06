"""Find and open save files for every engine."""
from __future__ import annotations

import re
from pathlib import Path

from ..detect import ci_child, detect_engine
from .model import SaveDoc, SaveError

SUFFIXES = {".rpgsave", ".rmmzsave", ".rvdata2", ".rvdata"}
_RGSS_SAVE = re.compile(r"^save\d+\.rvdata2?$", re.I)


def find_saves(path: str | Path) -> list[Path]:
    """List save files for a game folder, a save/ folder, or a single save file."""
    p = Path(path).expanduser()
    if p.is_file():
        return [p]
    info = detect_engine(p)
    cands: list[Path] = []
    folders = [p]
    if info is not None:
        folders = [info.root, info.root / info.web / "save" if info.web else info.root / "save"]
        if ci_child(info.root, "save"):
            folders.append(ci_child(info.root, "save"))
    for d in dict.fromkeys(folders):
        if d and d.is_dir():
            for f in d.iterdir():
                if f.is_file() and ((f.suffix in (".rpgsave", ".rmmzsave")) or _RGSS_SAVE.match(f.name)):
                    cands.append(f)
    return sorted(cands, key=lambda f: (f.parent.as_posix(), natural(f.name)))


def natural(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def open_save(path: str | Path) -> SaveDoc:
    p = Path(path)
    if not p.is_file():
        raise SaveError(f"{p} is not a file")
    if p.suffix in (".rpgsave", ".rmmzsave"):
        from .mvmz import JsonSave
        try:
            return JsonSave(p)
        except SaveError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SaveError(f"cannot read {p.name}: {e}") from e
    if p.suffix in (".rvdata2", ".rvdata"):
        from .rgss import MarshalSave
        try:
            return MarshalSave(p)
        except Exception as e:  # noqa: BLE001
            raise SaveError(f"cannot read {p.name}: {e}") from e
    raise SaveError(f"unsupported save type {p.suffix}")
