"""Builds fake VX Ace / VX projects (real Marshal files written by our codec, grid art like fakegame.py)."""
from __future__ import annotations

import zlib
from pathlib import Path

from PIL import Image

from rpgm_upscaler.rgss import archive as ar
from rpgm_upscaler.rgss import marshal as m
from tests.fakegame import grid_image

SCRIPTS = [("Game_Map", "class Game_Map; end"), ("Scene_Title", "class Scene_Title; end"), ("Main", "rgss_main { SceneManager.run }")]


def _s(text, ace):
    return m.RString(text.encode("utf-8"), {"E": True} if ace else {})


def scripts_blob(ace=True) -> bytes:
    arr = m.RArray()
    for i, (title, src) in enumerate(SCRIPTS):
        arr.append(m.RArray([1000 + i, _s(title, ace), m.RString(zlib.compress(src.encode("utf-8")), {})]))
    return m.dumps(arr)


def tilesets_blob(ace=True) -> bytes:
    names = lambda *n: m.RArray([_s(x, ace) for x in n])
    ts = m.RObject("RPG::Tileset", {"@tileset_names": names("World_A1", "", "", "", "Outside_A5", "Outside_B", "", "", "")})
    return m.dumps(m.RArray([None, ts]))


def make_ace(root: Path, ace=True, archive=False) -> Path:
    suffix = ".rvdata2" if ace else ".rvdata"
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    ini = ("[Game]\r\nRTP=RPGVXAce\r\nLibrary=System\\RGSS301.dll\r\nScripts=Data\\Scripts.rvdata2\r\nTitle=Fake Ace\r\n" if ace else
           "[Game]\r\nRTP=RPGVX\r\nLibrary=RGSS202E.DLL\r\nScripts=Data\\Scripts.rvdata\r\nTitle=Fake VX\r\n")
    files["Data/Scripts" + suffix] = scripts_blob(ace)
    files["Data/Tilesets" + suffix] = tilesets_blob(ace)
    files["Data/System" + suffix] = m.dumps(m.RObject("RPG::System", {"@switches": m.RArray([None])}))
    imgs = {
        "Graphics/Characters/Actor1": (384, 256, 32, 32), "Graphics/Characters/$Big": (96, 128, 32, 32),
        "Graphics/Faces/Actor1": (384, 192, 96, 96), "Graphics/System/IconSet": (384, 192, 24, 24),
        "Graphics/System/Balloon": (256, 320, 32, 32), "Graphics/System/Window": (128, 128, 128, 128),
        "Graphics/Animations/Fire": (960, 384, 192, 192), "Graphics/Pictures/Pic": (200, 100, 200, 100),
        "Graphics/Titles1/Title": (544, 416, 272, 208), "Graphics/Battlers/Slime": (120, 90, 120, 90),
        "Graphics/Custom/Thing": (64, 64, 32, 32),
    }
    if ace:
        imgs.update({"Graphics/Tilesets/World_A1": (512, 384, 16, 16), "Graphics/Tilesets/Outside_A5": (256, 512, 32, 32),
                     "Graphics/Tilesets/Outside_B": (512, 512, 32, 32), "Graphics/Battlebacks1/Grass": (544, 416, 272, 208)})
    else:
        imgs.update({"Graphics/System/TileA1": (512, 384, 16, 16), "Graphics/System/TileB": (512, 512, 32, 32)})
    for name, (w, h, cw, ch) in imgs.items():
        import io
        buf = io.BytesIO()
        grid_image(w, h, cw, ch).save(buf, "PNG")
        files[name + ".png"] = buf.getvalue()
    # a jpg and a bmp title/parallax, as RGSS allows
    for name, fmt in (("Graphics/Parallaxes/Sky.jpg", "JPEG"), ("Graphics/Pictures/Old.bmp", "BMP")):
        import io
        buf = io.BytesIO()
        Image.new("RGB", (160, 120), (10, 120, 200)).save(buf, fmt)
        files[name] = buf.getvalue()
    files["Audio/BGM/Theme.ogg"] = b"OggS-fake"
    if archive:
        root.joinpath("Game.rgss3a" if ace else "Game.rgss2a").write_bytes(ar.pack(files, version=3 if ace else 1))
    else:
        for name, data in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    root.joinpath("Game.ini").write_bytes(ini.encode("cp932"))
    if archive:
        (root / "Game.exe").write_bytes(b"MZ-fake")
    return root


def make_xp(root: Path, archive=False) -> Path:
    """A fake RPG Maker XP project (RGSS1): .rxdata Marshal files, 4x4 character sheets, autotiles, Windowskins."""
    import io
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    arr = m.RArray()
    for i, (title, src) in enumerate(SCRIPTS):
        arr.append(m.RArray([1000 + i, _s(title, False), m.RString(zlib.compress(src.encode("utf-8")), {})]))
    files["Data/Scripts.rxdata"] = m.dumps(arr)
    words = m.RObject("RPG::System::Words", {"@gold": m.RString("ゴールド".encode()), "@hp": m.RString("HP".encode()),
                                              "@equip": m.RString("装備".encode())})
    files["Data/System.rxdata"] = m.dumps(m.RObject("RPG::System", {"@words": words, "@elements": m.RArray([None, m.RString("炎".encode())]),
                                                                    "@switches": m.RArray([None])}))
    actor = m.RObject("RPG::Actor", {"@id": 1, "@name": m.RString("アレックス".encode()), "@class_id": 1})
    files["Data/Actors.rxdata"] = m.dumps(m.RArray([None, actor]))
    item = m.RObject("RPG::Item", {"@id": 1, "@name": m.RString("ポーション".encode()), "@description": m.RString("回復する薬。".encode())})
    files["Data/Items.rxdata"] = m.dumps(m.RArray([None, item]))
    iv = lambda c, p: m.RObject("RPG::EventCommand", {"@code": c, "@indent": 0, "@parameters": m.RArray(p)})
    s = lambda t: m.RString(t.encode())
    lst = m.RArray([iv(101, [s("アレックス")]), iv(401, [s("今日はいい天気ですね。")]),
                    iv(101, [s("こんにちは、魔王。")]), iv(401, [s("今日はいい天気ですね。")]),
                    iv(102, [m.RArray([s("はい"), s("いいえ")]), 2]), iv(0, [])])
    page = m.RObject("RPG::Event::Page", {"@list": lst})
    ev = m.RObject("RPG::Event", {"@id": 1, "@pages": m.RArray([page])})
    files["Data/Map001.rxdata"] = m.dumps(m.RObject("RPG::Map", {"@events": m.RHash({1: ev})}))
    imgs = {"Graphics/Characters/Hero": (128, 192, 32, 48), "Graphics/Tilesets/Town": (256, 512, 32, 32),
            "Graphics/Autotiles/Grass": (96, 128, 32, 32), "Graphics/Animations/Fire": (960, 384, 192, 192),
            "Graphics/Windowskins/Skin": (128, 128, 128, 128), "Graphics/Icons/Sword": (24, 24, 24, 24),
            "Graphics/Battlers/Slime": (120, 90, 120, 90), "Graphics/Titles/Title": (640, 480, 320, 240),
            "Graphics/Panoramas/Sky": (640, 480, 320, 240), "Graphics/Pictures/Pic": (200, 100, 200, 100)}
    for name, (w, h, cw, ch) in imgs.items():
        buf = io.BytesIO()
        grid_image(w, h, cw, ch).save(buf, "PNG")
        files[name + ".png"] = buf.getvalue()
    files["Audio/BGM/Theme.ogg"] = b"OggS-fake"
    if archive:
        root.joinpath("Game.rgssad").write_bytes(ar.pack(files, version=1))
        (root / "Game.exe").write_bytes(b"MZ-fake")
    else:
        for name, data in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    root.joinpath("Game.ini").write_bytes(b"[Game]\r\nLibrary=RGSS104E.dll\r\nScripts=Data\\Scripts.rxdata\r\nTitle=Fake XP\r\nRTP1=Standard\r\n")
    return root


def write_xp_save(path: Path, map_id=3) -> Path:
    """An XP save: `Save1.rxdata` is twelve consecutive Marshal dumps (characters, frame count, then the $game_* objects)."""
    s = lambda t: m.RString(t.encode())
    actor = lambda i, name, lvl: m.RObject("Game_Actor", {"@actor_id": i, "@name": s(name), "@level": lvl, "@exp": lvl * 100, "@hp": 300, "@sp": 40,
                                                          "@class_id": 1})
    a1, a2 = actor(1, "アレックス", 5), actor(2, "ミア", 4)
    streams = [
        m.RArray([m.RArray([s("Hero"), 0])]),
        40 * 3725,                                                   # Graphics.frame_count: 1:02:05 at 40 fps
        m.RObject("Game_System", {"@save_disabled": False}),
        m.RObject("Game_Switches", {"@data": m.RArray([None, True, False, None, True])}),
        m.RObject("Game_Variables", {"@data": m.RArray([None, 0, 0, 250])}),
        m.RObject("Game_SelfSwitches", {"@data": m.RHash()}),
        m.RObject("Game_Screen", {"@tone": 0}),
        m.RObject("Game_Actors", {"@data": m.RArray([None, a1, a2])}),
        m.RObject("Game_Party", {"@actors": m.RArray([a1, a2]), "@gold": 1234, "@items": m.RHash({1: 5, 3: 2}),
                                 "@weapons": m.RHash({1: 1}), "@armors": m.RHash()}),
        m.RObject("Game_Troop", {"@enemies": m.RArray()}),
        m.RObject("Game_Map", {"@map_id": map_id}),
        m.RObject("Game_Player", {"@x": 7, "@y": 9, "@real_x": 7 * 128, "@real_y": 9 * 128, "@direction": 2, "@transferring": False,
                                  "@new_map_id": 0, "@new_x": 0, "@new_y": 0, "@new_direction": 0}),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(m.dump_all(streams))
    return path
