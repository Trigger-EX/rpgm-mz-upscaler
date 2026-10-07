"""Find every player-visible string in an RPG Maker game's data and write translations back.

The same event-command logic serves MV/MZ (JSON) and VX/VX Ace (Ruby Marshal) through small adapters. Extraction produces
`Unit`s (one translatable string with a setter); multi-line dialogue becomes one unit per message so the translator sees
whole sentences, and the English is re-wrapped to the message window. Nothing here translates: callers fill `Unit.en`.
"""
from __future__ import annotations

import copy
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable

from ..rgss import marshal as m
from ..rgss import scripts as sc
from .detect import is_japanese

# ---------------------------------------------------------------------------------------------------------------------
# units


@dataclass
class Unit:
    kind: str                 # dialogue | scroll | choice | name | description | term | message | title | map | plugin
    ja: str
    where: str                # file (and place) for reports
    en: str | None = None
    apply: Callable[["Unit"], None] | None = None
    changed: bool = False
    skip_reason: str = ""      # set when translating would be unsafe (e.g. a script compares against this exact text)


@dataclass
class Collected:
    units: list[Unit] = field(default_factory=list)
    finishers: list[Callable[[], None]] = field(default_factory=list)
    known_names: set[str] = field(default_factory=set)      # actor/enemy names seen so far: lines equal to one are speaker labels

    def add(self, kind: str, ja: str, where: str, apply: Callable[[Unit], None]) -> None:
        if is_japanese(ja):
            self.units.append(Unit(kind, ja, where, apply=apply))

    def commit(self) -> int:
        """Write every filled-in translation back into the data it came from. Returns the number of strings changed."""
        n = 0
        for u in self.units:
            if u.en and u.en != u.ja:
                if u.apply:                       # list-rebuilding units (dialogue) are written by their finisher
                    u.apply(u)
                u.changed = True
                n += 1
        for f in self.finishers:
            f()
        return n


# ---------------------------------------------------------------------------------------------------------------------
# wrapping

_CTRL_W = [(re.compile(r"\\[Nn]\[\d+\]"), "NNNNNN"), (re.compile(r"\\[Vv]\[\d+\]"), "000"), (re.compile(r"\\[Ii]\[\d+\]"), "XX"),
           (re.compile(r"\\[Pp]\[\d+\]"), "NNNNNN"), (re.compile(r"\\G"), "G")]
_CTRL_ZERO = re.compile(r"\\[A-Za-z]+\[[^\]\n]*\]|\\[A-Za-z]+|\\[.|!><^${}]|\\\\")


def visible_len(s: str) -> int:
    for rx, rep in _CTRL_W:
        s = rx.sub(rep, s)
    return len(_CTRL_ZERO.sub("", s))


def wrap_text(text: str, width: int) -> list[str]:
    """Greedy word wrap that counts control codes as the width of what they print; keeps explicit newlines."""
    out: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            if not word:
                continue
            if line and visible_len(line) + 1 + visible_len(word) > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}" if line else word
        out.append(line)
    while len(out) > 1 and out[-1] == "":
        out.pop()
    return out


@dataclass
class Wrap:
    chars: int = 50            # visible characters per message line without a face
    face_chars: int = 38       # ... with a face picture
    wide_chars: int = 60       # scrolling text / descriptions
    lines: int = 4             # lines per message window


def wrap_for(engine: str, screen_w: int, font: int) -> Wrap:
    """Estimate message-window capacity for English (about 0.55 em per character) from the engine's stock layout."""
    em = max(8.0, font * 0.55)
    if engine == "XP":                       # 480 px message window (x 80..560) with 16 px padding, no faces; 640 px description window
        return Wrap(chars=max(24, int(448 / em) - 2), face_chars=max(24, int(448 / em) - 2), wide_chars=max(30, int(608 / em) - 2))
    pad, face = {"MV": (18, 168), "MZ": (12, 164), "ACE": (12, 112), "VX": (8, 112)}.get(engine, (18, 168))
    usable = screen_w - 2 * pad
    return Wrap(chars=max(24, int(usable / em) - 2), face_chars=max(18, int((usable - face) / em) - 2),
                wide_chars=max(30, int(usable / em) - 2))


# ---------------------------------------------------------------------------------------------------------------------
# adapters over the two data models


class _Json:
    marshal = False

    @staticmethod
    def code(c): return c.get("code")
    @staticmethod
    def indent(c): return c.get("indent", 0)
    @staticmethod
    def params(c): return c.get("parameters", [])
    @staticmethod
    def text(v): return v if isinstance(v, str) else ""
    @staticmethod
    def make(code, indent, params, like=None): return {"code": code, "indent": indent, "parameters": params}
    @staticmethod
    def clone(c): return copy.deepcopy(c)
    @staticmethod
    def str_like(like, text): return text


class _Rb:
    marshal = True

    @staticmethod
    def code(c): return c.ivars.get("@code")
    @staticmethod
    def indent(c): return c.ivars.get("@indent", 0)
    @staticmethod
    def params(c): return c.ivars.get("@parameters", [])
    @staticmethod
    def text(v): return v.text if isinstance(v, m.RString) else ""

    @staticmethod
    def make(code, indent, params, like=None):
        o = m.RObject("RPG::EventCommand", {"@code": code, "@indent": indent, "@parameters": m.RArray(params)})
        return o

    @staticmethod
    def clone(c): return copy.deepcopy(c)

    @staticmethod
    def str_like(like, text):
        s = copy.copy(like) if isinstance(like, m.RString) else m.RString(b"", {"E": True})
        s.ivars = dict(s.ivars or {"E": True})
        s.text = text
        return s


def _set_str(adapter, params: list, i: int, text: str) -> None:
    params[i] = adapter.str_like(params[i], text)


_SCRIPTY = re.compile(r"[{};=]|function|\$game|\$data|=>")


_SPEAKER = re.compile(r"^\s*([【\[（(「『]?)\s*([^\s。、！？!?「」『』…:：]{1,12}?)\s*([】\]）)」』]?)\s*[:：]?\s*$")
_BRACKETED = re.compile(r"^\s*([【\[（(]).+([】\]）)])\s*$|^\s*[^\s]{1,12}\s*[:：]\s*$")


def speaker_line(lines: list[str], known: set[str]) -> tuple[str, str, str] | None:
    """(lead, name, trail) if the first line of a message is a speaker label: bracketed, `Name:`, or exactly a known actor name."""
    if len(lines) < 2 or not is_japanese(lines[0]):
        return None
    mt = _SPEAKER.match(lines[0])
    if not mt:
        return None
    if _BRACKETED.match(lines[0]) or mt.group(2) in known:
        return mt.group(1), mt.group(2), mt.group(3) + (":" if lines[0].rstrip().endswith((":", "：")) else "")
    return None


def _join_lines(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        if out and out[-1].isascii() and out[-1].isalnum() and ln[:1].isascii() and ln[:1].isalnum():
            out += " "
        out += ln
    return out


# ---------------------------------------------------------------------------------------------------------------------
# event command lists (shared)

_TEXT_ARG = re.compile(r"(?i)^(text|message|msg|title|label|name|description|caption|word|words|content|tooltip|help|prompt)\d*$")
_MZ_NAME_CODES = {320: 1, 324: 1, 325: 1}      # change name / nickname / profile -> parameters[1]


def collect_event_list(cmds: list, adapter, where: str, wrap: Wrap, col: Collected, engine: str) -> None:
    """Units for the text commands in one event list; the list itself is rebuilt after translation."""
    segs: list[Any] = []     # ("keep", cmd) | ("text", header, line_cmds, unit) | ("scroll", header, line_cmds, unit)
    xp = engine == "XP"
    i = 0
    n = len(cmds)
    while i < n:
        c = cmds[i]
        code = adapter.code(c)
        if code == 101:
            j = i + 1
            while j < n and adapter.code(cmds[j]) == 401:
                j += 1
            lines = [adapter.text(adapter.params(x)[0]) if adapter.params(x) else "" for x in cmds[i + 1:j]]
            if xp:                                              # XP: the 101 command carries the first line itself
                lines = [adapter.text(adapter.params(c)[0]) if adapter.params(c) else ""] + lines
            sp_line = speaker_line(lines, col.known_names)
            sp_unit = None
            if sp_line:
                sp_unit = Unit("name", sp_line[1], where)
                col.units.append(sp_unit)
                lines = lines[1:]
            ja = _join_lines(lines)
            hp = adapter.params(c)
            if len(hp) > 4:                                     # MZ speaker name
                sp = adapter.text(hp[4])
                col.add("name", sp, where, lambda u, hp=hp, ad=adapter: _set_str(ad, hp, 4, u.en))
            if is_japanese(ja):
                u = Unit("dialogue", ja, where)
                col.units.append(u)
                segs.append(("text", c, cmds[i + 1:j], u, sp_unit, sp_line))
            else:
                if sp_unit is not None:
                    col.units.remove(sp_unit)
                segs.extend(("keep", x) for x in cmds[i:j])
            i = j
            continue
        if code == 105:
            j = i + 1
            while j < n and adapter.code(cmds[j]) == 405:
                j += 1
            lines = [adapter.text(adapter.params(x)[0]) if adapter.params(x) else "" for x in cmds[i + 1:j]]
            ja = _join_lines(lines)
            if is_japanese(ja):
                u = Unit("scroll", ja, where)
                col.units.append(u)
                segs.append(("scroll", c, cmds[i + 1:j], u))
            else:
                segs.extend(("keep", x) for x in cmds[i:j])
            i = j
            continue
        if code == 102:
            ps = adapter.params(c)
            if ps and isinstance(ps[0], list):
                for k, ch in enumerate(ps[0]):
                    col.add("choice", adapter.text(ch), where, lambda u, ps=ps, k=k, ad=adapter: ps[0].__setitem__(k, ad.str_like(ps[0][k], u.en)))
        elif code == 357 and adapter is _Json:                     # MZ plugin command: only arguments that are plainly text
            ps = adapter.params(c)
            args = ps[3] if len(ps) > 3 and isinstance(ps[3], dict) else {}
            for key, val in args.items():
                if isinstance(val, str) and _TEXT_ARG.match(str(key)):
                    col.add("message", val, f"{where}:{adapter.text(ps[0])}.{key}", lambda u, args=args, key=key: args.__setitem__(key, u.en))
        elif code in _MZ_NAME_CODES:
            ps = adapter.params(c)
            k = _MZ_NAME_CODES[code]
            if len(ps) > k:
                col.add("name" if code != 325 else "description", adapter.text(ps[k]), where,
                        lambda u, ps=ps, k=k, ad=adapter: _set_str(ad, ps, k, u.en))
        segs.append(("keep", c))
        i += 1

    if not any(s[0] != "keep" for s in segs):
        return

    def finish() -> None:
        out: list = []
        for s in segs:
            if s[0] == "keep":
                out.append(s[1])
                continue
            kind, header, line_cmds, u = s[:4]
            sp_unit, sp_line = (s[4], s[5]) if len(s) > 4 else (None, None)
            if not u.en or u.en == u.ja:
                out.append(header)
                out.extend(line_cmds)
                continue
            ind = adapter.indent(header)
            like = adapter.params(line_cmds[0])[0] if line_cmds else (adapter.params(header)[0] if adapter.params(header) else None)
            if kind == "text":
                face = bool(not xp and adapter.params(header) and adapter.text(adapter.params(header)[0]))
                lines = wrap_text(u.en, wrap.face_chars if face else wrap.chars)
                label = None
                if sp_line and sp_unit is not None and sp_unit.en:
                    soft = str.maketrans({"【": "[", "】": "]", "「": '"', "」": '"', "『": '"', "』": '"', "（": "(", "）": ")", "：": ":"})
                    label = f"{sp_line[0].translate(soft)}{sp_unit.en}{sp_line[2].translate(soft)}"
                    sp_unit.changed = True
                room = wrap.lines - (1 if label else 0)           # the label is repeated on every page
                pages = [lines[k:k + room] for k in range(0, len(lines), room)] or [[""]]
                for pi, page in enumerate(pages):
                    page_lines = ([label] if label else []) + page
                    if xp:                                          # first line in the 101 command, the others in 401s
                        out.append(adapter.make(101, ind, [adapter.str_like(like, page_lines[0] if page_lines else "")]))
                        out.extend(adapter.make(401, ind, [adapter.str_like(like, ln)]) for ln in page_lines[1:])
                        continue
                    out.append(header if pi == 0 else adapter.clone(header))
                    out.extend(adapter.make(401, ind, [adapter.str_like(like, ln)]) for ln in page_lines)
            else:
                lines = wrap_text(u.en, wrap.wide_chars)
                out.append(header)
                out.extend(adapter.make(405, ind, [adapter.str_like(like, ln)]) for ln in lines)
        cmds[:] = out

    col.finishers.append(finish)


# ---------------------------------------------------------------------------------------------------------------------
# MV / MZ JSON files

_J_FIELDS = {
    "Actors": {"name": "name", "nickname": "name", "profile": "description"},
    "Classes": {"name": "name"},
    "Items": {"name": "name", "description": "description"},
    "Weapons": {"name": "name", "description": "description"},
    "Armors": {"name": "name", "description": "description"},
    "Skills": {"name": "name", "description": "description", "message1": "message", "message2": "message"},
    "States": {"name": "name", "message1": "message", "message2": "message", "message3": "message", "message4": "message"},
    "Enemies": {"name": "name"},
}


def _jset(d, key):
    return lambda u: d.__setitem__(key, u.en)


def _desc_wrap(wrap: Wrap, en: str) -> str:
    return "\n".join(wrap_text(en, wrap.wide_chars))


def collect_json(name: str, data: Any, wrap: Wrap, col: Collected, engine: str, *, do_events=True, do_db=True, do_system=True) -> None:
    stem = name[:-5] if name.lower().endswith(".json") else name
    if stem in _J_FIELDS and do_db and isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            for key, kind in _J_FIELDS[stem].items():
                v = row.get(key)
                if isinstance(v, str):
                    if stem in ("Actors", "Enemies") and key == "name":
                        col.known_names.add(v)
                    if kind == "description":
                        col.add(kind, v, f"{name}#{row.get('id')}.{key}", lambda u, row=row, key=key: row.__setitem__(key, _desc_wrap(wrap, u.en)))
                    else:
                        col.add(kind, v, f"{name}#{row.get('id')}.{key}", _jset(row, key))
    elif stem == "System" and do_system and isinstance(data, dict):
        for key, kind in (("gameTitle", "title"), ("currencyUnit", "term")):
            if isinstance(data.get(key), str):
                col.add(kind, data[key], f"{name}.{key}", _jset(data, key))
        for key in ("elements", "skillTypes", "weaponTypes", "armorTypes", "equipTypes"):
            arr = data.get(key)
            if isinstance(arr, list):
                for i, v in enumerate(arr):
                    if isinstance(v, str):
                        col.add("term", v, f"{name}.{key}[{i}]", lambda u, arr=arr, i=i: arr.__setitem__(i, u.en))
        terms = data.get("terms")
        if isinstance(terms, dict):
            for key, v in terms.items():
                if isinstance(v, list):
                    for i, s in enumerate(v):
                        if isinstance(s, str):
                            col.add("term", s, f"{name}.terms.{key}[{i}]", lambda u, v=v, i=i: v.__setitem__(i, u.en))
                elif isinstance(v, dict):
                    for k2, s in v.items():
                        if isinstance(s, str):
                            col.add("term", s, f"{name}.terms.{key}.{k2}", _jset(v, k2))
    elif do_events and stem == "CommonEvents" and isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and isinstance(row.get("list"), list):
                collect_event_list(row["list"], _Json, f"{name}#{row.get('id')}", wrap, col, engine)
    elif do_events and stem == "Troops" and isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                for pi, pg in enumerate(row.get("pages") or []):
                    if isinstance(pg, dict) and isinstance(pg.get("list"), list):
                        collect_event_list(pg["list"], _Json, f"{name}#{row.get('id')}.page{pi}", wrap, col, engine)
    elif re.fullmatch(r"Map\d+", stem) and isinstance(data, dict):
        if do_system and isinstance(data.get("displayName"), str):
            col.add("map", data["displayName"], f"{name}.displayName", _jset(data, "displayName"))
        if do_events:
            for ev in data.get("events") or []:
                if isinstance(ev, dict):
                    for pi, pg in enumerate(ev.get("pages") or []):
                        if isinstance(pg, dict) and isinstance(pg.get("list"), list):
                            collect_event_list(pg["list"], _Json, f"{name}.event{ev.get('id')}.page{pi}", wrap, col, engine)


# ---------------------------------------------------------------------------------------------------------------------
# VX / VX Ace Marshal files

_R_FIELDS = {
    "RPG::Actor": {"@name": "name", "@nickname": "name", "@description": "description"},
    "RPG::Class": {"@name": "name", "@description": "description"},
    "RPG::Item": {"@name": "name", "@description": "description"},
    "RPG::Weapon": {"@name": "name", "@description": "description"},
    "RPG::Armor": {"@name": "name", "@description": "description"},
    "RPG::Skill": {"@name": "name", "@description": "description", "@message1": "message", "@message2": "message"},
    "RPG::State": {"@name": "name", "@message1": "message", "@message2": "message", "@message3": "message", "@message4": "message"},
    "RPG::Enemy": {"@name": "name"},
}
_R_FILES = {"Actors", "Classes", "Items", "Weapons", "Armors", "Skills", "States", "Enemies"}


def _rstr(obj, owner, key, where, col: Collected, kind: str, wrap: Wrap | None = None) -> None:
    if isinstance(obj, m.RString):
        def ap(u, obj=obj):
            obj.text = _desc_wrap(wrap, u.en) if (kind == "description" and wrap) else u.en
        col.add(kind, obj.text, where, ap)


def _rarr(arr, where, col: Collected, kind="term") -> None:
    if isinstance(arr, list):
        for i, s in enumerate(arr):
            _rstr(s, arr, i, f"{where}[{i}]", col, kind)


def _ruby_literals(src: str) -> list[tuple[int, int, str, str]]:
    """(start, end, quote, text) of every string literal on the code part of each line (a `#` outside a string starts a comment)."""
    out: list[tuple[int, int, str, str]] = []
    pos = 0
    for line in src.splitlines(keepends=True):
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if c == "#":
                break
            if c in "\"'":
                j = i + 1
                while j < n and line[j] != c:
                    j += 2 if line[j] == "\\" else 1
                if j < n:
                    out.append((pos + i, pos + j + 1, c, line[i + 1:j]))
                i = j + 1
                continue
            i += 1
        pos += n
    return out


def _collect_vocab(arr, name: str, col: Collected) -> None:
    """Ace / VX keep many battle and system messages in the `Vocab` script as string constants (Victory, ObtainExp ...). The
    constants are rewritten in the script source, so the Scripts file is changed only when something there was translated."""
    for entry in arr:
        if not (isinstance(entry, list) and len(entry) == 3 and sc._title(entry).strip() == "Vocab"):
            continue
        try:
            src = sc.source(entry)
        except Exception:  # noqa: BLE001
            return
        lits = [lit for lit in _ruby_literals(src) if is_japanese(lit[3])]
        units: list[tuple[Unit, tuple[int, int, str, str]]] = []
        for lit in lits:
            u = Unit("term", lit[3].replace("\\\"", '"').replace("\\'", "'"), f"{name}#Vocab")
            col.units.append(u)
            units.append((u, lit))

        def finish(entry=entry, src=src, units=units) -> None:
            out, last, changed = [], 0, False
            for u, (a, b, q, _t) in units:
                if u.en and u.en != u.ja:
                    esc = u.en.replace("\\", "\\\\").replace(q, "\\" + q)
                    out.append(src[last:a]); out.append(q + esc + q)
                    last, changed = b, True
            if changed:
                out.append(src[last:])
                entry[2] = m.RString(zlib.compress("".join(out).encode("utf-8"), 9), dict(entry[2].ivars or {}))
        col.finishers.append(finish)
        return


def collect_marshal(name: str, data: Any, wrap: Wrap, col: Collected, engine: str, *, do_events=True, do_db=True, do_system=True) -> None:
    stem = name.rsplit(".", 1)[0]
    if stem == "Scripts" and do_system and isinstance(data, list):
        _collect_vocab(data, name, col)
    elif stem in _R_FILES and do_db and isinstance(data, list):
        for row in data:
            if isinstance(row, m.RObject):
                for key, kind in _R_FIELDS.get(row.cls, {}).items():
                    if row.cls in ("RPG::Actor", "RPG::Enemy") and key == "@name" and isinstance(row.ivars.get(key), m.RString):
                        col.known_names.add(row.ivars[key].text)
                    _rstr(row.ivars.get(key), row, key, f"{name}#{row.ivars.get('@id')}.{key}", col, kind, wrap)
    elif stem == "System" and do_system and isinstance(data, m.RObject):
        iv = data.ivars
        _rstr(iv.get("@game_title"), data, "@game_title", f"{name}.game_title", col, "title")
        _rstr(iv.get("@currency_unit"), data, "@currency_unit", f"{name}.currency_unit", col, "term")
        for key in ("@elements", "@skill_types", "@weapon_types", "@armor_types"):
            _rarr(iv.get(key), f"{name}.{key}", col)
        words = iv.get("@words")                                   # XP: RPG::System::Words (HP, Gold, Equip ...)
        if isinstance(words, m.RObject):
            for key, v in words.ivars.items():
                _rstr(v, words, key, f"{name}.words.{key}", col, "term")
        terms = iv.get("@terms")
        if isinstance(terms, m.RObject):
            for key, v in terms.ivars.items():
                if isinstance(v, m.RString):
                    _rstr(v, terms, key, f"{name}.terms.{key}", col, "term")
                else:
                    _rarr(v, f"{name}.terms.{key}", col)
    elif do_events and stem == "CommonEvents" and isinstance(data, list):
        for row in data:
            if isinstance(row, m.RObject) and isinstance(row.ivars.get("@list"), list):
                collect_event_list(row.ivars["@list"], _Rb, f"{name}#{row.ivars.get('@id')}", wrap, col, engine)
    elif do_events and stem == "Troops" and isinstance(data, list):
        for row in data:
            if isinstance(row, m.RObject):
                for pi, pg in enumerate(row.ivars.get("@pages") or []):
                    if isinstance(pg, m.RObject) and isinstance(pg.ivars.get("@list"), list):
                        collect_event_list(pg.ivars["@list"], _Rb, f"{name}#{row.ivars.get('@id')}.page{pi}", wrap, col, engine)
    elif re.fullmatch(r"Map\d+", stem) and isinstance(data, m.RObject):
        if do_system:
            _rstr(data.ivars.get("@display_name"), data, "@display_name", f"{name}.display_name", col, "map")
        evs = data.ivars.get("@events")
        if do_events and isinstance(evs, dict):
            for eid, ev in evs.items():
                if isinstance(ev, m.RObject):
                    for pi, pg in enumerate(ev.ivars.get("@pages") or []):
                        if isinstance(pg, m.RObject) and isinstance(pg.ivars.get("@list"), list):
                            collect_event_list(pg.ivars["@list"], _Rb, f"{name}.event{eid}.page{pi}", wrap, col, engine)
