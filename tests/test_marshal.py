import shutil
import subprocess
from pathlib import Path

import pytest

from rpgm_upscaler.rgss import marshal as m

FIX = Path(__file__).parent / "fixtures" / "rgss"


@pytest.mark.parametrize("name", ["types.bin", "multi.bin"])
def test_real_ruby_fixtures_roundtrip_byte_exact(name):
    data = (FIX / name).read_bytes()
    objs = m.load_all(data)
    assert m.dump_all(objs) == data


def test_types_decoded():
    top = m.loads((FIX / "types.bin").read_bytes())
    assert top[:5] == [None, True, False, 0, 1]
    assert top[19] == -(2**30) - 1 and top[22] == 2**70 + 12345 and isinstance(top[22], m.RBignum)
    floats = [x for x in top if isinstance(x, m.RFloat)]
    assert 0.1 in floats and any(abs(x - 1 / 3) < 1e-15 for x in floats) and 1e20 in floats
    assert any(str(x) == '-0.0' for x in floats)
    strs = [x for x in top if isinstance(x, m.RString)]
    assert "ひらがな漢字" in [s.text for s in strs]
    arr = next(x for x in top if isinstance(x, m.RArray) and len(x) == 3 and isinstance(x[0], m.RString) and x[0] is x[1])
    assert arr[0] is arr[1]                       # object links resolve to the same Python object
    thing = next(x for x in top if isinstance(x, m.RObject) and x.cls == "Thing")
    assert thing.ivars["@self"] is thing and thing.ivars["@name"].text == "ソード"
    tbl = next(x for x in top if isinstance(x, m.RUserDef) and x.cls == "Table")
    t = m.Table(tbl.data)
    assert (t.x, t.y, t.z, t.cells) == (2, 3, 1, [1, -2, 3, 4, 5, 6]) and t.dump() == tbl.data


def test_multiple_streams():
    a, b, c = m.load_all((FIX / "multi.bin").read_bytes())
    assert a[m.Sym("playtime_s")] == 1234 and b[m.Sym("gold")] == 500 and list(c) == [1, 2, 3]
    with pytest.raises(m.MarshalError):
        m.loads((FIX / "multi.bin").read_bytes())


def test_errors():
    with pytest.raises(m.MarshalError):
        m.loads(b"\x04\x08?")
    with pytest.raises(m.MarshalError):
        m.loads(b"\x04\x08[\x07i\x06")
    with pytest.raises(m.MarshalError):
        m.loads(b"junk")


def test_edit_keeps_aliasing_and_new_values():
    top = m.loads((FIX / "types.bin").read_bytes())
    arr = next(x for x in top if isinstance(x, m.RArray) and len(x) == 3 and isinstance(x[0], m.RString) and x[0] is x[1])
    arr[0].text = "changed ✓"
    f = next(x for x in top if isinstance(x, m.RFloat) and x == 0.1)
    top[top.index(f)] = m.RFloat(0.25)
    top[3] = 99999
    again = m.loads(m.dumps(top))
    arr2 = next(x for x in again if isinstance(x, m.RArray) and len(x) == 3 and isinstance(x[0], m.RString) and x[0] is x[1])
    assert arr2[0].text == "changed ✓" and arr2[0] is arr2[1]
    assert again[3] == 99999 and 0.25 in again


@pytest.mark.skipif(not shutil.which("ruby"), reason="ruby not installed")
def test_ruby_loads_what_we_write(tmp_path):
    top = m.loads((FIX / "types.bin").read_bytes())
    arr = next(x for x in top if isinstance(x, m.RArray) and len(x) == 3 and isinstance(x[0], m.RString) and x[0] is x[1])
    arr[0].text = "edited"
    top[3] = 4242
    f = tmp_path / "edited.bin"
    f.write_bytes(m.dumps(top))
    script = (
        "class Table; def self._load(s); s; end; end; class Color; def self._load(s); s; end; end\n"
        "class Wrapped; def marshal_load(a); @v=a[0]; end; end; module Mixin; end; class MyStr<String; end; class MyArr<Array; end\n"
        "class MyHash<Hash; end; Pt=Struct.new(:x,:y); class Thing; end\n"
        f"a = Marshal.load(File.binread('{f}'))\n"
        "s = a.find { |x| x.is_a?(Array) && x.size == 3 && x[0].equal?(x[1]) && x[0].is_a?(String) }\n"
        "puts a[3], s[0], s[0].equal?(s[1])\n"
    )
    r = subprocess.run(["ruby", "-e", script], capture_output=True, text=True)
    assert r.stdout.split() == ["4242", "edited", "true"], r.stderr
