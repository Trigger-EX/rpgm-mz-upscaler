import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rpgm_upscaler.detect import detect_engine
from rpgm_upscaler.saves import mvmz
from rpgm_upscaler.saves.database import load_names, strip_codes
from rpgm_upscaler.saves.files import find_saves, open_save
from rpgm_upscaler.saves.model import SaveError
from tests import fakesaves
from tests.fakegame import make_game

FIX = Path(__file__).parent / "fixtures" / "rgss"


def copy_fixture(tmp_path, game):
    dst = tmp_path / game
    shutil.copytree(FIX / game, dst)
    return dst


# ---- MV / MZ ----------------------------------------------------------------------------------
@pytest.mark.parametrize("writer,name,codec", [
    (fakesaves.write_mv, "file1.rpgsave", "lz"),
    (lambda p: fakesaves.write_mz(p, variant="zlib-utf8"), "file1.rmmzsave", "zlib-utf8"),
    (lambda p: fakesaves.write_mz(p, variant="zlib-raw"), "file1.rmmzsave", "zlib-raw"),
])
def test_json_save_view_edit_save_reload(tmp_path, writer, name, codec):
    f = writer(tmp_path / "save" / name)
    s = open_save(f)
    assert s.codec == codec and s.readonly is None
    assert s.get_switch(1) and not s.get_switch(2) and s.switch_count() == 4
    assert s.get_variable(3) == 250 and s.get_variable(5) == "text" and s.gold() == 1234
    assert s.party_ids() == [1, 2] and s.actor(1).name == "アレックス" and s.actor(1).level == 5 and s.actor(1).exp == 500
    assert s.inventory("items") == {1: 5, 3: 2} and s.position() == (3, 7, 9) and s.playtime() == "01:02:05"
    s.set_switch(2, True); s.set_switch(9, True); s.set_variable(3, 999); s.set_gold(5000)
    s.set_actor(2, level=10, hp=77, exp=1234); s.set_item("items", 4, 7); s.set_item("items", 1, 0)
    s.set_position(map_id=5, x=2, y=3)
    s.save()
    assert (tmp_path / "save" / (name + ".bak")).exists()
    t = open_save(f)
    assert t.codec == codec
    assert t.get_switch(2) and t.get_switch(9) and t.get_variable(3) == 999 and t.gold() == 5000
    a = t.actor(2)
    assert (a.level, a.hp, a.exp) == (10, 77, 1234)
    assert t.inventory("items") == {3: 2, 4: 7} and t.position() == (5, 2, 3)
    assert t.tree["actors"]["_data"]["@a"][1]["_name"] == "アレックス"       # untouched data and "@a" metadata preserved
    assert t.tree["player"]["_realX"] == 2 and t.tree["player"]["_transferring"] is False


def test_json_save_guards(tmp_path):
    f = fakesaves.write_mv(tmp_path / "file1.rpgsave")
    s = open_save(f)
    assert s.clamp_gold(10 ** 12) == 99999999 and s.clamp_gold(-5) == 0
    s.set_gold(10 ** 12)
    assert s.gold() == 99999999
    with pytest.raises(SaveError):
        s.set_actor(9, level=1)
    with pytest.raises(SaveError):
        s.set_item("gems", 1, 1)
    s.save(backup=False)
    other = open_save(f)
    other.set_gold(1); other.save()            # a second writer changes the file under the first one
    s.set_gold(2)
    with pytest.raises(SaveError, match="changed on disk"):
        s.save()


def test_backups_rotate(tmp_path):
    f = fakesaves.write_mv(tmp_path / "file1.rpgsave")
    for i in range(8):
        s = open_save(f); s.set_gold(i); s.save()
    assert len(list(tmp_path.glob("file1.rpgsave.bak*"))) == 5


def test_corrupt_and_unknown(tmp_path):
    bad = tmp_path / "file1.rpgsave"
    bad.write_text("this is !! not a save")
    with pytest.raises(SaveError):
        open_save(bad)
    with pytest.raises(SaveError):
        open_save(tmp_path / "missing.rpgsave")
    (tmp_path / "x.dat").write_bytes(b"")
    with pytest.raises(SaveError):
        open_save(tmp_path / "x.dat")


def test_nan_refused_and_unicode_kept(tmp_path):
    t = fakesaves.contents()
    t["party"]["_gold"] = float("nan")
    with pytest.raises(ValueError):
        mvmz.dump_json(t)
    assert "ソード" in mvmz.dump_json({"n": "ソード"})


# ---- Ace / VX ---------------------------------------------------------------------------------
@pytest.mark.parametrize("game,save,engine", [("ace_game", "Save01.rvdata2", "ACE"), ("vx_game", "Save1.rvdata", "VX")])
def test_marshal_save_unchanged_is_byte_identical_and_readable(tmp_path, game, save, engine):
    g = copy_fixture(tmp_path, game)
    s = open_save(g / save)
    assert s.engine == engine and s.readonly is None
    assert s.get_switch(1) and not s.get_switch(2) and s.switch_count() == 4
    assert s.get_variable(3) == 250 and s.gold() == 1234 and s.party_ids() == [1, 2]
    a = s.actor(1)
    assert a.name == "アレックス" and a.level == 5 and a.exp == 500
    assert s.inventory("items") == {1: 5, 3: 2} and s.position() == (3, 7, 9)
    if engine == "ACE":
        assert s.playtime() == "01:12:01"
    from rpgm_upscaler.rgss import marshal as m
    assert m.dump_all(s.streams) == (g / save).read_bytes()


@pytest.mark.parametrize("game,save", [("ace_game", "Save01.rvdata2"), ("vx_game", "Save1.rvdata")])
def test_marshal_save_edit_roundtrip(tmp_path, game, save):
    g = copy_fixture(tmp_path, game)
    s = open_save(g / save)
    s.set_switch(2, True); s.set_switch(8, True); s.set_variable(3, 777); s.set_variable(2, "hello")
    s.set_gold(4321); s.set_actor(1, level=50, hp=999, exp=12345); s.set_item("items", 2, 9); s.set_item("items", 3, 0)
    s.set_position(map_id=1, x=4, y=5)
    s.save()
    assert (g / (save + ".bak")).exists()
    t = open_save(g / save)
    assert t.readonly is None
    assert t.get_switch(2) and t.get_switch(8) and t.get_variable(3) == 777 and t.gold() == 4321
    assert t.get_variable(2).text == "hello"
    a = t.actor(1)
    assert (a.level, a.hp, a.exp) == (50, 999, 12345)
    assert t.inventory("items") == {1: 5, 2: 9} and t.position() == (1, 4, 5)
    real = t._o("Game_Player").ivars["@real_x"]
    assert real == (4.0 if game == "ace_game" else 4 * 256)
    assert t.actor(2).name == "Mia"


@pytest.mark.skipif(not shutil.which("ruby"), reason="ruby not installed")
@pytest.mark.parametrize("game,save", [("ace_game", "Save01.rvdata2"), ("vx_game", "Save1.rvdata")])
def test_real_ruby_reads_the_edited_save(tmp_path, game, save):
    g = copy_fixture(tmp_path, game)
    s = open_save(g / save)
    s.set_gold(777); s.set_switch(2, True); s.set_actor(1, level=42)
    s.save()
    script = (
        "module RPG; end; %w[BGM].each { |n| RPG.const_set(n, Class.new) }\n"
        "%w[Game_Switches Game_Variables Game_SelfSwitches Game_System Game_Message Game_Actors Game_Actor Game_Party Game_Troop "
        "Game_Map Game_Player Game_Screen Game_Timer].each { |n| Object.const_set(n, Class.new) }\n"
        f"d = File.binread('{g / save}'); io = StringIO.new(d); require 'stringio'; objs = []\n"
        "objs << Marshal.load(io) until io.eof?\n"
        "flat = objs.flat_map { |o| o.is_a?(Hash) ? o.values : [o] }\n"
        "party = flat.find { |o| o.class == Game_Party }; sw = flat.find { |o| o.class == Game_Switches }\n"
        "acts = flat.find { |o| o.class == Game_Actors }\n"
        "puts party.instance_variable_get(:@gold), sw.instance_variable_get(:@data)[2], "
        "acts.instance_variable_get(:@data)[1].instance_variable_get(:@level)\n"
    )
    r = subprocess.run(["ruby", "-rstringio", "-e", script], capture_output=True, text=True)
    assert r.stdout.split() == ["777", "true", "42"], r.stderr


def test_marshal_readonly_on_garbage(tmp_path):
    f = tmp_path / "Save01.rvdata2"
    f.write_bytes(b"\x04\x08[\x06i\x06")
    s = open_save(f)
    assert s.readonly
    with pytest.raises(SaveError, match="read-only"):
        s.set_gold(1)
    broken = tmp_path / "Save02.rvdata2"
    broken.write_bytes(b"\x04\x08[\x07i\x06")
    with pytest.raises(SaveError):
        open_save(broken)


# ---- names / detection / discovery ------------------------------------------------------------
def test_database_names_json_and_marshal(tmp_path):
    g = make_game(tmp_path / "g", "MV")
    fakesaves.write_database(g)
    n = load_names(g / "data")
    assert n.switch(2) == "ボス撃破" and n.variable(1) == "所持金" and n.items[1] == "ポーション" and n.actors[1] == "アレックス"
    assert n.maps[1] == "村" and n.currency == "G"
    ace = load_names(FIX / "ace_game" / "Save01.rvdata2")
    assert ace.switch(1) == "ドア開放" and ace.items[3] == "万能薬" and ace.weapons[1] == "鉄の剣" and ace.maps[3] == "Forest"
    vx = load_names(FIX / "vx_game")
    assert vx.actors[1] == "アレックス" and vx.classes[1] == "戦士"
    assert load_names(tmp_path).switches == []
    assert strip_codes(r"\C[2]Potion\C[0] \I[5]") == "Potion"


def test_detect_engine(tmp_path):
    mv = make_game(tmp_path / "mv", "MV")
    mz = make_game(tmp_path / "mz", "MZ", www=True)
    assert detect_engine(mv).engine == "MV" and detect_engine(mz).engine == "MZ" and detect_engine(mz).web == "www"
    assert detect_engine(FIX / "ace_game").engine == "ACE"
    assert detect_engine(FIX / "vx_game" / "Save1.rvdata").engine == "VX"
    assert detect_engine(FIX / "ace_game" / "Data").engine == "ACE"
    assert detect_engine(tmp_path / "nothing") is None
    xp = tmp_path / "xp"; (xp / "Data").mkdir(parents=True); (xp / "Game.ini").write_text("[Game]\nLibrary=RGSS104E.dll\n")
    assert detect_engine(xp).engine == "XP"
    arc = tmp_path / "enc"; arc.mkdir(); (arc / "Game.rgss3a").write_bytes(b"x")
    info = detect_engine(arc)
    assert info.engine == "ACE" and info.archive.name == "Game.rgss3a"


def test_find_saves(tmp_path):
    g = make_game(tmp_path / "g", "MV")
    for i in (1, 2, 10):
        fakesaves.write_mv(g / "save" / f"file{i}.rpgsave")
    fakesaves.write_mv(g / "save" / "global.rpgsave")
    names = [p.name for p in find_saves(g)]
    assert names == ["file1.rpgsave", "file2.rpgsave", "file10.rpgsave", "global.rpgsave"]
    assert [p.name for p in find_saves(FIX / "ace_game")] == ["Save01.rvdata2"]
    assert find_saves(g / "save" / "file1.rpgsave")[0].name == "file1.rpgsave"


# ---- CLI -------------------------------------------------------------------------------------
def test_cli_detect_list_dump_set(tmp_path, capsys):
    from rpgm_upscaler.cli import main
    g = make_game(tmp_path / "g", "MV")
    fakesaves.write_database(g)
    f = fakesaves.write_mv(g / "save" / "file1.rpgsave")
    assert main(["detect", str(g)]) == 0 and capsys.readouterr().out.startswith("MV")
    assert main(["detect", str(tmp_path / "nothing")]) == 2
    capsys.readouterr()
    assert main(["saves", "list", str(g)]) == 0 and "file1.rpgsave" in capsys.readouterr().out
    assert main(["saves", "dump", str(f), "--json", "--names"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["gold"] == 1234 and d["switch_names"]["2"] == "ボス撃破" and d["inventory"]["items"]["1"]["name"] == "ポーション"
    assert main(["saves", "dump", str(f), "--names"]) == 0
    assert "アレックス" in capsys.readouterr().out
    assert main(["saves", "set", str(f), "--switch", "2=on", "--var", "3=5", "--gold", "42", "--item", "weapons:2=3",
                 "--actor", "1:level=9", "--map", "2", "--pos", "4,5"]) == 0
    s = open_save(f)
    assert s.get_switch(2) and s.get_variable(3) == 5 and s.gold() == 42 and s.inventory("weapons")[2] == 3
    assert s.actor(1).level == 9 and s.position() == (2, 4, 5)
    with pytest.raises(SystemExit) as e:
        main(["saves", "set", str(f), "--gold", "abc"])
    assert e.value.code == 2
    assert main(["saves", "set", str(f), "--item", "bogus:1=1"]) == 2


def test_ace_position_when_real_xy_is_an_integer(tmp_path):
    """A standing Ace player holds Integer @real_x/@real_y; writing x*256 there would put the player far off the map."""
    g = copy_fixture(tmp_path, "ace_game")
    s = open_save(g / "Save01.rvdata2")
    pl = s._o("Game_Player")
    pl.ivars["@real_x"], pl.ivars["@real_y"] = 7, 8
    s.set_position(x=4, y=5)
    assert pl.ivars["@real_x"] == 4 and pl.ivars["@real_y"] == 5 and isinstance(pl.ivars["@real_x"], int)
