"""MV (.rpgsave, LZString base64) and MZ (.rmmzsave, zlib) saves: JSON trees edited in place."""
from __future__ import annotations

import json
import re
import zlib
from pathlib import Path

from . import lzstring
from .io import guarded_write
from .model import ActorView, INVENTORY_KINDS, SaveDoc, SaveError

MV_SUFFIX, MZ_SUFFIX = ".rpgsave", ".rmmzsave"
_B64 = re.compile(rb"^[A-Za-z0-9+/=\s]+$")


def decode(raw: bytes) -> tuple[object, str]:
    """Return (tree, codec). codec in lz | zlib-raw | zlib-utf8 (the variant is preserved on write)."""
    if len(raw) > 1 and raw[0] == 0x78 and raw[1] in (0x01, 0x5E, 0x9C, 0xDA):
        try:                       # a latin-1 string written as UTF-8 starts with the same two ASCII bytes
            return json.loads(zlib.decompress(raw).decode("utf-8")), "zlib-raw"
        except (zlib.error, UnicodeError, ValueError):
            pass
    if _B64.match(raw):
        return json.loads(lzstring.decompress_from_base64(raw.decode("ascii"))), "lz"
    try:
        return json.loads(zlib.decompress(raw.decode("utf-8").encode("latin-1")).decode("utf-8")), "zlib-utf8"
    except (UnicodeError, zlib.error, ValueError):
        pass
    raise SaveError("unrecognised save encoding (not LZString, not zlib)")


def dump_json(tree) -> str:
    return json.dumps(tree, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def encode(tree, codec: str) -> bytes:
    text = dump_json(tree)
    if codec == "lz":
        return lzstring.compress_to_base64(text).encode("ascii")
    z = zlib.compress(text.encode("utf-8"), 1)
    return z if codec == "zlib-raw" else z.decode("latin-1").encode("utf-8")


def arr(node) -> list | None:
    """JsonEx stores arrays that carry extra properties as {"@a": [...]}."""
    if isinstance(node, list):
        return node
    if isinstance(node, dict) and isinstance(node.get("@a"), list):
        return node["@a"]
    return None


def real_items(d: dict) -> dict:
    """Entries of a JsonEx dict without its metadata keys ("@", "@c", "@r", "@a")."""
    return {k: v for k, v in d.items() if not str(k).startswith("@")}


def _grow(a: list, i: int) -> None:
    while len(a) <= i:
        a.append(None)


class JsonSave(SaveDoc):
    def __init__(self, path: Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self._mtime = self.path.stat().st_mtime_ns
        self.tree, self.codec = decode(raw)
        self.engine = "MZ" if self.path.suffix == MZ_SUFFIX or self.codec != "lz" else "MV"
        self.kind = "global" if self.path.stem in ("global", "config") else "game"
        self.readonly = None
        try:
            if decode(encode(self.tree, self.codec))[0] != self.tree:
                raise ValueError("re-encoding changes the data")
        except Exception as e:  # noqa: BLE001
            self.readonly = f"round-trip check failed ({e})"
        if self.kind == "game" and not isinstance(self.tree, dict):
            self.readonly = "unexpected save structure"

    # ---- helpers
    def _c(self, key: str):
        node = self.tree.get(key) if isinstance(self.tree, dict) else None
        return node if isinstance(node, dict) else None

    def _list(self, key: str, field: str = "_data") -> list | None:
        c = self._c(key)
        return arr(c.get(field)) if c else None

    # ---- switches / variables
    def switch_count(self) -> int:
        a = self._list("switches")
        return max(0, len(a) - 1) if a else 0

    def get_switch(self, i: int) -> bool:
        a = self._list("switches")
        return bool(a[i]) if a and 0 < i < len(a) else False

    def set_switch(self, i: int, v: bool) -> None:
        self._touch()
        a = self._list("switches")
        if a is None:
            raise SaveError("no switches in this save")
        _grow(a, i)
        a[i] = bool(v)

    def variable_count(self) -> int:
        a = self._list("variables")
        return max(0, len(a) - 1) if a else 0

    def get_variable(self, i: int):
        a = self._list("variables")
        v = a[i] if a and 0 < i < len(a) else 0
        return 0 if v is None else v

    def set_variable(self, i: int, v) -> None:
        self._touch()
        a = self._list("variables")
        if a is None:
            raise SaveError("no variables in this save")
        _grow(a, i)
        a[i] = v

    # ---- party
    def gold(self):
        p = self._c("party")
        return p.get("_gold") if p else None

    def set_gold(self, v: int) -> None:
        self._touch()
        p = self._c("party")
        if p is None:
            raise SaveError("no party in this save")
        p["_gold"] = self.clamp_gold(v)

    def party_ids(self) -> list[int]:
        p = self._c("party")
        a = arr(p.get("_actors")) if p else None
        return [int(x) for x in a] if a else []

    def _actor_node(self, actor_id: int):
        a = self._list("actors")
        if a and 0 < actor_id < len(a) and isinstance(a[actor_id], dict):
            return a[actor_id]
        return None

    def actor(self, actor_id: int):
        n = self._actor_node(actor_id)
        if n is None:
            return None
        exp = n.get("_exp")
        cid = n.get("_classId")
        if isinstance(exp, dict):
            vals = real_items(exp)
            exp = vals.get(str(cid), next(iter(vals.values()), None))
        return ActorView(actor_id, str(n.get("_name", "")), n.get("_level"), exp, n.get("_hp"), n.get("_mp"), cid)

    def set_actor(self, actor_id: int, **fields) -> None:
        self._touch()
        n = self._actor_node(actor_id)
        if n is None:
            raise SaveError(f"actor {actor_id} not found")
        for k, v in fields.items():
            if k == "level":
                n["_level"] = max(1, min(int(v), 99999))
            elif k == "exp":
                if isinstance(n.get("_exp"), dict):
                    n["_exp"][str(n.get("_classId"))] = max(0, int(v))
                else:
                    n["_exp"] = max(0, int(v))
            elif k in ("hp", "mp"):
                n["_" + k] = max(0, int(v))
            elif k == "name":
                n["_name"] = str(v)
            else:
                raise SaveError(f"unknown actor field {k}")

    def inventory(self, kind: str) -> dict[int, int]:
        p = self._c("party")
        d = p.get("_" + kind) if p else None
        return {int(k): int(v) for k, v in real_items(d).items()} if isinstance(d, dict) else {}

    def set_item(self, kind: str, item_id: int, count: int) -> None:
        self._touch()
        if kind not in INVENTORY_KINDS:
            raise SaveError(f"unknown inventory kind {kind}")
        p = self._c("party")
        if p is None:
            raise SaveError("no party in this save")
        d = p.setdefault("_" + kind, {})
        if count <= 0:
            d.pop(str(item_id), None)
        else:
            d[str(item_id)] = min(int(count), 99)

    def position(self):
        m, pl = self._c("map"), self._c("player")
        if not m or not pl:
            return None
        return int(m.get("_mapId", 0)), int(pl.get("_x", 0)), int(pl.get("_y", 0))

    def set_position(self, map_id=None, x=None, y=None) -> None:
        self._touch()
        m, pl = self._c("map"), self._c("player")
        if m is None or pl is None:
            raise SaveError("no map/player in this save")
        if map_id is not None:
            m["_mapId"] = int(map_id)
        if x is not None:
            pl["_x"] = pl["_realX"] = int(x)
        if y is not None:
            pl["_y"] = pl["_realY"] = int(y)
        pl["_transferring"] = False

    def playtime(self):
        s = self._c("system")
        f = s.get("_framesOnSave") if s else None
        if not isinstance(f, (int, float)):
            return None
        sec = int(f // 60)
        return f"{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"

    def save(self, backup: bool = True) -> None:
        self._check_writable()
        data = encode(self.tree, self.codec)
        if decode(data)[0] != self.tree:
            raise SaveError("re-decoding the new data did not reproduce the edited tree; nothing was written")
        guarded_write(self.path, data, self._mtime, backup)
        self._mtime = self.path.stat().st_mtime_ns
        self.dirty = False
