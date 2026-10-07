import pytest

from rpgm_upscaler.rgss import archive as ar

FILES = {
    "Data/Scripts.rvdata2": b"\x04\x08[\x00",
    "Graphics/Characters/Actor1.png": bytes(range(256)) * 5,
    "Graphics/System/IconSet.png": b"x",
    "Audio/BGM/empty.ogg": b"",
    "Data/odd1.bin": b"abc", "Data/odd2.bin": b"abcde", "Data/odd3.bin": b"abcdefg",
    "Graphics/Pictures/日本語.png": b"jp name",
}


@pytest.mark.parametrize("version", [1, 3])
def test_pack_unpack_roundtrip(tmp_path, version):
    f = tmp_path / "Game.rgss3a"
    f.write_bytes(ar.pack(FILES, version=version))
    a = ar.open_archive(f)
    assert a.version == version and [e.name for e in a.entries] == list(FILES)
    for name, data in FILES.items():
        assert a.read(name) == data
    assert a.exists("data/SCRIPTS.rvdata2") and not a.exists("nope")           # case-insensitive lookup
    out = tmp_path / "out"
    files = a.extract_all(out)
    assert len(files) == len(FILES)
    for name, data in FILES.items():
        assert (out / name).read_bytes() == data


def test_known_vector_v3():
    """Hand-computed v3 archive: seed 0, one 4-byte file 'a.b' (key = seed*9+3 = 3)."""
    blob = ar.pack({"a.b": b"ABCD"}, version=3, seed=0, data_keys={"a.b": 0})
    a = ar.Archive.__new__(ar.Archive)
    a.path, a.buf = None, blob
    a.version = 3
    assert a._parse()[0].name == "a.b"


@pytest.mark.parametrize("bad", [b"", b"RGSSAD", b"NOTRGSS\x03" + b"\0" * 20, b"RGSSAD\x00\x09" + b"\0" * 20])
def test_rejects_garbage(tmp_path, bad):
    f = tmp_path / "x.rgss3a"
    f.write_bytes(bad)
    with pytest.raises(ar.ArchiveError):
        ar.open_archive(f)


def test_truncated_and_corrupt(tmp_path):
    blob = ar.pack(FILES, version=3)
    f = tmp_path / "t.rgss3a"
    f.write_bytes(blob[:40])
    with pytest.raises(ar.ArchiveError):
        ar.open_archive(f)
    f.write_bytes(ar.pack(FILES, version=1)[:30])
    with pytest.raises(ar.ArchiveError):
        ar.open_archive(f)


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/path", "a/../../b", "C:/win.txt"])
def test_malicious_names_are_not_extracted(tmp_path, name):
    f = tmp_path / "e.rgss3a"
    f.write_bytes(ar.pack({name: b"pwned"}, version=3))
    a = ar.open_archive(f)
    with pytest.raises(ar.ArchiveError, match="unsafe"):
        a.extract_all(tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_vectorised_cipher_matches_the_reference_loop():
    import os
    from rpgm_upscaler.rgss import archive as a
    for n in (0, 1, 3, 4, 5, 7, 8, 1023, 4099):
        for key in (0, 1, 0xDEADCAFE, 0xFFFFFFFF):
            d = os.urandom(n)
            assert a._crypt_data(d, key) == a._crypt_data_py(d, key)


def test_archive_is_memory_mapped_and_closable(tmp_path):
    import mmap
    f = tmp_path / "Game.rgss3a"
    f.write_bytes(ar.pack({"Data/A.bin": b"x" * 1000}))
    with ar.open_archive(f) as a:
        assert isinstance(a.buf, mmap.mmap) and a.read("data/a.bin") == b"x" * 1000
    with pytest.raises(ValueError):
        a.buf.size()                                           # closed on leaving the block


def test_names_differing_only_in_case_extract_once_first_wins(tmp_path):
    f = tmp_path / "Game.rgss3a"
    f.write_bytes(ar.pack({"Graphics/Pictures/A.png": b"first", "graphics/pictures/a.PNG": b"second", "Data/B.bin": b"b"}))
    with ar.open_archive(f) as a:
        assert a.collisions == [("Graphics/Pictures/A.png", "graphics/pictures/a.PNG")]
        files = a.extract_all(tmp_path / "out")
    assert len(files) == 2 and (tmp_path / "out/Graphics/Pictures/A.png").read_bytes() == b"first"


def test_extract_never_writes_through_a_symlink(tmp_path):
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "keep.txt").write_text("original")
    dest = tmp_path / "dest"; dest.mkdir()
    (dest / "Graphics").symlink_to(outside, target_is_directory=True)
    (dest / "Data").mkdir()
    (dest / "Data" / "B.bin").symlink_to(outside / "keep.txt")
    f = tmp_path / "Game.rgss3a"
    f.write_bytes(ar.pack({"Data/B.bin": b"new"}))
    with ar.open_archive(f) as a:
        a.extract_all(dest)
    assert (outside / "keep.txt").read_text() == "original"            # the file symlink was replaced, not followed
    assert (dest / "Data/B.bin").read_bytes() == b"new" and not (dest / "Data/B.bin").is_symlink()
    f.write_bytes(ar.pack({"Graphics/C.png": b"x"}))
    with ar.open_archive(f) as a, pytest.raises(ar.ArchiveError, match="symbolic link"):
        a.extract_all(dest)
    assert not (outside / "C.png").exists()


def test_extract_can_be_limited_to_some_entries(tmp_path):
    f = tmp_path / "Game.rgss3a"
    f.write_bytes(ar.pack({"Data/Scripts.rvdata2": b"s", "Graphics/Big.png": b"b" * 100}))
    with ar.open_archive(f) as a:
        got = a.extract(tmp_path / "o", lambda n: n.startswith("Data/"))
    assert [p.name for p in got] == ["Scripts.rvdata2"] and not (tmp_path / "o/Graphics").exists()
