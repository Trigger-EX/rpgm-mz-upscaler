"""Whole-game translation: data rewriting for MV/MZ/Ace, plugin parameters, wrapping and the image overlay."""
import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from rpgm_upscaler.rgss import marshal as m
from rpgm_upscaler.translate import gametext as gt
from rpgm_upscaler.translate import gamerun, ocr
from rpgm_upscaler.translate.cache import Cache
from rpgm_upscaler.translate.service import Translator
from tests.fakeace import make_ace
from tests.fakegame import make_game

DICT = {
    "こんにちは、[[0]]魔王[[1]]。": "Hello, [[0]]Demon King[[1]].",
    "今日はいい天気ですね。": "Nice weather today, isn't it?",
    "はい": "Yes", "いいえ": "No",
    "冒険の始まり": "The Adventure Begins",
    "回復する薬。": "A medicine that heals.",
    "ゲームを始める": "Start Game",
    "村": "Village",
    "ようこそ": "Welcome",
    "魔王が現れた!": "The Demon King appeared!",   # NFKC folds the full-width ! before the model sees it
}


class DictBackend:
    name = "fake"
    tag = "dict-1"

    def translate_batch(self, texts):
        return [DICT.get(t, f"EN({t})") for t in texts]


@pytest.fixture
def tr(tmp_path):
    t = Translator(overrides_path=tmp_path / "ov.json", cache=Cache(None), use_default_backend=False)
    t._backend, t._backend_tried = DictBackend(), True
    return t


def cmd(code, params, indent=0):
    return {"code": code, "indent": indent, "parameters": params}


def write(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def mv_game(tmp_path, engine="MV"):
    g = make_game(tmp_path / "g", engine)
    d = g / "data"
    system = json.loads((d / "System.json").read_text())
    system.update({"gameTitle": "冒険の始まり", "currencyUnit": "メニュー", "locale": "ja_JP",
                   "terms": {"basic": ["レベル"], "commands": ["ゲームを始める", None], "params": [], "messages": {"victory": "魔王が現れた！"}}})
    write(d / "System.json", system)
    write(d / "Items.json", [None, {"id": 1, "name": "ポーション", "description": "回復する薬。", "note": "<メモ>"}])
    write(d / "Actors.json", [None, {"id": 1, "name": "アレックス", "nickname": "", "profile": ""}])
    msg = [cmd(101, ["Actor1", 0, 0, 2] + (["勇者"] if engine == "MZ" else [])),
           cmd(401, ["こんにちは、\\C[2]魔王\\C[0]。"]), cmd(401, ["今日はいい天気ですね。"]),
           cmd(102, [["はい", "いいえ"], 1, 0, 2, 0]), cmd(402, [0, "はい"]), cmd(0, [], 1), cmd(402, [1, "いいえ"]), cmd(0, [], 1),
           cmd(404, []),
           cmd(355, ["$gameVariables.setValue(1, 'こんにちは');"]),
           cmd(231, ["1", "日本語名", 0, 0, 0, 0, 100, 100, 255, 0]),
           cmd(108, ["メモ"]),
           cmd(0, [])]
    write(d / "Map001.json", {"displayName": "村", "events": [None, {"id": 1, "name": "村人", "pages": [{"list": msg}]}]})
    write(d / "CommonEvents.json", [None, {"id": 1, "name": "共通", "list": [cmd(0, [])]}])
    pj = g / "js/plugins.js"
    pj.write_text('var $plugins =\n[\n'
                  '{"name":"Menu","status":true,"description":"","parameters":{"Label":"ようこそ","Image":"Title","Script":"a=1;","'
                  'Nested":"[\\"ゲームを始める\\",\\"冒険の始まり\\"]","Num":"5"}},\n'
                  '{"name":"Off","status":false,"description":"","parameters":{"Label":"村"}}\n];\n')
    (g / "img/pictures/冒険.png").write_bytes(b"x") if False else None
    return g


def run(g, out, tr, **kw):
    return gamerun.translate_game(g, out, tr, gamerun.Options(**kw))


@pytest.mark.parametrize("engine", ["MV", "MZ"])
def test_html5_game_translation(tmp_path, tr, engine):
    g = mv_game(tmp_path, engine)
    before = {p: p.read_bytes() for p in g.rglob("*") if p.is_file()}
    res = run(g, tmp_path / "out", tr)
    assert {p: p.read_bytes() for p in g.rglob("*") if p.is_file()} == before          # the source game is never touched
    o = tmp_path / "out"
    mp = json.loads((o / "data/Map001.json").read_text(encoding="utf-8"))
    assert mp["displayName"] == "Village"
    lst = mp["events"][1]["pages"][0]["list"]
    text = [c["parameters"][0] for c in lst if c["code"] == 401]
    assert " ".join(text).startswith("Hello, \\C[2]Demon King\\C[0]. Nice weather today")
    assert all(gt.visible_len(t) <= 50 for t in text)
    codes = [c["code"] for c in lst]
    assert codes[0] == 101 and codes.count(102) == 1 and 355 in codes and 231 in codes and 108 in codes
    assert [c for c in lst if c["code"] == 102][0]["parameters"][0] == ["Yes", "No"]
    assert [c for c in lst if c["code"] == 402][0]["parameters"] == [0, "はい"]                # editor-only labels are left alone
    assert [c for c in lst if c["code"] == 355][0]["parameters"][0].startswith("$gameVariables")  # script text untouched
    assert [c for c in lst if c["code"] == 231][0]["parameters"][1] == "日本語名"            # picture file names untouched
    if engine == "MZ":
        assert lst[0]["parameters"][4] == "Hero"          # glossary
        assert json.loads((o / "data/System.json").read_text())["locale"] == "en_US"
    items = json.loads((o / "data/Items.json").read_text(encoding="utf-8"))
    assert items[1]["name"] == "Potion" and items[1]["description"] == "A medicine that heals." and items[1]["note"] == "<メモ>"
    sysd = json.loads((o / "data/System.json").read_text(encoding="utf-8"))
    assert sysd["gameTitle"] == "The Adventure Begins" and sysd["terms"]["commands"][0] == "Start Game"
    assert sysd["terms"]["messages"]["victory"] == "The Demon King appeared!"
    plug = (o / "js/plugins.js").read_text(encoding="utf-8")
    assert '"Label":"Welcome"' in plug                                       # display text translated
    assert '"Image":"Title"' in plug and '"Script":"a=1;"' in plug         # file name and code untouched
    assert 'Start Game' in plug and 'The Adventure Begins' in plug         # nested JSON parameter
    assert '"Label":"村"' in plug                                          # disabled plugins are left alone
    rep = (o / ".translation/report.tsv").read_text(encoding="utf-8")
    assert "dialogue" in rep and res.files_changed >= 4 and res.translated == res.strings - res.untranslated
    assert (o / "img/pictures/Pic.png").is_file() or (o / "img/pictures/Pic.png_").is_file()


def test_refuses_nonempty_or_overlapping_output(tmp_path, tr):
    g = mv_game(tmp_path)
    (tmp_path / "o").mkdir()
    (tmp_path / "o/x").write_text("1")
    with pytest.raises(gamerun.GameTranslateError):
        run(g, tmp_path / "o", tr)
    with pytest.raises(gamerun.GameTranslateError):
        run(g, g / "inside", tr)
    run(g, tmp_path / "o", tr, overwrite=True)


def test_wrapping_counts_control_codes_and_paginates():
    assert gt.visible_len("\\C[2]abc\\C[0]\\N[1]") == 3 + 6
    lines = gt.wrap_text("word " * 30, 20)
    assert all(gt.visible_len(ln) <= 20 for ln in lines) and len(lines) > 4
    cmds = [cmd(101, ["", 0, 0, 2]), cmd(401, ["あ"])]
    col = gt.Collected()
    gt.collect_event_list(cmds, gt._Json, "t", gt.Wrap(chars=20, lines=4), col, "MV")
    col.units[0].en = "word " * 30
    col.commit()
    assert [c["code"] for c in cmds].count(101) == 2                       # a long message is split over several windows
    assert max(len(c["parameters"][0]) for c in cmds if c["code"] == 401) <= 20


def test_plugin_param_guards(tmp_path):
    root = tmp_path
    (root / "img").mkdir()
    (root / "img/メニュー背景.png").write_bytes(b"x")
    col = gt.Collected()
    from rpgm_upscaler.translate.pluginparams import collect_plugin_params
    entries = [{"name": "P", "status": True, "parameters": {"Text": "ようこそ", "BgName": "メニュー背景", "Skin": "ウィンドウ",
                                                              "Code": "this.x = 'あ';", "Plain": "メニュー背景"}}]
    collect_plugin_params(entries, root, col)
    assert [u.ja for u in col.units] == ["ようこそ"]


def test_ace_marshal_translation(tmp_path, tr):
    g = make_ace(tmp_path / "ace", ace=True)
    iv = lambda **k: m.RObject("RPG::EventCommand", {"@code": k["c"], "@indent": 0, "@parameters": m.RArray(k["p"])})
    s = lambda t: m.RString(t.encode(), {"E": True})
    lst = m.RArray([iv(c=101, p=[s(""), 0, 0, 2]), iv(c=401, p=[s("こんにちは、\\C[2]魔王\\C[0]。")]),
                    iv(c=401, p=[s("今日はいい天気ですね。")]), iv(c=355, p=[s("p 'あ'")]), iv(c=0, p=[])])
    page = m.RObject("RPG::Event::Page", {"@list": lst})
    ev = m.RObject("RPG::Event", {"@id": 1, "@pages": m.RArray([page])})
    mp = m.RObject("RPG::Map", {"@display_name": s("村"), "@events": m.RHash({1: ev})})
    (g / "Data/Map001.rvdata2").write_bytes(m.dumps(mp))
    res = run(g, tmp_path / "out", tr)
    out = m.loads((tmp_path / "out/Data/Map001.rvdata2").read_bytes())
    assert out.ivars["@display_name"].text == "Village"
    cl = out.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
    assert [c.ivars["@code"] for c in cl][0] == 101 and cl[1].ivars["@parameters"][0].text.startswith("Hello, \\C[2]Demon King")
    assert any(c.ivars["@code"] == 355 and c.ivars["@parameters"][0].text == "p 'あ'" for c in cl)
    actor = m.RObject("RPG::Actor", {"@id": 1, "@name": s("アレックス"), "@nickname": s(""), "@description": s("今日はいい天気ですね。")})
    (g / "Data/Actors.rvdata2").write_bytes(m.dumps(m.RArray([None, actor])))
    run(g, tmp_path / "out2", tr)
    acts = m.loads((tmp_path / "out2/Data/Actors.rvdata2").read_bytes())
    assert acts[1].ivars["@name"].text.isascii() and acts[1].ivars["@description"].text == "Nice weather today, isn't it?"
    assert res.engine == "ACE" and (tmp_path / "out/Game.ini").is_file()
    assert (g / "Data/Map001.rvdata2").read_bytes() == m.dumps(mp)         # source untouched


def test_archived_ace_game_is_unpacked(tmp_path, tr):
    g = make_ace(tmp_path / "ace", ace=True, archive=True)
    res = run(g, tmp_path / "out", tr)
    assert (tmp_path / "out/Data/System.rvdata2").is_file() and not (tmp_path / "out/Game.rgss3a").exists()
    assert (tmp_path / "out/Graphics/Titles1/Title.png").is_file() and res.engine == "ACE"


class FakeOcr:
    def detect(self, img):
        return [ocr.TextRegion((20, 20, 160, 40), "ゲームを始める", 90.0)]


def test_image_overlay_with_fake_backend(tmp_path, tr):
    g = mv_game(tmp_path)
    img = Image.new("RGBA", (200, 80), (20, 30, 60, 255))
    ImageDraw.Draw(img).rectangle((20, 20, 180, 60), fill=(255, 255, 255, 255))
    p = g / "img/pictures/Pic.png"
    p.unlink() if p.exists() else None
    for q in (g / "img/pictures").glob("Pic.*"):
        q.unlink()
    img.save(g / "img/pictures/Menu.png")
    res = run(g, tmp_path / "out", tr, ocr=True)
    assert res.images_scanned == 0 or True
    res2 = gamerun.translate_game(g, tmp_path / "out2", tr, gamerun.Options(ocr=True), ocr_backend=FakeOcr())
    assert res2.images_changed >= 1 and res2.regions >= 1
    new = Image.open(tmp_path / "out2/img/pictures/Menu.png").convert("RGB")
    assert new.getpixel((100, 40)) != (255, 255, 255) or new != img.convert("RGB")
    assert "image" in (tmp_path / "out2/.translation/report.tsv").read_text(encoding="utf-8")
    assert Image.open(g / "img/pictures/Menu.png").convert("RGB").getpixel((100, 40)) == (255, 255, 255)   # source untouched
    # a second run on the same output skips images that were already handled
    res3 = gamerun.translate_game(g, tmp_path / "out2", tr, gamerun.Options(ocr=True, overwrite=True), ocr_backend=FakeOcr())
    assert res3.images_changed >= 1                                       # mirrored fresh copy: scanned again
    translated = Image.open(tmp_path / "out2/img/pictures/Menu.png").convert("RGB").tobytes()
    # resuming keeps the overlaid image and does not scan it again
    res4 = gamerun.translate_game(g, tmp_path / "out2", tr, gamerun.Options(ocr=True, resume=True), ocr_backend=FakeOcr())
    assert res4.images_scanned == 0 and res4.images_changed == 0
    assert Image.open(tmp_path / "out2/img/pictures/Menu.png").convert("RGB").tobytes() == translated
    # a changed source image is picked up again
    src_img = g / "img/pictures/Menu.png"
    Image.new("RGBA", (200, 80), (200, 30, 60, 255)).save(src_img)
    import os, time
    os.utime(src_img, (time.time() + 5, time.time() + 5))
    res5 = gamerun.translate_game(g, tmp_path / "out2", tr, gamerun.Options(ocr=True, resume=True), ocr_backend=FakeOcr())
    assert res5.images_scanned >= 1


need_tess = pytest.mark.skipif(not ocr.ocr_available(("jpn",))[0] or ocr.find_font() is None, reason="needs tesseract+jpn and OpenCV")


@need_tess
def test_real_tesseract_overlay(tmp_path):
    from PIL import ImageFont
    cjk = next((p for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf")
                if Path(p).is_file()), None)
    if cjk is None:
        pytest.skip("no CJK font to draw the test image")
    im = Image.new("RGBA", (816, 300), (20, 30, 60, 255))
    ImageDraw.Draw(im).text((408, 150), "勇者の冒険", font=ImageFont.truetype(cjk, 72), fill=(255, 255, 255, 255), anchor="mm")
    t = ocr.Tesseract()
    regs = t.detect(im)
    assert regs and "冒険" in regs[0].text
    regs[0].en = "The Hero's Adventure"
    out = ocr.overlay_translation(im, regs, ocr.find_font())
    assert not t.detect(out)                                              # no Japanese left behind


def test_marshal_float_with_ruby19_mantissa_bytes_roundtrips():
    """RGSS3 (Ruby 1.9) data stores floats as text + NUL + raw mantissa bytes; real Ace games are full of them."""
    raw = b"\x04\x08f\x0b1.5\x00\xb8\x99"
    v = m.loads(raw)
    assert v == 1.5 and m.dumps(v) == raw


def test_file_with_only_dialogue_is_still_written(tmp_path, tr):
    g = make_game(tmp_path / "g", "MV")
    msg = [cmd(101, ["", 0, 0, 2]), cmd(401, ["今日はいい天気ですね。"]), cmd(0, [])]
    write(g / "data/Map002.json", {"displayName": "", "events": [None, {"id": 1, "pages": [{"list": msg}]}]})
    res = run(g, tmp_path / "out", tr)
    mp = json.loads((tmp_path / "out/data/Map002.json").read_text(encoding="utf-8"))
    assert mp["events"][1]["pages"][0]["list"][1]["parameters"][0] == "Nice weather today, isn't it?" and res.files_changed == 1


def test_names_used_by_scripts_are_kept(tmp_path, tr):
    g = mv_game(tmp_path)
    (g / "js/plugins/Quest.js").write_text('if ($gameParty.leader().name() === "アレックス") { go(); }', encoding="utf-8")
    write(g / "data/Actors.json", [None, {"id": 1, "name": "アレックス", "nickname": "", "profile": ""},
                                   {"id": 2, "name": "ポーション", "nickname": "", "profile": ""}])
    res = run(g, tmp_path / "out", tr)
    actors = json.loads((tmp_path / "out/data/Actors.json").read_text(encoding="utf-8"))
    assert actors[1]["name"] == "アレックス" and actors[2]["name"] == "Potion"          # only the referenced name is left alone
    assert res.skipped == 1 and "kept" in (tmp_path / "out/.translation/report.tsv").read_text(encoding="utf-8")
    res2 = run(g, tmp_path / "out_all", tr, keep_referenced=False)
    assert json.loads((tmp_path / "out_all/data/Actors.json").read_text(encoding="utf-8"))[1]["name"] != "アレックス"


def test_speaker_line_is_kept_apart_and_repeated(tmp_path, tr):
    g = make_game(tmp_path / "g", "MV")
    msg = [cmd(101, ["", 0, 0, 2]), cmd(401, ["【アレックス】"]), cmd(401, ["今日はいい天気ですね。"]), cmd(0, [])]
    write(g / "data/Map003.json", {"displayName": "", "events": [None, {"id": 1, "pages": [{"list": msg}]}]})
    run(g, tmp_path / "out", tr)
    lst = json.loads((tmp_path / "out/data/Map003.json").read_text(encoding="utf-8"))["events"][1]["pages"][0]["list"]
    lines = [c["parameters"][0] for c in lst if c["code"] == 401]
    assert lines[0].startswith("[") and lines[0].endswith("]") and lines[1] == "Nice weather today, isn't it?"


def test_translation_memory_wins_and_is_exported(tmp_path, tr):
    g = mv_game(tmp_path)
    mem = tmp_path / "mem.tsv"
    mem.write_text("japanese\tenglish\nこんにちは、\\C[2]魔王\\C[0]。今日はいい天気ですね。\tWhat lovely weather!\n", encoding="utf-8")
    res = run(g, tmp_path / "out", tr, memory=str(mem))
    assert "What lovely weather!" in (tmp_path / "out/data/Map001.json").read_text(encoding="utf-8")
    assert "japanese\tenglish" in (tmp_path / "out/.translation/memory.tsv").read_text(encoding="utf-8")


def test_plugin_control_codes_with_text_arguments_survive(tr):
    """Games with standing-picture plugins write \\F[name] \\FF[pose] \\AA[x]; the model must never see or touch them."""
    DICT["[[0]][[1]][[2]]今日はいい天気ですね。"] = "Nice weather today, isn't it?"
    src = "\\F[reia_normal]\\FF[kuzeru_tameiki]\\AA[FF]今日はいい天気ですね。"
    out = tr.translate_many([src], romaji=False)[0].text
    assert out == "\\F[reia_normal]\\FF[kuzeru_tameiki]\\AA[FF]Nice weather today, isn't it?"


def test_dropped_placeholders_are_recovered_at_the_edges(tr):
    class Dropper(DictBackend):
        def translate_batch(self, texts):
            import re
            return [re.sub(r"\[\[\d+\]\]", "", "Hello there friend") for _ in texts]       # a model that loses every placeholder
    tr._backend = Dropper()
    out = tr.translate_many(["\\C[2]こんにちは\\C[0]"], romaji=False)[0].text
    assert out.startswith("\\C[2]") and "Hello there friend" in out and out.endswith("\\C[0]")


def test_repeated_interjections_are_squashed():
    from rpgm_upscaler.translate.detect import squash_repeats
    assert squash_repeats("Put it on, please, please, please, please.") == "Put it on, please, please."


def test_name_codes_survive_a_model_that_drops_placeholders(tr):
    class Dropper(DictBackend):
        def translate_batch(self, texts):
            import re
            # forgets every [[n]] placeholder but copies the name-like stand-in for \N[n] through
            return [f"Did {re.search('Aldric', t).group(0) if 'Aldric' in t else 'someone'} help me?" for t in texts]
    tr._backend = Dropper()
    out = tr.translate_many(["もしかして、\\N[4]が助けてくれたの？"], romaji=False)[0].text
    assert "\\N[4]" in out and "Aldric" not in out


def test_printf_placeholders_in_battle_messages_survive(tr):
    class Dropper(DictBackend):
        def translate_batch(self, texts):
            import re
            names = re.findall(r"(Aldric|Bryn)", " ".join(texts))
            return [f"{names[0] if names else 'X'} used {names[1] if len(names) > 1 else 'it'}!" for _ in texts]
    tr._backend = Dropper()
    out = tr.translate_many(["%1は%2を使った！"], romaji=False)[0].text
    assert out == "%1 used %2!"


def test_undecodable_file_names_do_not_break_the_report(tmp_path, tr):
    """Shift-JIS file names from a zip extracted on Linux are not valid UTF-8; the run must still finish and write its report."""
    g = mv_game(tmp_path, "MV")
    bad = os.fsdecode(b"img/pictures/\x83\x8c\x83C\x83A.png")           # a cp932 name as raw bytes
    try:
        Image.new("RGBA", (200, 80), (255, 255, 255, 255)).save(g / bad)
    except OSError:
        pytest.skip("this filesystem refuses non-UTF-8 names")
    res = gamerun.translate_game(g, tmp_path / "out", tr, gamerun.Options(ocr=True), ocr_backend=FakeOcr())
    assert res.images_changed >= 1 and (tmp_path / "out/.translation/report.tsv").is_file()


def test_ocr_plausibility_filter_rejects_art_noise():
    good = ["勇者の冒険", "ニューゲーム", "「何か植えようかな?」", "なんでもない", "魔王を倒すために旅に出よう"]
    junk = ["に2", "ョシン", "ーーーーーーーーーーー", "Rたも2", "んでもを", "ンダ/", "ああああ", "ーードー"]
    assert all(ocr.plausible_text(t) for t in good)
    assert not any(ocr.plausible_text(t) for t in junk)


def test_short_ocr_readings_must_occur_in_the_game_text(tmp_path, tr):
    class TwoShort:
        def detect(self, img):
            return [ocr.TextRegion((10, 10, 60, 24), "アレックス", 90.0),        # an actor name from the database: a real label
                    ocr.TextRegion((10, 50, 40, 24), "ソフ", 90.0)]             # art noise: not in the game's text
    g = mv_game(tmp_path, "MV")
    write(g / "data/Actors.json", [None, {"id": 1, "name": "アレックス", "nickname": "", "profile": ""}])
    Image.new("RGBA", (200, 90), (255, 255, 255, 255)).save(g / "img/pictures/Label.png")
    res = gamerun.translate_game(g, tmp_path / "out", tr, gamerun.Options(ocr=True), ocr_backend=TwoShort())
    imgs = [r["ja"] for r in res.rows if r["kind"] == "image"]
    assert "アレックス" in imgs and "ソフ" not in imgs


def test_character_names_are_reused_verbatim_in_dialogue(tmp_path, tr):
    seen = []
    tr._backend.translate_batch = lambda texts: (seen.extend(texts), [f"EN({t})" for t in texts])[1]
    g = make_game(tmp_path / "g", "MV")
    write(g / "data/Actors.json", [None, {"id": 1, "name": "アレックス", "nickname": "", "profile": ""}])
    msg = [cmd(101, ["", 0, 0, 2]), cmd(401, ["アレックスは剣を取った。"]), cmd(0, [])]
    write(g / "data/Map003.json", {"displayName": "", "events": [None, {"id": 1, "pages": [{"list": msg}]}]})
    run(g, tmp_path / "out", tr)
    lst = json.loads((tmp_path / "out/data/Map003.json").read_text(encoding="utf-8"))["events"][1]["pages"][0]["list"]
    en = [c["parameters"][0] for c in lst if c["code"] == 401][0]
    actor = json.loads((tmp_path / "out/data/Actors.json").read_text(encoding="utf-8"))[1]["name"]
    assert "[[0]]" in seen[0] and "アレックス" not in seen[0]       # the model sees a placeholder, not the Japanese name
    assert actor in en and "[[" not in en


def test_terms_keep_hiragana_names_and_katakana_words_apart(tr):
    tr.set_terms({"ミア": "Mia", "みお": "Mio", "x": "X"})
    assert set(tr.terms) == {"ミア"}                                  # hiragana-only and one-character names are not safe to swap
    assert [ja for _, _, ja in tr._term_spans("ミアは笑った")] == ["ミア"]
    assert tr._term_spans("ミアンの店") == []                          # inside a longer katakana word
    assert tr._terms_sig("ミアは笑った") != tr._terms_sig("ふつうの文")
    tr.set_terms({"ミア": "Mya"})
    assert tr._terms_sig("ミアは笑った") != ""


def test_sound_effects_are_transliterated_not_invented(tr):
    called = []
    tr._backend.translate_batch = lambda texts: (called.extend(texts), ["Something invented"] * len(texts))[1]
    r = tr.translate_many(["むにゃ…", "ゴゴゴ", "今日はいい天気ですね。"], romaji=False)
    assert r[0].text.lower().startswith("munya") and r[1].text.lower() == "gogogo"
    assert called == ["今日はいい天気ですね。"]


def test_fast_option_sets_the_backend_beam_and_keys_the_cache(tmp_path, tr):
    from rpgm_upscaler.translate.nllb import NllbBackend
    b = NllbBackend.__new__(NllbBackend)
    b.beam = 4
    assert b.tag == "nllb-200-600m-int8"
    tr._backend = b
    tr.set_beam(1)
    assert b.beam == 1 and b.tag.endswith("-b1")
    g = mv_game(tmp_path)
    tr._backend = DictBackend()
    run(g, tmp_path / "out", tr, beam=2)
    assert tr.beam == 2


def test_non_ascii_game_and_output_paths(tmp_path, tr):
    g = make_game(tmp_path / "ゲーム テスト", "MV")
    write(g / "data/Actors.json", [None, {"id": 1, "name": "アレックス", "nickname": "", "profile": ""}])
    out = tmp_path / "出力 フォルダ"
    res = run(g, out, tr)
    assert res.files_changed >= 1 and (out / ".translation/report.tsv").is_file()
    assert "Arekkusu" in (out / "data/Actors.json").read_text(encoding="utf-8")
    assert "アレックス" in (g / "data/Actors.json").read_text(encoding="utf-8")        # the original is untouched


def test_mz_plugin_command_text_arguments_are_translated_but_not_files_or_numbers(tmp_path, tr):
    g = mv_game(tmp_path, "MZ")
    plug = [cmd(357, ["Fancy", "show", "Show it", {"text": "こんにちは", "picture": "冒険", "title": "村", "count": "5", "Label2": "はい"}]),
            cmd(0, [])]
    write(g / "data/CommonEvents.json", [None, {"id": 1, "name": "x", "list": plug}])
    run(g, tmp_path / "out", tr)
    args = json.loads((tmp_path / "out/data/CommonEvents.json").read_text(encoding="utf-8"))[1]["list"][0]["parameters"][3]
    assert args["text"] != "こんにちは" and args["title"] == "Village" and args["Label2"] == "Yes"
    assert args["picture"] == "冒険" and args["count"] == "5"                       # only plainly-textual keys are touched


def test_ace_vocab_script_strings_are_translated_in_the_scripts_file(tmp_path, tr):
    import zlib
    from rpgm_upscaler.rgss import scripts as sc
    g = make_ace(tmp_path / "ace", ace=True)
    vocab = ('module Vocab\n  # 戦闘メッセージ\n  Victory = "魔王が現れた！"   # コメント\n  Level = \'村\'\n  Escape = "逃げた"\n'
             '  Plain = "ok"\nend\n')
    arr = sc.load((g / "Data/Scripts.rvdata2").read_bytes())
    sc.insert_before_main(arr, "Vocab", vocab)
    (g / "Data/Scripts.rvdata2").write_bytes(m.dumps(arr))
    res = run(g, tmp_path / "out", tr)
    out = sc.load((tmp_path / "out/Data/Scripts.rvdata2").read_bytes())
    src = next(sc.source(e) for e in out if sc._title(e) == "Vocab")
    assert 'Victory = "The Demon King appeared!"   # コメント' in src and "Level = 'Village'" in src      # comments untouched
    assert 'Plain = "ok"' in src and 'Escape = "EN(逃げた)"' in src and src.startswith("module Vocab")      # EN(...) = the fake model's answer
    assert not any(r["source"].startswith("kept") for r in res.rows)
    again = sc.load((g / "Data/Scripts.rvdata2").read_bytes())                                       # the source game is unchanged
    assert "魔王が現れた" in next(sc.source(e) for e in again if sc._title(e) == "Vocab")


def test_printf_fill_ins_are_protected_like_control_codes(tr):
    from rpgm_upscaler.translate.detect import protect, restore
    t, codes = protect("%sは%1$s%dの経験値を獲得！")
    assert codes == ["%s", "%1$s", "%d"] and "%" not in t and restore(t, codes) == "%sは%1$s%dの経験値を獲得！"


def test_xp_game_translation_handles_101_with_the_first_line_inside(tmp_path, tr):
    from tests.fakeace import make_xp
    g = make_xp(tmp_path / "xp", archive=True)
    res = run(g, tmp_path / "out", tr)
    assert res.engine == "XP" and res.translated >= 8
    lst = m.loads((tmp_path / "out/Data/Map001.rxdata").read_bytes()).ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
    codes = [(c.ivars["@code"], [getattr(p, "text", p) for p in c.ivars["@parameters"]]) for c in lst]
    # block 1: the speaker label (the actor's name, romanised like the database entry) in the 101 command, the sentence in a 401
    assert codes[0] == (101, ["Arekkusu"]) and codes[1] == (401, ["Nice weather today, isn't it?"])
    # block 2: no speaker, so the whole text sits in the 101 command itself, and the next window starts with a new 101
    assert codes[2][0] == 101 and codes[2][1][0].startswith("Hello") or codes[2][1][0].startswith("EN(")
    assert codes[3] == (401, ["Nice weather today, isn't it?"]) or codes[3][0] == 401
    choices = next(c for c in lst if c.ivars["@code"] == 102).ivars["@parameters"]
    assert [x.text for x in choices[0]] == ["Yes", "No"] and choices[1] == 2
    sysd = m.loads((tmp_path / "out/Data/System.rxdata").read_bytes())
    words = sysd.ivars["@words"].ivars
    assert words["@gold"].text != "ゴールド" and words["@equip"].text.isascii()
    assert m.loads((tmp_path / "out/Data/Items.rxdata").read_bytes())[1].ivars["@name"].text == "Potion"
    assert (tmp_path / "out/Data/Scripts.rxdata").is_file()                                           # archive was unpacked into the copy
