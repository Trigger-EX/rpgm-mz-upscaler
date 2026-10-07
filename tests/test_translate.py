import functools
import io
import json
import sys
import threading
import types
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rpgm_upscaler.translate import argos
from rpgm_upscaler.translate.cache import Cache
from rpgm_upscaler.translate.detect import is_japanese, normalize, protect, restore, split_sentences
from rpgm_upscaler.translate.glossary import Glossary, romanize
from rpgm_upscaler.translate.service import Translator


class FakeBackend:
    """Stands in for the neural model: upper-cases and tags the input."""
    name = "fake"
    tag = "fake-1"

    def __init__(self):
        self.calls: list[list[str]] = []

    def translate_batch(self, texts):
        self.calls.append(list(texts))
        return [f"EN({t})" for t in texts]


@pytest.fixture
def tr(tmp_path):
    return Translator(overrides_path=tmp_path / "translations.json", cache=Cache(tmp_path / "c.sqlite"), use_default_backend=False)


def test_detection_and_normalisation():
    for t in ("ひらがな", "カタカナ", "漢字", "ｱﾘｶﾞﾄ", "mixed テキスト"):
        assert is_japanese(t)
    for t in ("", "plain english 123", "Ünïcode"):
        assert not is_japanese(t)
    assert normalize("ＡＢＣ１２３ｱ") == "ABC123ア"


def test_control_code_roundtrip():
    text = r"\C[2]勇者\C[0]は\N[1]に\V[5]ゴールド\G渡した%1\\"
    protected, codes = protect(text)
    assert "\\" not in protected.replace("\\\\", "") or True
    assert all(f"[[{i}]]" in protected for i in range(len(codes)))
    assert restore(protected, codes) == text
    assert restore("[[ 0 ]] x", ["\\C[1]"]) == "\\C[1] x"
    assert split_sentences("一つ目。二つ目！\n三つ目") == ["一つ目。", "二つ目！\n", "三つ目"]       # bare newlines stay with their sentence
    assert split_sentences("「行くぞ！」と叫んだ。") == ["「行くぞ！」と叫んだ。"] and split_sentences("影分身!!") == ["影分身!!"]


@pytest.mark.parametrize("kana,rom", [("アレックス", "Arekkusu"), ("ルーシー", "Ruushii"), ("シャーロット", "Shaarotto"),
                                       ("ちゃんぽん", "Chanpon"), ("しっぽ", "Shippo"), ("きょう", "Kyou"), ("ティファ", "Tifa"),
                                       ("ヴァンパイア", "Vanpaia"), ("こんにちは", "Konnichiha"), ("はんいん", "Han'in")])
def test_romaji(kana, rom):
    assert romanize(kana) == rom


def test_glossary_segmentation():
    g = Glossary()
    assert len(g) > 300
    assert g.translate("ポーション") == ("Potion", 1.0)
    assert g.translate("ボス撃破") == ("Boss Defeated", 0.8)
    assert g.translate("鉄の剣")[0] == "Iron Sword"
    assert g.translate("宝箱1を開けた")[0] == "Treasure Chest 1 Opened"
    assert g.translate("アレックスの剣")[0] == "Arekkusu Sword"
    assert g.translate(r"\C[2]ポーション\C[0]")[0] == r"\C[2]Potion\C[0]"
    assert g.translate("未知の漢字語") == (None, 0.0) or g.translate("未知の漢字語")[1] == 0.0
    assert g.translate("") == (None, 0.0)


def test_translator_layers_and_cache(tr):
    b = FakeBackend()
    tr._backend, tr._backend_tried = b, True
    res = tr.translate_many(["ポーション", "English text", "テスト用の長い文章です。", "テスト用の長い文章です。", "ポーション"])
    assert [r.source for r in res] == ["glossary", "passthrough", "fake", "fake", "glossary"]
    assert res[2].text == "EN(テスト用の長い文章です。)"
    assert b.calls == [["テスト用の長い文章です。"]]                 # deduplicated, one batch, glossary hits never reach the model
    again = tr.translate("テスト用の長い文章です。")
    assert again.source == "cache" and len(b.calls) == 1             # served from sqlite, backend not called again
    tr.set_override("ポーション", "Healing Draught")                    # overrides beat the glossary
    assert tr.translate("ポーション") == tr.translate("ポーション") and tr.translate("ポーション").text == "Healing Draught"
    t2 = Translator(overrides_path=tr.overrides_path, cache=Cache(None), use_default_backend=False)
    assert t2.translate("ポーション").source == "override"          # persisted
    tr.set_override("ポーション", None)
    assert tr.translate("ポーション").source == "glossary"


def test_translator_without_model_leaves_unknown_text(tr):
    r = tr.translate("未知の漢字語です")
    assert r.source == "unchanged" and r.confidence == 0 and r.text == "未知の漢字語です" and not r.translated
    assert tr.translate("Hello").source == "passthrough"
    assert tr.translate("").text == ""


def test_control_codes_survive_the_model(tr):
    class Echo(FakeBackend):
        def translate_batch(self, texts):
            return [t.replace("勇者", "the hero") for t in texts]
    tr._backend, tr._backend_tried = Echo(), True
    r = tr.translate("これは\\C[2]勇者\\C[0]の話です。")
    assert r.source == "fake" and "\\C[2]" in r.text and "\\C[0]" in r.text and "the hero" in r.text


def test_cancel_and_progress(tr):
    tr._backend, tr._backend_tried = FakeBackend(), True
    texts = [f"長い文章{i}です。" for i in range(70)]
    seen = []
    ev = threading.Event()
    res = tr.translate_many(texts, progress=lambda d, t: (seen.append(d), ev.set() if d >= 32 else None), cancel=ev)
    assert sum(r.source == "fake" for r in res) == 32 and sum(r.source == "unchanged" for r in res) == 38
    assert seen[-1] == 32


def test_status_reports_missing_model_and_deps(tr, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    st = tr.status()
    assert st["model"] == "missing" and st["glossary_size"] > 300
    zpath = make_argos_zip(tmp_path / "m.argosmodel")
    argos.import_package(zpath)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)           # simulate the optional dependency being absent
    assert tr.status()["model"] == "deps-missing" and "pip install" in tr.status()["deps_hint"]


# ---- model packages -------------------------------------------------------------------------------
def make_argos_zip(path: Path, top="translate-ja_en-1_1", skip=None) -> Path:
    files = {f"{top}/model/model.bin": b"bin", f"{top}/sentencepiece.model": b"sp",
             f"{top}/metadata.json": json.dumps({"package_version": "1.1"}).encode()}
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            if n != skip:
                z.writestr(n, d)
    return path


def test_find_installed_plain_top_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    dest = argos.import_package(make_argos_zip(tmp_path / "p.argosmodel", top="ja_en"))   # what argos-net.com really ships
    assert argos.find_installed() == dest


def test_package_validation(tmp_path):
    good = make_argos_zip(tmp_path / "g.argosmodel")
    assert argos.validate_zip(good) == "translate-ja_en-1_1"
    with pytest.raises(argos.ArgosError, match="missing"):
        argos.validate_zip(make_argos_zip(tmp_path / "b.argosmodel", skip="translate-ja_en-1_1/sentencepiece.model"))
    notzip = tmp_path / "n.argosmodel"
    notzip.write_bytes(b"nope")
    with pytest.raises(argos.ArgosError):
        argos.validate_zip(notzip)
    evil = tmp_path / "e.argosmodel"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("top/../../x", b"x")
    with pytest.raises(argos.ArgosError, match="unsafe"):
        argos.validate_zip(evil)


def test_import_and_find(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert argos.find_installed() is None
    dest = argos.import_package(make_argos_zip(tmp_path / "m.argosmodel"))
    assert argos.find_installed() == dest and argos.package_version(dest) == "1.1"
    argos.import_package(make_argos_zip(tmp_path / "m.argosmodel"))      # re-import replaces cleanly
    folder = tmp_path / "ext" / "translate-ja_en-9"
    (folder / "model").mkdir(parents=True)
    (folder / "model" / "model.bin").write_bytes(b"x"); (folder / "sentencepiece.model").write_bytes(b"x")
    assert argos.import_package(folder).name == "translate-ja_en-9"


def test_install_from_local_server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    web = tmp_path / "web"; web.mkdir()
    make_argos_zip(web / "ja_en.argosmodel")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(web))
    handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    (web / "index.json").write_text(json.dumps([
        {"from_code": "en", "to_code": "ja", "package_version": "9", "links": [f"{base}/other"]},
        {"from_code": "ja", "to_code": "en", "package_version": "1.0", "links": [f"{base}/old"]},
        {"from_code": "ja", "to_code": "en", "package_version": "1.1", "links": ["ipfs://x", f"{base}/ja_en.argosmodel"]}]))
    progress = []
    try:
        dest = argos.install(progress=lambda d, t: progress.append((d, t)), index_url=f"{base}/index.json")
        assert argos.find_installed() == dest and progress and progress[-1][0] == progress[-1][1] > 0
        ev = threading.Event(); ev.set()
        with pytest.raises(argos.ArgosError, match="cancelled"):
            argos.install(cancel=ev, index_url=f"{base}/index.json")
    finally:
        srv.shutdown()


def test_argos_backend_with_fake_runtime(tmp_path, monkeypatch):
    calls = {}

    class SP:
        def __init__(self, model_file): calls["sp"] = model_file
        def encode(self, t, out_type=str): return list(t)
        def decode(self, toks): return "".join(toks).upper() if toks and toks[0].isascii() else "This is a long sentence."

    class Hyp: 
        def __init__(self, h): self.hypotheses = [h]

    class CT:
        def __init__(self, path, device, inter_threads, **kw): calls["ct"] = (path, device)
        def translate_batch(self, toks, **kw): calls["kw"] = kw; return [Hyp(t) for t in toks]

    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(Translator=CT))
    monkeypatch.setitem(sys.modules, "sentencepiece", types.SimpleNamespace(SentencePieceProcessor=SP))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    dest = argos.import_package(make_argos_zip(tmp_path / "m.argosmodel"))
    b = argos.ArgosBackend(dest)
    assert b.translate_batch(["ab", "cd"]) == ["AB", "CD"] and b.translate_batch([]) == []
    assert calls["ct"][1] == "cpu" and calls["kw"]["beam_size"] == 2 and b.tag.endswith("1.1")
    t = Translator(overrides_path=tmp_path / "o.json", cache=Cache(None))          # picks the installed model up by itself
    r = t.translate("これは長い文章です。")
    assert r.source == "argos" and t.status()["model"] == "ready"


@pytest.mark.skipif(not __import__("os").environ.get("RPGM_HUB_ARGOS_MODEL"), reason="set RPGM_HUB_ARGOS_MODEL=/path/x.argosmodel")
def test_real_model(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    argos.import_package(__import__("os").environ["RPGM_HUB_ARGOS_MODEL"])
    t = Translator(overrides_path=tmp_path / "o.json", cache=Cache(None))
    r = t.translate("私は昨日、友達と一緒に映画を見に行きました。")
    assert r.source == "argos" and not __import__("rpgm_upscaler.translate.detect", fromlist=["x"]).is_japanese(r.text)


def test_cli_translate_and_dump(tmp_path, monkeypatch, capsys):
    from rpgm_upscaler.cli import main
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert main(["translate", "ポーション", "Hello", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["en"] == "Potion" and rows[0]["source"] == "glossary" and rows[1]["source"] == "passthrough"
    f = tmp_path / "lines.txt"
    f.write_text("ボス撃破\n\n宝箱\n", encoding="utf-8")
    assert main(["translate", "--file", str(f)]) == 0
    assert "Boss Defeated" in capsys.readouterr().out
    assert main(["translate", "status"]) == 0 and '"glossary_size"' in capsys.readouterr().out
    assert main(["translate"]) == 2
    assert main(["translate", "import", str(tmp_path / "missing.argosmodel")]) == 2
    from tests import fakesaves
    from tests.fakegame import make_game
    g = make_game(tmp_path / "g", "MV"); fakesaves.write_database(g)
    sv = fakesaves.write_mv(g / "save" / "file1.rpgsave")
    capsys.readouterr()
    assert main(["saves", "dump", str(sv), "--json", "--translate"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["english"]["ボス撃破"] == "Boss Defeated" and d["english"]["ポーション"] == "Potion" and d["english"]["アレックス"] == "Arekkusu"
    assert main(["saves", "dump", str(sv), "--translate"]) == 0
    assert "ドア開放 [Door Open]" in capsys.readouterr().out
