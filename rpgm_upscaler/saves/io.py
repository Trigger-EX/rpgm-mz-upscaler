"""Safe file handling for saves: backups, atomic writes, change detection."""
from __future__ import annotations

import os
from pathlib import Path

from .model import SaveError

KEEP_BACKUPS = 5


def backup(path: Path) -> Path:
    """Copy `path` to <name>.bak (or .bak.N when one exists); keep the newest KEEP_BACKUPS."""
    base = path.with_name(path.name + ".bak")
    target = base
    n = 0
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.bak.{n}")
    target.write_bytes(path.read_bytes())
    olds = sorted([p for p in path.parent.glob(path.name + ".bak*")], key=lambda p: p.stat().st_mtime_ns)
    for p in olds[:-KEEP_BACKUPS]:
        p.unlink(missing_ok=True)
    return target


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def guarded_write(path: Path, data: bytes, loaded_mtime_ns: int, make_backup: bool = True) -> Path | None:
    """Refuse when the file changed on disk since it was loaded (the game may be running)."""
    if path.exists() and path.stat().st_mtime_ns != loaded_mtime_ns:
        raise SaveError("the file changed on disk since it was loaded (is the game running?). Reload it first.")
    bak = backup(path) if (make_backup and path.exists()) else None
    atomic_write(path, data)
    return bak
