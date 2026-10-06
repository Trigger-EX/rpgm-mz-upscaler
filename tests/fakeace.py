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
