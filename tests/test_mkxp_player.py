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
