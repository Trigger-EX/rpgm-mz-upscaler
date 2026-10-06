"""Optional end-to-end test: runs a real mkxp-z on a generated VX Ace game, original vs the Hires pack we write.

Enabled by setting RPGM_MKXPZ to a built mkxp-z binary (https://github.com/mkxp-z/mkxp-z, GPL). Needs xvfb-run, openbox,
xdotool and ImageMagick's `import`. RPGM_MKXPZ_RUBYLIB (os.pathsep-separated) is added as rubyLoadpath when the binary
cannot find Ruby's zlib by itself. The probe game draws one solid 64x48 picture; the Hires copy is recoloured so the
screenshot proves which texture the engine really used.
"""
import json
import os
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from PIL import Image

from rpgm_upscaler.cli import main
from rpgm_upscaler.rgss import marshal as m

MKXPZ = os.environ.get("RPGM_MKXPZ")
TOOLS = ("xvfb-run", "openbox", "xdotool", "import")
pytestmark = pytest.mark.skipif(not (MKXPZ and all(shutil.which(t) for t in TOOLS)),
                                reason="set RPGM_MKXPZ=/path/to/mkxp-z (and have xvfb-run, openbox, xdotool, import)")

SCRIPT = b'''
s = Sprite.new
s.bitmap = Bitmap.new("Graphics/Pictures/test")
s.x = 10; s.y = 10
3.times { Graphics.update }
File.write("probe.txt", "gfx=#{Graphics.width}x#{Graphics.height} bmp=#{s.bitmap.width}x#{s.bitmap.height}")
2000.times { Graphics.update }
exit
'''
DRIVER = """cd "$1"
openbox >/dev/null 2>&1 &
sleep 1
./Game >game.log 2>&1 &
sleep 6
xdotool search --class Game windowsize %@ 1920 1080 windowmove %@ 0 0 2>/dev/null
sleep 3
import -window root shot.png
kill %2
"""


def make_game(root: Path) -> None:
    (root / "Data").mkdir(parents=True)
    (root / "Graphics/Pictures").mkdir(parents=True)
    Image.new("RGBA", (64, 48), (200, 30, 30, 255)).save(root / "Graphics/Pictures/test.png")
    (root / "Game.ini").write_bytes(b"[Game]\r\nRTP=RPGVXAce\r\nLibrary=System\\RGSS301.dll\r\nScripts=Data\\Scripts.rvdata2\r\nTitle=Probe\r\n")
    arr = m.RArray([m.RArray([1, m.RString(b"Main", {"E": True}), m.RString(zlib.compress(SCRIPT), {})])])
    (root / "Data/Scripts.rvdata2").write_bytes(m.dumps(arr))


def shoot(game: Path, tmp: Path) -> Image.Image:
    shutil.copy(MKXPZ, game / "Game")                         # mkxp-z reads <exe name>.ini, so the binary must be called Game
    conf = {"rubyLoadpath": [p for p in os.environ.get("RPGM_MKXPZ_RUBYLIB", "").split(os.pathsep) if p]}
    old = game / "mkxp.json"
    if old.exists():
        conf = {**json.loads(old.read_text()), **conf}
    old.write_text(json.dumps(conf))
    (tmp / "drive.sh").write_text(DRIVER)
    env = {**os.environ, "SDL_AUDIODRIVER": "dummy", "ALSOFT_DRIVERS": "null", "LIBGL_ALWAYS_SOFTWARE": "1", "XDG_RUNTIME_DIR": str(tmp)}
    os.chmod(tmp, 0o700)
    subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24 +extension GLX", "bash", str(tmp / "drive.sh"), str(game)],
                   env=env, capture_output=True, timeout=120)
    return Image.open(game / "shot.png").convert("RGB")


def box(im: Image.Image, want) -> tuple[int, int]:
    px = im.load()
    pts = [(x, y) for y in range(im.height) for x in range(im.width) if want(*px[x, y])]
    assert pts, "colour not on screen"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def test_mkxpz_hires_pack(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_game(src)
    assert main(["run", str(src), "-o", str(out), "--engine", "lanczos"]) == 0
    hires = out / "Hires/Graphics/Pictures/test.png"
    assert Image.open(hires).size == (160, 120)                        # 64x48 at the 2.5x we chose for 1920x1080
    conf = json.loads((out / "mkxp.json").read_text())
    assert conf["enableHires"] and conf["textureScalingFactor"] == 2.5
    Image.new("RGBA", (160, 120), (20, 40, 220, 255)).save(hires)       # distinguishable from the original art
    im = shoot(out, tmp_path)
    assert (out / "probe.txt").read_text() == "gfx=544x416 bmp=64x48"   # the game still sees stock Ace sizes
    w, h = box(im, lambda r, g, b: b > 180 and r < 60)                  # blue = the Hires texture was drawn
    assert abs(w - 64 * 1080 / 416) <= 3 and abs(h - 48 * 1080 / 416) <= 3
