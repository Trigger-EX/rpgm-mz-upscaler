"""XP (.rxdata), VX (.rvdata) and VX Ace (.rvdata2) saves: several consecutive Marshal streams, edited in place."""
from __future__ import annotations

from pathlib import Path

from ..rgss import marshal as m
from .io import guarded_write
from .model import ActorView, INVENTORY_KINDS, SaveDoc, SaveError


def _walk(o, seen=None):
    """Yield every RObject reachable from o (cycle safe)."""
    seen = seen if seen is not None else set()
    if id(o) in seen:
        return
    seen.add(id(o))
    if isinstance(o, m.RObject):
        yield o
        for v in o.ivars.values():
            yield from _walk(v, seen)
    elif isinstance(o, dict):
        for v in o.values():
            yield from _walk(v, seen)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, seen)


def _iv(o, name, default=None):
    return o.ivars.get(name, default) if o is not None else default


def _text(v) -> str:
    return v.text if isinstance(v, m.RString) else ("" if v is None else str(v))


class MarshalSave(SaveDoc):
    def __init__(self, path: Path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self._mtime = self.path.stat().st_mtime_ns
        self.streams = m.load_all(raw)
        self.engine = {".rvdata2": "ACE", ".rxdata": "XP"}.get(self.path.suffix, "VX")
        self.readonly = None
        if m.dump_all(self.streams) != raw:
            self.readonly = "re-serialising does not reproduce the original bytes"
        self._cls: dict[str, m.RObject] = {}
        for s in self.streams:
            for o in _walk(s):
                self._cls.setdefault(o.cls, o)
        if "Game_Party" not in self._cls and "Game_Switches" not in self._cls:
            self.readonly = self.readonly or "not a recognisable RPG Maker save (no Game_Party / Game_Switches)"
        self.header = self.streams[0] if self.streams and isinstance(self.streams[0], dict) else None

    def _o(self, cls: str):
        return self._cls.get(cls)

    def _data(self, cls: str):
        o = self._o(cls)
        d = _iv(o, "@data")
        return d if isinstance(d, list) else None

    # ---- switches / variables
    def switch_count(self) -> int:
        d = self._data("Game_Switches")
        return max(0, len(d) - 1) if d else 0

    def get_switch(self, i: int) -> bool:
        d = self._data("Game_Switches")
        return bool(d[i]) if d and 0 < i < len(d) else False

    def _set(self, cls: str, i: int, v, what: str) -> None:
        self._touch()
        d = self._data(cls)
        if d is None:
            raise SaveError(f"no {what} in this save")
        while len(d) <= i:
            d.append(None)
        d[i] = v

    def set_switch(self, i: int, v: bool) -> None:
        self._set("Game_Switches", i, bool(v), "switches")

    def variable_count(self) -> int:
        d = self._data("Game_Variables")
        return max(0, len(d) - 1) if d else 0

    def get_variable(self, i: int):
        d = self._data("Game_Variables")
        v = d[i] if d and 0 < i < len(d) else 0
        return 0 if v is None else v

    def set_variable(self, i: int, v) -> None:
        if isinstance(v, str):
            v = m.RString(v.encode("utf-8"), {"E": True} if self.engine == "ACE" else {})
        self._set("Game_Variables", i, v, "variables")

    # ---- party
    def gold(self):
        return _iv(self._o("Game_Party"), "@gold")

    def set_gold(self, v: int) -> None:
        self._touch()
        p = self._o("Game_Party")
        if p is None:
            raise SaveError("no party in this save")
        p.ivars["@gold"] = self.clamp_gold(v)

    def party_ids(self) -> list[int]:
        a = _iv(self._o("Game_Party"), "@actors")
        if not isinstance(a, list):
            return []
        # VX / Ace keep actor ids here; XP keeps the Game_Actor objects themselves
        return [int(_iv(x, "@actor_id", 0)) if isinstance(x, m.RObject) else int(x) for x in a]

    def _actor_obj(self, actor_id: int):
        d = self._data("Game_Actors")
        if d and 0 < actor_id < len(d) and isinstance(d[actor_id], m.RObject):
            return d[actor_id]
        return None

    def actor(self, actor_id: int):
        a = self._actor_obj(actor_id)
        if a is None:
            return None
        exp = _iv(a, "@exp")
        cid = _iv(a, "@class_id")
        if isinstance(exp, dict):
            exp = exp.get(cid, next(iter(exp.values()), None))
        mp = _iv(a, "@mp") if "@mp" in a.ivars else _iv(a, "@sp")                 # XP calls magic points SP
        return ActorView(actor_id, _text(_iv(a, "@name")), _iv(a, "@level"), exp, _iv(a, "@hp"), mp, cid)

    def set_actor(self, actor_id: int, **fields) -> None:
        self._touch()
        a = self._actor_obj(actor_id)
        if a is None:
            raise SaveError(f"actor {actor_id} not found")
        for k, v in fields.items():
            if k == "level":
                a.ivars["@level"] = max(1, min(int(v), 99))
            elif k == "exp":
                e = a.ivars.get("@exp")
                if isinstance(e, dict):
                    e[a.ivars.get("@class_id")] = max(0, int(v))
                else:
                    a.ivars["@exp"] = max(0, int(v))
            elif k in ("hp", "mp"):
                key = "@sp" if (k == "mp" and "@mp" not in a.ivars and "@sp" in a.ivars) else "@" + k
                a.ivars[key] = max(0, int(v))
            elif k == "name":
                a.ivars["@name"] = m.RString(str(v).encode("utf-8"), {"E": True} if self.engine == "ACE" else {})
            else:
                raise SaveError(f"unknown actor field {k}")

    def inventory(self, kind: str) -> dict[int, int]:
        d = _iv(self._o("Game_Party"), "@" + kind)
        return {int(k): int(v) for k, v in d.items()} if isinstance(d, dict) else {}

    def set_item(self, kind: str, item_id: int, count: int) -> None:
        self._touch()
        if kind not in INVENTORY_KINDS:
            raise SaveError(f"unknown inventory kind {kind}")
        p = self._o("Game_Party")
        d = _iv(p, "@" + kind)
        if not isinstance(d, dict):
            raise SaveError("no inventory in this save")
        if count <= 0:
            d.pop(item_id, None)
        else:
            d[item_id] = min(int(count), 99)

    def position(self):
        mp, pl = self._o("Game_Map"), self._o("Game_Player")
        if mp is None or pl is None:
            return None
        if _iv(pl, "@transferring", False):               # a reserved transfer is what the engine will do on load
            return int(_iv(pl, "@new_map_id", 0)), int(_iv(pl, "@new_x", 0)), int(_iv(pl, "@new_y", 0))
        return int(_iv(mp, "@map_id", 0)), int(_iv(pl, "@x", 0)), int(_iv(pl, "@y", 0))

    def set_position(self, map_id=None, x=None, y=None) -> None:
        """Another map is reached by reserving a transfer: Game_Player#perform_transfer only calls Game_Map#setup when
        @new_map_id differs from @map_id, so rewriting @map_id alone would keep the old map's events."""
        self._touch()
        mp, pl = self._o("Game_Map"), self._o("Game_Player")
        if mp is None or pl is None:
            raise SaveError("no map/player in this save")
        cur_map, cur_x, cur_y = self.position()
        target = int(map_id) if map_id is not None else cur_map
        tx, ty = int(x) if x is not None else cur_x, int(y) if y is not None else cur_y
        if target != int(_iv(mp, "@map_id", 0)) or _iv(pl, "@transferring", False):
            pl.ivars["@transferring"] = True
            pl.ivars["@new_map_id"], pl.ivars["@new_x"], pl.ivars["@new_y"] = target, tx, ty
            pl.ivars.setdefault("@new_direction", _iv(pl, "@direction", 2))
            return
        for axis, v in (("x", tx), ("y", ty)):
            pl.ivars["@" + axis] = v
            real = pl.ivars.get("@real_" + axis)
            if real is None:
                continue
            if getattr(self, "engine", "ACE") in ("VX", "XP"):   # VX: 1/256 tile units, XP: 1/128
                pl.ivars["@real_" + axis] = v * (128 if self.engine == "XP" else 256)
            elif isinstance(real, float):                     # Ace: tiles; a standing player may hold an Integer, a moving one a Float
                pl.ivars["@real_" + axis] = m.RFloat(float(v))
            else:
                pl.ivars["@real_" + axis] = v

    def playtime(self):
        if self.engine == "XP" and len(self.streams) > 1 and isinstance(self.streams[1], int):
            sec = self.streams[1] // 40                                  # Graphics.frame_count at RGSS1's 40 fps
            return f"{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"
        if self.header is not None:
            for k, v in self.header.items():
                if str(k) == "playtime_s" and isinstance(v, int):
                    return f"{v // 3600:02d}:{v // 60 % 60:02d}:{v % 60:02d}"
        return None

    def save(self, backup: bool = True) -> None:
        self._check_writable()
        data = m.dump_all(self.streams)
        if m.dump_all(m.load_all(data)) != data:
            raise SaveError("re-reading the new data did not reproduce it byte for byte; nothing was written")
        guarded_write(self.path, data, self._mtime, backup)
        self._mtime = self.path.stat().st_mtime_ns
        self.dirty = False
