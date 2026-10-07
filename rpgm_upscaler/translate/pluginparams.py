"""Translate player-visible plugin parameters (menu labels, help text) in js/plugins.js without breaking references.

Plugin parameters mix display text with file names, switch ids, script snippets and nested JSON. We only touch a string when
it contains Japanese, is not code-like, does not name a real asset file, sits under a key that is not file/audio/script-like
and (for MZ plugins with an annotation block) is declared as a text type.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .detect import is_japanese
from .gametext import Collected

_KEY_SKIP = re.compile(r"(?i)(file|image|img|picture|\bpic\b|icon|bgm|bgs|audio|sound|font|switch|variable|script|eval|formula|regex|"
                       r"path|url|folder|animation|sprite|filter|skin|tileset|symbol|\bext\b|\bclass\b|\bhex\b|opacity|offset)")
_CODEY = re.compile(r"[{};]|=>|function\b|\$game|\$data|\bthis\.|\bconst\b|\bvar\b|\breturn\b")
_TEXT_TYPES = {"", "string", "text", "multiline_string", "note"}


def _asset_stems(root: Path) -> set[str]:
    stems: set[str] = set()
    for sub in ("img", "audio", "movies", "fonts", "icon"):
        d = root / sub
        if d.is_dir():
            for p in d.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(d).with_suffix("")
                    stems.add(rel.as_posix().lower())
                    stems.add(p.stem.lower())
    return stems


def declared_types(plugin_js: Path) -> dict[str, str]:
    """`@param Name` -> `@type X` from the plugin's annotation header (MZ and some MV plugins)."""
    try:
        text = plugin_js.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    types: dict[str, str] = {}
    cur = None
    for ln in text.splitlines():
        mt = re.match(r"\s*\*?\s*@param\s+(.+?)\s*$", ln)
        if mt:
            cur = mt.group(1)
            types.setdefault(cur, "")
            continue
        mt = re.match(r"\s*\*?\s*@type\s+(.+?)\s*$", ln)
        if mt and cur is not None:
            types[cur] = mt.group(1).strip().lower()
    return types


def _ok_text(key: str, val: str, ptype: str, stems: set[str]) -> bool:
    if not is_japanese(val):
        return False
    if _KEY_SKIP.search(key or ""):
        return False
    base = ptype[:-2] if ptype.endswith("[]") else ptype
    if base not in _TEXT_TYPES:
        return False
    if _CODEY.search(val):
        return False
    low = val.strip().lower()
    if low in stems or Path(low).stem in stems:
        return False
    return True


def collect_plugin_params(entries: list[dict], root: Path, col: Collected, where: str = "plugins.js") -> int:
    """Add units for visible plugin parameters; returns how many were added."""
    stems = _asset_stems(root)
    before = len(col.units)
    for e in entries:
        if not e.get("status", True):
            continue
        name = e.get("name", "?")
        types = declared_types(root / "js" / "plugins" / f"{name}.js")
        params = e.get("parameters")
        if isinstance(params, dict):
            for key in list(params):
                _walk_str(params, key, key, types.get(key, ""), stems, col, f"{where}:{name}.{key}")
    return len(col.units) - before


def _walk_str(container, k, key: str, ptype: str, stems: set[str], col: Collected, where: str) -> None:
    v = container[k]
    if not isinstance(v, str):
        return
    s = v.strip()
    if s[:1] in "[{" and s[-1:] in "]}":
        try:
            inner = json.loads(s)
        except ValueError:
            inner = None
        if isinstance(inner, (list, dict)):
            _walk_json(inner, key, "", stems, col, where)
            col.finishers.append(lambda container=container, k=k, inner=inner: container.__setitem__(
                k, json.dumps(inner, ensure_ascii=False, separators=(",", ":"))) if _changed(col, where) else None)
            return
    if _ok_text(key, v, ptype, stems):
        col.add("plugin", v, where, lambda u, container=container, k=k: container.__setitem__(k, u.en))


def _changed(col: Collected, where: str) -> bool:
    return any(u.changed and u.where.startswith(where) for u in col.units)


def _walk_json(node, key: str, ptype: str, stems: set[str], col: Collected, where: str) -> None:
    items = node.items() if isinstance(node, dict) else enumerate(node)
    for k, v in list(items):
        sub_key = k if isinstance(k, str) else key
        if isinstance(v, (list, dict)):
            _walk_json(v, sub_key, ptype, stems, col, f"{where}.{k}")
        elif isinstance(v, str):
            _walk_str(node, k, sub_key, ptype, stems, col, f"{where}.{k}")
