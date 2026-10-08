import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from rpgm_upscaler.core.settings import Options
from rpgm_upscaler.rgss import mkxp, patch
from rpgm_upscaler.rgss.planner import build_plan
from rpgm_upscaler.rgss.project import load_rgss_project
from tests.fakeace import make_ace

INDEX = ('<a href="https://nightly.link/mkxp-z/mkxp-z/workflows/autobuild/dev/mkxp-z.linux.debian.trixie.x86_64.dev-abc1234.zip">x</a>'
         '<a href="https://nightly.link/mkxp-z/mkxp-z/workflows/autobuild/dev/mkxp-z.linux.ubuntu.xenial.x86_64.dev-abc1234.zip">y</a>'
         '<a href="https://nightly.link/mkxp-z/mkxp-z/workflows/autobuild/dev/mkxp-z.windows.msys2.x86_64.dev-abc1234.zip">z</a>')


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("RPGM_MKXPZ_DIR", raising=False)


def fake_player(folder: Path) -> Path:
    (folder / "scripts" / "preload").mkdir(parents=True)
    (folder / "scripts" / "preload" / "win32_wrap.rb").write_text("# wrap")
    (folder / "stdlib").mkdir()
    (folder / "stdlib" / "English.rb").write_text("# lib")
    (folder / "mkxp-z.x86_64").write_bytes(b"\x7fELF" + b"0" * 1_200_000)
    (folder / "mkxp.json").write_text("{}")
    (folder / "LICENSE.txt").write_text("GPL")
    return folder


def player_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        zi = zipfile.ZipInfo("mkxp-z.x86_64"); zi.external_attr = 0o100755 << 16
        z.writestr(zi, b"\x7fELF" + b"0" * 1_200_000)
        z.writestr("scripts/preload/win32_wrap.rb", "# wrap")
        z.writestr("mkxp.json", "{}")
    return buf.getvalue()


def test_requirement_only_for_rgss_hires_without_a_player(tmp_path):
    o = Options()
    assert mkxp.requirement("MV", "hires", o) is None and mkxp.requirement("ACE", "stock640", o) is None
    msg = mkxp.requirement("ACE", "hires", o)
    assert msg and "Install mkxp-z" in msg and "nightly.link" in msg and "build it from" in msg
    assert "--allow-no-player" in mkxp.requirement("VX", "hires", o, cli=True)
    assert mkxp.requirement("ACE", "hires", Options(allow_no_player=True)) is None
    assert mkxp.requirement("ACE", "hires", Options(bundle_player=False)) is None
    folder = fake_player(tmp_path / "p")
    assert mkxp.requirement("ACE", "hires", Options(mkxp_path=str(folder))) is None
    assert mkxp.locate(str(tmp_path / "empty")) is None


def test_find_latest_prefers_xenial_and_install_unpacks_the_player(tmp_path, monkeypatch):
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(mkxp, "open_url", lambda url, timeout=30: R(INDEX.encode()))
    url, name = mkxp.find_latest(machine="x86_64")
    assert "xenial" in url and name.startswith("mkxp-z.linux.ubuntu.xenial.x86_64")
    with pytest.raises(mkxp.MkxpError):
        mkxp.find_latest(machine="riscv64")

    def fake_download(url, dest, progress=None, cancel=None, timeout=60):
        Path(dest).write_bytes(player_zip())
        progress and progress(10, 10)
    monkeypatch.setattr(mkxp, "download", fake_download)
    seen = []
    where = mkxp.install(lambda d, t: seen.append((d, t)), machine="x86_64")
    exe = mkxp.player_exe(where)
    assert exe is not None and os.access(exe, os.X_OK) and seen
    assert mkxp.locate() == where and "xenial" in mkxp.version(where)
    assert mkxp.requirement("ACE", "hires", Options()) is None


def test_install_rejects_unsafe_and_empty_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(mkxp, "find_latest", lambda *a, **k: ("http://x/a.zip", "n"))
    for entries in ({"../evil": b"x"}, {"readme.txt": b"nothing"}):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for k, v in entries.items():
                z.writestr(k, v)
        monkeypatch.setattr(mkxp, "download", lambda url, dest, *a, _b=buf.getvalue(), **k: Path(dest).write_bytes(_b))
        with pytest.raises(mkxp.MkxpError):
            mkxp.install()
    assert mkxp.locate() is None and not (tmp_path / "evil").exists()


def hires(tmp_path, **opt):
    g = make_ace(tmp_path / "g", ace=True)
    plan = build_plan(load_rgss_project(g), Options(**opt), "hires")
    out = tmp_path / "out"; out.mkdir()
    return plan, out, patch.apply_hires(plan, out)


def test_hires_export_carries_the_player_and_the_win32_preloads(tmp_path):
    folder = fake_player(tmp_path / "player")
    plan, out, written = hires(tmp_path, mkxp_path=str(folder))
    assert "Game" in written and os.access(out / "Game", os.X_OK)
    assert (out / "scripts" / "preload" / "win32_wrap.rb").is_file() and (out / "stdlib" / "English.rb").is_file()
    cfg = json.loads((out / "mkxp.json").read_text())      # the player's own default mkxp.json must not replace ours
    assert cfg["enableHires"] is True and "scripts/preload/win32_wrap.rb" in cfg["preloadScript"]
    assert "Run 'Game'" in (out / "README-HUB.txt").read_text()
    assert not (out / "scripts" / "preload" / "hub_trgssx.rb").exists()


def test_hires_without_a_player_warns_and_readme_explains(tmp_path):
    plan, out, written = hires(tmp_path)
    assert "Game" not in written and not (out / "Game").exists()
    assert any("mkxp-z player is not installed" in w for w in plan.warnings)
    assert "Install mkxp-z" in (out / "README-HUB.txt").read_text()


def test_trgssx_games_get_a_version_stand_in(tmp_path):
    import zlib
    from rpgm_upscaler.rgss import marshal as m
    from tests.fakeace import SCRIPTS, _s
    g = make_ace(tmp_path / "g", ace=True)
    arr = m.RArray()
    for i, (t, src) in enumerate([*SCRIPTS[:2], ("ビットマップ拡張", "Win32API.new('TRGSSX', 'DllGetVersion', 'v', 'l')"), SCRIPTS[2]]):
        arr.append(m.RArray([1000 + i, _s(t, True), m.RString(zlib.compress(src.encode("utf-8")), {})]))
    (g / "Data" / "Scripts.rvdata2").write_bytes(m.dumps(arr))
    plan = build_plan(load_rgss_project(g), Options(), "hires")
    out = tmp_path / "out"; out.mkdir()
    patch.apply_hires(plan, out)
    rb = (out / "scripts" / "preload" / "hub_trgssx.rb").read_text()
    assert "TRGSSX" in rb and "DllGetVersion" in rb
    assert "scripts/preload/hub_trgssx.rb" in json.loads((out / "mkxp.json").read_text())["preloadScript"]
    assert any("TRGSSX.dll" in w for w in plan.warnings)


def test_fix_export_repairs_an_old_export_without_touching_images(tmp_path, monkeypatch):
    from rpgm_upscaler.rgss import fonts
    fdir = tmp_path / "sysfonts"; fdir.mkdir()
    (fdir / "IPAGothic.ttf").write_bytes(b"fake")
    monkeypatch.setattr(fonts, "SYSTEM_DIRS", [str(fdir)])
    monkeypatch.setattr(fonts, "_fc_list", lambda: {})
    monkeypatch.setattr(fonts, "japanese_fonts", lambda: [])
    import shutil
    g = make_ace(tmp_path / "g", ace=True)
    out = tmp_path / "out"
    shutil.copytree(g, out)                                    # a real export holds the game's Data/ and Game.ini too
    plan = build_plan(load_rgss_project(g), Options(), "hires")
    patch.apply_hires(plan, out)
    hero = out / "Hires" / "Graphics" / "Faces"; hero.mkdir(parents=True)
    (hero / "keep.png").write_bytes(b"png")
    # what the broken versions wrote: mixed-case keys, a file-name target, a stray italic font, no player
    cfg = json.loads((out / "mkxp.json").read_text())
    cfg["fontSub"] = ["UmePlus Gothic>ipag", "メイリオ>ipag", "my font>mine"]
    cfg["preloadScript"] = []
    (out / "mkxp.json").write_text(json.dumps(cfg))
    (out / "Fonts").mkdir(exist_ok=True); (out / "Fonts" / "ipag.ttf").write_bytes(b"old")
    player = fake_player(tmp_path / "player")
    notes = patch.refresh_export(out, str(player))
    new = json.loads((out / "mkxp.json").read_text())
    assert "umeplus gothic>ipagothic" in new["fontSub"] and "UmePlus Gothic>ipag" not in new["fontSub"] and "メイリオ>ipag" not in new["fontSub"]
    assert "my font>mine" not in new["fontSub"]                          # points at no font in Fonts/
    assert "scripts/preload/win32_wrap.rb" in new["preloadScript"] and new["textureScalingFactor"] == 2.5
    assert os.access(out / "Game", os.X_OK) and (hero / "keep.png").read_bytes() == b"png"
    assert notes and notes[0].startswith("updated")
    with pytest.raises(ValueError):
        patch.refresh_export(tmp_path / "nothing")


def test_fix_export_replaces_the_misnamed_stand_in_files_of_older_versions(tmp_path, monkeypatch):
    """Older versions wrote 'Fonts/UmePlus Gothic.ttf' (a stand-in under the font's name). mkxp-z keys fonts by the family inside
    the file, so that file provided nothing, yet it made the tool think the font was there and skip the fontSub entry."""
    import shutil
    from rpgm_upscaler.rgss import fonts
    fdir = tmp_path / "sysfonts"; fdir.mkdir()
    (fdir / "IPAGothic.ttf").write_bytes(b"fake")
    monkeypatch.setattr(fonts, "SYSTEM_DIRS", [str(fdir)])
    monkeypatch.setattr(fonts, "_fc_list", lambda: {})
    monkeypatch.setattr(fonts, "japanese_fonts", lambda: [])
    real_family = fonts.font_family
    monkeypatch.setattr(fonts, "font_family", lambda p: "ipagothic" if p.stem == "UmePlus Gothic" else real_family(p))
    g = make_ace(tmp_path / "g", ace=True)
    out = tmp_path / "out"; shutil.copytree(g, out)
    (out / "mkxp.json").write_text("{}")
    (out / "Fonts").mkdir(); (out / "Fonts" / "UmePlus Gothic.ttf").write_bytes(b"stand-in under the wrong name")
    patch.refresh_export(out)
    cfg = json.loads((out / "mkxp.json").read_text())
    assert "umeplus gothic>ipagothic" in cfg["fontSub"]
    assert not (out / "Fonts" / "UmePlus Gothic.ttf").exists() and (out / "Fonts" / "IPAGothic.ttf").is_file()


def test_scripts_with_string_ids_can_be_listed_and_patched():
    import zlib
    from rpgm_upscaler.rgss import marshal as m, scripts as sc
    arr = m.RArray([m.RArray([m.RString(b"7", {}), m.RString(b"Main", {}), m.RString(zlib.compress(b"x"), {})])])
    assert sc.listing(arr)[0].id == 7
    sc.insert_before_main(arr, "extra", "y")
    assert len(arr) == 2


def test_fix_export_restores_audio_the_export_lacks(tmp_path):
    import shutil
    g = make_ace(tmp_path / "g", ace=True)
    (g / "Audio" / "SE").mkdir(parents=True)
    (g / "Audio" / "SE" / "hit.ogg").write_bytes(b"se")
    out = tmp_path / "out"; shutil.copytree(g, out)
    (out / "mkxp.json").write_text("{}")
    (out / "Audio" / "SE" / "hit.ogg").unlink()
    (out / "Audio" / "BGM" / "Theme.ogg").write_bytes(b"export copy")
    notes = patch.refresh_export(out, source=g)
    assert (out / "Audio" / "SE" / "hit.ogg").read_bytes() == b"se"
    assert (out / "Audio" / "BGM" / "Theme.ogg").read_bytes() == b"export copy"      # existing files are never replaced
    assert any("restored 1 file" in n for n in notes)


def test_rtp_is_found_in_a_lutris_prefix_and_a_missing_rtp_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home")); monkeypatch.delenv("WINEPREFIX", raising=False); monkeypatch.delenv("RPGM_RTP", raising=False)
    g = make_ace(tmp_path / "g", ace=False)                      # Game.ini asks for the RPGVX RTP
    plan = build_plan(load_rgss_project(g), Options(), "hires")
    out = tmp_path / "out1"; out.mkdir()
    patch.apply_hires(plan, out)
    warn = [w for w in plan.warnings if "RTP" in w and "not found" in w]
    assert warn and "RPGVX" in warn[0] and "Characters/Vehicle" in warn[0] and "--rtp" in warn[0]

    rtp = tmp_path / "home/Games/my-game/drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS2/RPGVX"
    (rtp / "Graphics" / "Characters").mkdir(parents=True)
    assert patch.wine_prefixes() == [tmp_path / "home/Games/my-game"]
    plan = build_plan(load_rgss_project(g), Options(), "hires")
    out = tmp_path / "out2"; out.mkdir()
    patch.apply_hires(plan, out)
    assert json.loads((out / "mkxp.json").read_text())["RTP"] == [str(rtp)]
    assert not any("not found" in w and "RTP" in w for w in plan.warnings)

    mine = tmp_path / "elsewhere" / "VXRTP"; (mine / "Graphics").mkdir(parents=True)
    plan = build_plan(load_rgss_project(g), Options(rtp_path=str(mine)), "hires")
    out = tmp_path / "out3"; out.mkdir()
    patch.apply_hires(plan, out)
    assert json.loads((out / "mkxp.json").read_text())["RTP"][0] == str(mine)
