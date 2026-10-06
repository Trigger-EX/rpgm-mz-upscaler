import hashlib
import json
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest
from PIL import Image

from rpgm_upscaler.cli import main as cli_main
from rpgm_upscaler.core.planner import Plan
from rpgm_upscaler.core.settings import Options
from rpgm_upscaler.rgss import marshal as m
from rpgm_upscaler.rgss import patch, pipeline
from rpgm_upscaler.rgss import scripts as sc
from rpgm_upscaler.rgss.planner import build_plan
from rpgm_upscaler.rgss.project import load_rgss_project
from tests.fakeace import SCRIPTS, make_ace


def tree_hash(root: Path) -> str:
    h = hashlib.sha1()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(root).as_posix().encode()); h.update(f.read_bytes())
    return h.hexdigest()


def run(game, out, mode="hires", **kw):
    return pipeline.run(game, out, Options(workers=kw.pop("workers", 1), movies=False, **kw), mode)


@pytest.mark.parametrize("ace,archive", [(True, False), (True, True), (False, False), (False, True)])
def test_hires_full_run(tmp_path, ace, archive):
    g = make_ace(tmp_path / "g", ace=ace, archive=archive)
    before = tree_hash(g)
    res = run(g, tmp_path / "out")
    assert res.success, res.failed
    assert tree_hash(g) == before                                         # source never touched
    out = tmp_path / "out"
    n = 2.5                                                                # fit(544x416 -> 1920x1080) = floor(8*2.596)/8
    ch = Image.open(out / "Hires/Graphics/Characters/Actor1.png")
    assert ch.size == (12 * 80, 8 * 80)                                    # 32px cells -> 80px
    assert Image.open(out / "Hires/Graphics/Characters/$Big.png").size == (3 * 80, 4 * 80)
    assert Image.open(out / "Hires/Graphics/Faces/Actor1.png").size == (4 * 240, 2 * 240)
    assert Image.open(out / "Hires/Graphics/System/IconSet.png").size == (16 * 60, 8 * 60)   # 24px icons -> 60
    assert Image.open(out / "Hires/Graphics/Titles1/Title.png").size == (1360, 1040)
    assert Image.open(out / "Hires/Graphics/Animations/Fire.png").size == (5 * 480, 2 * 480)
    a1 = "Graphics/Tilesets/World_A1" if ace else "Graphics/System/TileA1"
    assert Image.open(out / f"Hires/{a1}.png").size == (32 * 40, 24 * 40)       # 16px half-tiles -> 40
    assert Image.open(out / "Hires/Graphics/Parallaxes/Sky.png").size == (400, 300)        # jpg -> png
    assert Image.open(out / "Hires/Graphics/Pictures/Old.png").size == (400, 300)          # bmp -> png
    # unique cell colour survives (no bleeding)
    src = Image.open(g / "Graphics/Characters/Actor1.png") if not archive else None
    if src is not None:
        assert ch.getpixel((80 * 5 + 40, 80 * 3 + 40)) == src.getpixel((32 * 5 + 16, 32 * 3 + 16))
    # originals still shipped, windowskin untouched and not duplicated
    assert (out / "Graphics/Characters/Actor1.png").exists() and (out / "Graphics/System/Window.png").exists()
    assert not (out / "Hires/Graphics/System/Window.png").exists()
    assert (out / "Audio/BGM/Theme.ogg").read_bytes() == b"OggS-fake" and (out / "Game.ini").exists()
    cfg = json.loads((out / "mkxp.json").read_text())
    assert cfg["enableHires"] is True and cfg["textureScalingFactor"] == n == cfg["framebufferScalingFactor"] == cfg["atlasScalingFactor"]
    assert cfg["rgssVersion"] == (3 if ace else 2) and cfg["fixedAspectRatio"] is True
    assert "mkxp-z" in (out / "README-HUB.txt").read_text()
    assert not list(out.rglob("*.tmp"))


def test_plan_categories_and_warnings(tmp_path):
    g = make_ace(tmp_path / "g")
    plan = build_plan(load_rgss_project(g), Options(movies=False, scale="3"))
    cats = plan.summary()
    assert cats["characters"] == 2 and cats["tilesets"] == 3 and cats["system"] == 2 and "battlebacks1" in cats
    assert any("Custom" in w and "unknown folder" in w for w in plan.warnings)
    assert "Graphics/System/Window.png" in plan.skipped_windowskins
    assert plan.scale.n == 3 and all(j.keep_source and j.dst.startswith("Hires/") for j in plan.jobs)
    skip = build_plan(load_rgss_project(g), Options(movies=False, skip=["characters"]))
    assert "characters" not in skip.summary()


def test_resume_and_options_digest(tmp_path):
    g = make_ace(tmp_path / "g", archive=True)
    first = run(g, tmp_path / "out")
    f = tmp_path / "out/Hires/Graphics/Faces/Actor1.png"
    mt = f.stat().st_mtime_ns
    again = run(g, tmp_path / "out")                                       # re-extracted archive: stable mtimes -> all resumed
    assert again.ok == 0 and again.skipped == first.ok and f.stat().st_mtime_ns == mt
    assert run(g, tmp_path / "out", scale="2").ok == first.ok              # new options redo everything


def test_archive_extraction_is_cleaned_up(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp")); (tmp_path / "tmp").mkdir()
    g = make_ace(tmp_path / "g", archive=True)
    run(g, tmp_path / "out")
    assert not list((tmp_path / "tmp").glob("rpgmhub_*"))


# ---- stock640 + scripts --------------------------------------------------------------------------
def test_scripts_roundtrip_and_insert(tmp_path):
    g = make_ace(tmp_path / "g")
    raw = (g / "Data/Scripts.rvdata2").read_bytes()
    arr = sc.load(raw)
    assert m.dumps(arr) == raw
    assert [e.title for e in sc.listing(arr)] == [t for t, _ in SCRIPTS]
    assert sc.source(arr[0]) == SCRIPTS[0][1]
    sc.insert_before_main(arr, patch.HUB_TITLE, "puts 1")
    sc.insert_before_main(arr, patch.HUB_TITLE, "puts 2")                  # replaces, never stacks
    titles = [e.title for e in sc.listing(arr)]
    assert titles == ["Game_Map", "Scene_Title", patch.HUB_TITLE, "Main"] and sc.source(arr[2]) == "puts 2"
    assert sc.remove(arr, patch.HUB_TITLE) and not sc.remove(arr, patch.HUB_TITLE)
    with pytest.raises(sc.ScriptsError):
        sc.load(m.dumps(m.RArray([1, 2])))


@pytest.mark.parametrize("ace", [True, False])
def test_stock640_inserts_script_idempotently(tmp_path, ace):
    g = make_ace(tmp_path / "g", ace=ace)
    out = tmp_path / "out"
    for _ in range(2):
        res = run(g, out, mode="stock640")
        assert res.success, res.failed
    path = out / ("Data/Scripts.rvdata2" if ace else "Data/Scripts.rvdata")
    arr = sc.load(path.read_bytes())
    titles = [e.title for e in sc.listing(arr)]
    assert titles.count(patch.HUB_TITLE) == 1 and titles[-1] == "Main" and titles[-2] == patch.HUB_TITLE
    assert "resize_screen(RPGMHub::WIDTH" in sc.source(arr[-2]) and "640" in sc.source(arr[-2])
    assert not (out / "Hires").exists() and not (out / "mkxp.json").exists()
    assert sc.load((g / path.relative_to(out)).read_bytes()) and len(sc.listing(sc.load((g / path.relative_to(out)).read_bytes()))) == 3


@pytest.mark.skipif(not shutil.which("ruby"), reason="ruby not installed")
def test_hub_script_is_valid_ruby_and_guarded(tmp_path):
    rb = tmp_path / "hub.rb"
    rb.write_text(patch.render_script())
    assert subprocess.run(["ruby", "-c", str(rb)], capture_output=True, text=True).returncode == 0
    ok = subprocess.run(["ruby", "-e", "module Graphics; def self.resize_screen(w,h); puts [w,h].inspect; end; end; load ARGV[0]", str(rb)],
                        capture_output=True, text=True)
    assert ok.stdout.strip() == "[640, 480]", ok.stderr
    for stub in ("module Graphics; end", "module Graphics; def self.resize_screen(w,h); raise ArgumentError; end; end"):
        r = subprocess.run(["ruby", "-e", stub + "; load ARGV[0]; puts 'survived'", str(rb)], capture_output=True, text=True)
        assert r.stdout.strip() == "survived", r.stderr


@pytest.mark.skipif(not shutil.which("ruby"), reason="ruby not installed")
def test_ruby_can_read_the_patched_scripts_file(tmp_path):
    g = make_ace(tmp_path / "g")
    run(g, tmp_path / "out", mode="stock640")
    script = ("require 'zlib'; a = Marshal.load(File.binread(ARGV[0]))\n"
              "a.each { |id, title, data| puts \"#{title}|#{Zlib::Inflate.inflate(data).force_encoding('UTF-8').lines.first.to_s.strip}\" }")
    r = subprocess.run(["ruby", "-e", script, str(tmp_path / "out/Data/Scripts.rvdata2")], capture_output=True, text=True)
    lines = r.stdout.splitlines()
    assert lines[2].startswith(patch.HUB_TITLE) and "RPGM Hub Resolution" in lines[2] and lines[3].startswith("Main"), r.stderr


def test_existing_mkxp_json_is_merged(tmp_path):
    g = make_ace(tmp_path / "g")
    (g / "mkxp.json").write_text('{\n // my settings\n "windowTitle": "Mine", "vsync": false,\n}\n')
    run(g, tmp_path / "out")
    cfg = json.loads((tmp_path / "out/mkxp.json").read_text())
    assert cfg["windowTitle"] == "Mine" and cfg["enableHires"] is True and cfg["vsync"] is True


# ---- CLI --------------------------------------------------------------------------------------
def test_cli_rgss(tmp_path, capsys):
    g = make_ace(tmp_path / "g", archive=True)
    assert cli_main(["analyze", str(g)]) == 0
    out = capsys.readouterr().out
    assert "VX Ace" in out and "544x416" in out and "Game.rgss3a" in out
    assert cli_main(["plan", str(g), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scale"] == 2.5
    assert cli_main(["scripts", str(g)]) == 0 and "Scene_Title" in capsys.readouterr().out
    assert cli_main(["scripts", str(g), "--extract", str(tmp_path / "rb")]) == 0
    assert (tmp_path / "rb/000_Game_Map.rb").read_text() == SCRIPTS[0][1]
    assert cli_main(["unpack", str(g), "-o", str(tmp_path / "x")]) == 0
    assert (tmp_path / "x/Graphics/Faces/Actor1.png").exists()
    assert cli_main(["run", str(g), "-o", str(tmp_path / "o"), "--workers", "1"]) == 0
    assert (tmp_path / "o/mkxp.json").exists()
    assert cli_main(["run", str(g), "-o", str(tmp_path / "o2"), "--mode", "stock640"]) == 0
    loose = make_ace(tmp_path / "loose")
    assert cli_main(["unpack", str(loose)]) == 1
    xp = tmp_path / "xp"; (xp / "Data").mkdir(parents=True); (xp / "Game.ini").write_text("[Game]\nLibrary=RGSS104E.dll\n")
    assert cli_main(["plan", str(xp)]) == 2
