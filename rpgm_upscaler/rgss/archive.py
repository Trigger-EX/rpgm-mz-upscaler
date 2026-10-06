"""RGSS encrypted archives: XP/VX (.rgssad / .rgss2a, v1) and VX Ace (.rgss3a, v3). Read, extract and (for tests) pack."""
from __future__ import annotations

import struct
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

M32 = 0xFFFFFFFF
MAGIC = b"RGSSAD\x00"


class ArchiveError(Exception):
    pass


@dataclass
class Entry:
    name: str          # '/'-separated, as stored (case preserved)
    offset: int
    size: int
    key: int           # data key


def _adv(k: int) -> int:
    return (k * 7 + 3) & M32


def _crypt_data(data: bytes, key: int) -> bytes:
    """XOR little-endian words with a rolling key; the trailing partial word uses the low bytes."""
    n = len(data)
    words = n // 4
    out = bytearray(n)
    k = key
    if words:
        vals = struct.unpack_from(f"<{words}I", data)
        res = []
        for v in vals:
            res.append(v ^ k)
            k = (k * 7 + 3) & M32
        struct.pack_into(f"<{words}I", out, 0, *res)
    for i in range(words * 4, n):
        out[i] = data[i] ^ ((k >> (8 * (i - words * 4))) & 0xFF)
    return bytes(out)


def _decode_name(raw: bytes) -> str:
    name = raw.decode("utf-8") if _is_utf8(raw) else raw.decode("cp932", errors="replace")
    return name.replace("\\", "/")


def _is_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def safe_relpath(name: str) -> PurePosixPath:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or any(part == "" for part in p.parts) or ":" in name:
        raise ArchiveError(f"unsafe path in archive: {name!r}")
    return p


class Archive:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.buf = self.path.read_bytes()
        if self.buf[:7] != MAGIC or len(self.buf) < 8:
            raise ArchiveError(f"{self.path.name} is not an RGSS archive (bad header)")
        self.version = self.buf[7]
        if self.version not in (1, 3):
            raise ArchiveError(f"unsupported RGSS archive version {self.version}")
        self.entries: list[Entry] = self._parse()
        self._by_name = {e.name.lower(): e for e in self.entries}

    def _u32(self, pos: int) -> int:
        if pos + 4 > len(self.buf):
            raise ArchiveError("archive is truncated")
        return struct.unpack_from("<I", self.buf, pos)[0]

    def _parse(self) -> list[Entry]:
        out: list[Entry] = []
        b = self.buf
        if self.version == 1:
            key, pos = 0xDEADCAFE, 8
            while pos < len(b):
                nlen = self._u32(pos) ^ key; pos += 4; key = _adv(key)
                if nlen > 4096 or pos + nlen > len(b):
                    raise ArchiveError("corrupt entry name length")
                name = bytearray()
                for i in range(nlen):
                    name.append(b[pos + i] ^ (key & 0xFF)); key = _adv(key)
                pos += nlen
                size = self._u32(pos) ^ key; pos += 4; key = _adv(key)
                if pos + size > len(b):
                    raise ArchiveError("archive is truncated")
                out.append(Entry(_decode_name(bytes(name)), pos, size, key))
                pos += size
        else:
            key = (self._u32(8) * 9 + 3) & M32
            pos = 12
            while True:
                offset = self._u32(pos) ^ key
                if offset == 0:
                    break
                size = self._u32(pos + 4) ^ key
                ekey = self._u32(pos + 8) ^ key
                nlen = self._u32(pos + 12) ^ key
                pos += 16
                if nlen > 4096 or pos + nlen > len(b) or offset + size > len(b):
                    raise ArchiveError("corrupt entry table")
                name = bytes(b[pos + i] ^ ((key >> (8 * (i % 4))) & 0xFF) for i in range(nlen))
                pos += nlen
                out.append(Entry(_decode_name(name), offset, size, ekey))
        return out

    def read(self, name: str) -> bytes:
        e = self._by_name.get(name.replace("\\", "/").lower())
        if e is None:
            raise KeyError(name)
        return _crypt_data(self.buf[e.offset:e.offset + e.size], e.key)

    def exists(self, name: str) -> bool:
        return name.replace("\\", "/").lower() in self._by_name

    def extract_all(self, dest: str | Path, progress: Callable[[int, int, str], None] | None = None,
                    cancel: threading.Event | None = None) -> list[Path]:
        dest = Path(dest)
        out = []
        for i, e in enumerate(self.entries):
            if cancel is not None and cancel.is_set():
                break
            rel = safe_relpath(e.name)
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_crypt_data(self.buf[e.offset:e.offset + e.size], e.key))
            out.append(target)
            if progress:
                progress(i + 1, len(self.entries), e.name)
        return out


def open_archive(path: str | Path) -> Archive:
    return Archive(path)


def pack(files: dict[str, bytes], version: int = 3, seed: int = 0x1234ABCD, data_keys: dict[str, int] | None = None) -> bytes:
    """Build an archive (used by tests, and handy for round-tripping). Names use '/' and are stored with '\\'."""
    out = bytearray(MAGIC + bytes([version]))
    names = list(files)
    if version == 1:
        key = 0xDEADCAFE
        for n in names:
            raw = n.replace("/", "\\").encode("cp932" if not n.isascii() else "ascii")
            out += struct.pack("<I", len(raw) ^ key); key = _adv(key)
            for c in raw:
                out.append(c ^ (key & 0xFF)); key = _adv(key)
            data = files[n]
            out += struct.pack("<I", len(data) ^ key); key = _adv(key)
            out += _crypt_data(data, key)
        return bytes(out)
    key = (seed * 9 + 3) & M32
    out += struct.pack("<I", seed)
    table_size = sum(16 + len(n.replace("/", "\\").encode("utf-8")) for n in names) + 16
    pos = 12 + table_size
    blobs = []
    for n in names:
        data = files[n]
        raw = n.replace("/", "\\").encode("utf-8")
        ekey = (data_keys or {}).get(n, (hash(n) * 2654435761 + 7) & M32)
        out += struct.pack("<4I", pos ^ key, len(data) ^ key, ekey ^ key, len(raw) ^ key)
        out += bytes(c ^ ((key >> (8 * (i % 4))) & 0xFF) for i, c in enumerate(raw))
        blobs.append(_crypt_data(data, ekey))
        pos += len(data)
    out += struct.pack("<4I", 0 ^ key, 0 ^ key, 0 ^ key, 0 ^ key)
    for b in blobs:
        out += b
    return bytes(out)
