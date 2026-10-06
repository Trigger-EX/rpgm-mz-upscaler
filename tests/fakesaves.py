"""MV / MZ save builders (structure as written by JsonEx)."""
from __future__ import annotations

import json
import zlib
from pathlib import Path

from rpgm_upscaler.saves import lzstring


def contents():
    actor = lambda i, name, lvl: {"@": "Game_Actor", "_actorId": i, "_name": name, "_level": lvl, "_classId": 1,
                                  "_exp": {"@c": 30 + i, "1": lvl * 100}, "_hp": 150 + i, "_mp": 20, "_tp": 0, "_paramPlus": [0] * 8}
    return {
        "system": {"@": "Game_System", "_framesOnSave": 60 * 3725, "_saveCount": 2},
        "screen": {"@": "Game_Screen"},
        "switches": {"@": "Game_Switches", "_data": {"@a": [None, True, False, True, None]}},
        "variables": {"@": "Game_Variables", "_data": {"@a": [None, 10, 0, 250, None, "text"]}},
        "selfSwitches": {"@": "Game_SelfSwitches", "_data": {"1,2,A": True}},
        "actors": {"@": "Game_Actors", "_data": {"@a": [None, actor(1, "アレックス", 5), actor(2, "Mia", 3)]}},
        "party": {"@": "Game_Party", "_gold": 1234, "_steps": 99, "_items": {"@c": 45, "1": 5, "3": 2}, "_weapons": {"@c": 46, "1": 1}, "_armors": {"@c": 47},
                  "_actors": {"@c": 44, "@a": [1, 2]}},
        "map": {"@": "Game_Map", "_mapId": 3},
        "player": {"@": "Game_Player", "_x": 7, "_y": 9, "_realX": 7, "_realY": 9, "_transferring": False},
    }


def write_mv(path: Path, tree=None) -> Path:
    text = json.dumps(tree if tree is not None else contents(), ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lzstring.compress_to_base64(text), encoding="ascii")
    return path


def write_mz(path: Path, tree=None, variant="zlib-utf8") -> Path:
    text = json.dumps(tree if tree is not None else contents(), ensure_ascii=False, separators=(",", ":"))
    z = zlib.compress(text.encode("utf-8"), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(z if variant == "zlib-raw" else z.decode("latin-1").encode("utf-8"))
    return path


def write_database(web: Path) -> None:
    d = web / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "System.json").write_text(json.dumps({"switches": ["", "ドア開放", "ボス撃破", "Chest opened", ""],
                                               "variables": ["", "所持金", "Steps", "", "", "Text var"],
                                               "currencyUnit": "G", "gameTitle": "テスト"}), encoding="utf-8")
    mk = lambda names: [None] + [{"id": i + 1, "name": n} for i, n in enumerate(names)]
    for base, names in (("Actors", ["アレックス", "Mia"]), ("Items", ["ポーション", "Ether", "万能薬"]),
                        ("Weapons", ["鉄の剣", "Staff"]), ("Armors", ["革の鎧"]), ("Classes", ["戦士"])):
        (d / f"{base}.json").write_text(json.dumps(mk(names)), encoding="utf-8")
    (d / "MapInfos.json").write_text(json.dumps([None, {"id": 1, "name": "村"}, {"id": 3, "name": "Forest"}]), encoding="utf-8")
