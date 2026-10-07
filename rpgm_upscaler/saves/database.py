"""Names of switches, variables, actors, items ... read from the game's database files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..detect import ci_child, detect_engine
from ..rgss import marshal as m

_CODES = re.compile(r"\\[A-Za-z]+\[\d+\]|\\[{}.|!><^$]|\\\\")


def strip_codes(text: str) -> str:
    return _CODES.sub("", text or "").strip()


@dataclass
class Names:
    switches: list[str] = field(default_factory=list)       # index = id
    variables: list[str] = field(default_factory=list)
    actors: dict[int, str] = field(default_factory=dict)
    items: dict[int, str] = field(default_factory=dict)
    weapons: dict[int, str] = field(default_factory=dict)
    armors: dict[int, str] = field(default_factory=dict)
    classes: dict[int, str] = field(default_factory=dict)
    maps: dict[int, str] = field(default_factory=dict)
    currency: str = ""
    title: str = ""
    source: str = ""

    def switch(self, i: int) -> str:
        return self.switches[i] if 0 < i < len(self.switches) else ""

    def variable(self, i: int) -> str:
        return self.variables[i] if 0 < i < len(self.variables) else ""

    def inventory(self, kind: str) -> dict[int, str]:
        return getattr(self, kind)


def _read(folder: Path, base: str, suffixes: tuple[str, ...]):
    for suf in suffixes:
        f = ci_child(folder, base + suf)
        if f is not None and f.is_file():
            return f
    return None


def _ivname(o) -> str:
    v = o.ivars.get("@name") if isinstance(o, m.RObject) else None
    return v.text if isinstance(v, m.RString) else ""


def _json_names(data: Path) -> Names:
    def load(base):
        f = _read(data, base, (".json",))
        return json.loads(f.read_text(encoding="utf-8-sig")) if f else None

    n = Names(source=str(data))
    sysd = load("System") or {}
    n.switches = [str(x or "") for x in sysd.get("switches", [])]
    n.variables = [str(x or "") for x in sysd.get("variables", [])]
    n.currency, n.title = sysd.get("currencyUnit", ""), sysd.get("gameTitle", "")
    for attr, base in (("actors", "Actors"), ("items", "Items"), ("weapons", "Weapons"), ("armors", "Armors"), ("classes", "Classes")):
        arr = load(base) or []
        setattr(n, attr, {o["id"]: str(o.get("name", "")) for o in arr if isinstance(o, dict) and "id" in o})
    infos = load("MapInfos") or []
    n.maps = {o["id"]: str(o.get("name", "")) for o in infos if isinstance(o, dict) and "id" in o}
    return n


def _marshal_names(data: Path, suffix: str) -> Names:
    def load(base):
        f = _read(data, base, (suffix,))
        return m.loads(f.read_bytes()) if f else None

    n = Names(source=str(data))
    sysobj = load("System")
    if isinstance(sysobj, m.RObject):
        for attr, key in (("switches", "@switches"), ("variables", "@variables")):
            v = sysobj.ivars.get(key)
            setattr(n, attr, [(x.text if isinstance(x, m.RString) else "") for x in v] if isinstance(v, list) else [])
        cu = sysobj.ivars.get("@currency_unit")
        if cu is None and isinstance(sysobj.ivars.get("@words"), m.RObject):          # XP keeps it in the vocabulary
            cu = sysobj.ivars["@words"].ivars.get("@gold")
        n.currency = cu.text if isinstance(cu, m.RString) else ""
    for attr, base in (("actors", "Actors"), ("items", "Items"), ("weapons", "Weapons"), ("armors", "Armors"), ("classes", "Classes")):
        arr = load(base)
        if isinstance(arr, list):
            setattr(n, attr, {i: _ivname(o) for i, o in enumerate(arr) if o is not None})
    infos = load("MapInfos")
    if isinstance(infos, dict):
        n.maps = {int(k): _ivname(v) for k, v in infos.items()}
    return n


def load_names(game: str | Path) -> Names:
    """`game` may be the game folder or any file inside it (e.g. a save)."""
    info = detect_engine(game)
    if info is None:
        return Names()
    try:
        if info.is_html5:
            return _json_names(info.data_dir)
        return _marshal_names(info.data_dir, {"ACE": ".rvdata2", "VX": ".rvdata", "XP": ".rxdata"}[info.engine])
    except (OSError, ValueError, m.MarshalError, KeyError):
        return Names()
