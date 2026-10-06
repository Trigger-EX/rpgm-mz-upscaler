"""Scripts.rvdata / Scripts.rvdata2: a Marshal array of [id, title, zlib-compressed source]."""
from __future__ import annotations

import zlib
from dataclasses import dataclass

from . import marshal as m


class ScriptsError(Exception):
    pass


@dataclass
class ScriptInfo:
    index: int
    id: int
    title: str
    size: int


def load(buf: bytes) -> m.RArray:
    try:
        arr = m.loads(buf)
    except m.MarshalError as e:
        raise ScriptsError(f"cannot parse scripts file: {e}") from e
    if not isinstance(arr, list) or any(not (isinstance(e, list) and len(e) == 3) for e in arr):
        raise ScriptsError("unexpected Scripts structure (expected [[id, title, data], ...])")
    return arr


def _title(e) -> str:
    t = e[1]
    return t.text if isinstance(t, m.RString) else str(t)


def source(entry) -> str:
    raw = zlib.decompress(entry[2].data)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def listing(arr) -> list[ScriptInfo]:
    out = []
    for i, e in enumerate(arr):
        try:
            size = len(zlib.decompress(e[2].data))
        except zlib.error:
            size = -1
        out.append(ScriptInfo(i, int(e[0]), _title(e), size))
    return out


def make_entry(arr, script_id: int, title: str, src: str) -> m.RArray:
    """Create an entry shaped like the existing ones (VX/Ruby 1.8 strings carry no encoding ivar)."""
    legacy = bool(arr) and isinstance(arr[0][1], m.RString) and not arr[0][1].ivars
    t = m.RString(title.encode("utf-8"), {} if legacy else {"E": True})
    d = m.RString(zlib.compress(src.encode("utf-8"), 9), {})
    return m.RArray([script_id, t, d])


def insert_before_main(arr, title: str, src: str, script_id: int = 700001) -> m.RArray:
    """Add (or replace, by title) a script just before the last entry titled 'Main' (else before the last entry)."""
    for i, e in enumerate(list(arr)):
        if _title(e) == title:
            del arr[i]
    used = {int(e[0]) for e in arr}
    while script_id in used:
        script_id += 1
    idx = next((i for i in range(len(arr) - 1, -1, -1) if _title(arr[i]).strip().lower().endswith("main")), len(arr) - 1)
    entry = make_entry(arr, script_id, title, src)
    arr.insert(max(idx, 0), entry)
    return entry


def remove(arr, title: str) -> bool:
    for i, e in enumerate(list(arr)):
        if _title(e) == title:
            del arr[i]
            return True
    return False
