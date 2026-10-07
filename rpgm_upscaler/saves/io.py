"""Safe file handling for saves: backups, atomic writes, change detection."""
from __future__ import annotations

import os
import tempfile
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
    """Write beside the target, flush to disk, then rename over it: a crash leaves the old save or the new one, never half of one."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    try:                                          # make the rename itself durable (not available on every platform)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def guarded_write(path: Path, data: bytes, loaded_mtime_ns: int, make_backup: bool = True) -> Path | None:
    """Refuse when the file changed on disk since it was loaded (the game may be running)."""
    if path.exists() and path.stat().st_mtime_ns != loaded_mtime_ns:
        raise SaveError("the file changed on disk since it was loaded (is the game running?). Reload it first.")
    bak = backup(path) if (make_backup and path.exists()) else None
    atomic_write(path, data)
    return bak
