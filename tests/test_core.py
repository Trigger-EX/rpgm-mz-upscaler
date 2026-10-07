import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from rpgm_upscaler.cli import main as cli_main
from rpgm_upscaler.core import categories, crypto, engines, imageops, patcher, scaling
from rpgm_upscaler.core.planner import build_plan
from rpgm_upscaler.core.project import ProjectError, load_project
from rpgm_upscaler.core.runner import RunError, Runner, validate_output
from rpgm_upscaler.core.settings import Options
from tests.fakegame import KEY, grid_image, make_game


def tree_hash(root: Path) -> str:
    h = hashlib.sha1()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


# ---- crypto ------------------------------------------------------------------------------------
def test_crypto_roundtrip_and_key_recovery():
    png = Path(__file__).parent / "x.png"
    Image.new("RGBA", (4, 4), (1, 2, 3, 4)).save(png)
    data = png.read_bytes(); png.unlink()
    enc = crypto.encrypt(data, KEY)
    assert enc[:16] == crypto.HEADER and crypto.decrypt(enc, KEY) == data
    assert crypto.recover_key(enc) == KEY
    assert not crypto.looks_like_png(crypto.decrypt(enc, bytes(16)))
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(data, KEY)


# ---- scaling -----------------------------------------------------------------------------------
def test_scaling():
    assert scaling.parse_scale("fit", (816, 624), (1920, 1080)) == 1.625
    assert scaling.parse_scale("fit", (1280, 720), (1920, 1080)) == 1.5
    assert scaling.parse_scale("2.3", (816, 624), (1920, 1080)) == 2.25
    sp = scaling.ScalePlan(1.625, (816, 624), (816, 624), (1920, 1080))
    assert sp.tile == 78 and sp.ui_area == (1326, 1014) and sp.offset == (297, 33)
    assert scaling.cover_size(816, 624, 1.625, (1920, 1080)) == (1920, 1469)
    assert scaling.ScalePlan(2, (816, 624), (816, 624), (1920, 1080)).check()


def test_categories():
    kinds = categories.tileset_kinds([["World_A1", "", "", "", "Outside_A5", "Outside_B", "", "", ""]])
    assert categories.cell_size("tilesets", "World_A1", 768, 576, kinds) == (24, 24)
    assert categories.cell_size("tilesets", "Outside_B", 768, 768, kinds) == (48, 48)
    assert categories.cell_size("characters", "$Big", 144, 192, kinds) == (48, 48)
    assert categories.cell_size("faces", "Actor1", 576, 288, kinds) == (144, 144)
    assert categories.cell_size("pictures", "P", 100, 100, kinds) is None


# ---- imageops / engines ------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["lanczos", "nearest", "sharp"])
def test_cells_keep_colour_and_size(mode):
    img = grid_image(576, 288, 144, 144)
    out = imageops.upscale_image(img, engines.PillowEngine(mode), None, 1.625, (144, 144))
    assert out.size == (4 * 234, 2 * 234)
    for r in range(2):
        for c in range(4):
            assert out.getpixel((c * 234 + 117, r * 234 + 117)) == img.getpixel((c * 144 + 72, r * 144 + 72))
            # corners must not bleed from the neighbour either
            assert out.getpixel((c * 234, r * 234)) == img.getpixel((c * 144, r * 144))


def test_alpha_bleed_keeps_alpha():
    img = grid_image(96, 96, 48, 48)
    img.paste((0, 0, 0, 0), (0, 0, 48, 96))
    b = imageops.bleed_rgb(img)
    assert b.getchannel("A").tobytes() == img.getchannel("A").tobytes()
    assert b.getpixel((44, 10))[:3] != (0, 0, 0)


def _stub_ncnn(tmp_path: Path) -> Path:
    exe = tmp_path / "realesrgan-ncnn-vulkan"
    exe.write_text("#!/usr/bin/env python3\nimport sys\nfrom PIL import Image\n"
                   "a=sys.argv; i=a[a.index('-i')+1]; o=a[a.index('-o')+1]; s=int(a[a.index('-s')+1])\n"
                   "im=Image.open(i); im.resize((im.width*s, im.height*s), Image.BICUBIC).save(o)\n")
    exe.chmod(0o755)
    return exe


def test_ncnn_stub(tmp_path):
    exe = _stub_ncnn(tmp_path)
    eng = engines.make_engine("realesrgan", str(exe))
    eng.probe()
    img = grid_image(96, 96, 48, 48)
    img.paste((0, 0, 0, 0), (0, 0, 48, 96))
    out = imageops.upscale_image(img, eng, None, 1.5, (48, 48))
    assert out.size == (144, 144)
    assert out.getpixel((100, 20))[3] == 255 and out.getpixel((10, 10))[3] == 0
    assert out.getpixel((100, 20))[:3] == img.getpixel((50, 10))[:3]
    assert engines.detect_engines({"realesrgan": str(exe)})["realesrgan"][0]
    assert not engines.detect_engines()["waifu2x"][0]


# ---- project -----------------------------------------------------------------------------------
@pytest.mark.parametrize("engine,www", [("MZ", False), ("MV", False), ("MZ", True)])
def test_project_detection(tmp_path, engine, www):
    g = make_game(tmp_path / "g", engine, (1280, 720), www=www)
    p = load_project(g)
    assert p.engine == engine and p.screen == (1280, 720) and p.web == ("www" if www else "")
    assert p.plugins == ["Foo", "Bar"]


def test_project_rejects_bad_dir(tmp_path):
    with pytest.raises(ProjectError):
        load_project(tmp_path)


def test_encrypted_key_recovery(tmp_path):
    g = make_game(tmp_path / "g", "MZ", encrypted=True)
    sysf = g / "data/System.json"
    d = json.loads(sysf.read_text()); d.pop("encryptionKey"); sysf.write_text(json.dumps(d))
    assert load_project(g).key == KEY


# ---- patcher -----------------------------------------------------------------------------------
def test_plugins_js_roundtrip():
    text = '// c\nvar $plugins =\n[\n{"name":"A","status":true,"parameters":{}},\n];\n'
    prefix, entries = patcher.parse_plugins_js(text)
    assert entries[0]["name"] == "A"
    again = patcher.format_plugins_js(prefix, entries)
    assert patcher.parse_plugins_js(again)[1] == entries and again.startswith("// c\nvar $plugins =\n")


# ---- planner / runner --------------------------------------------------------------------------
def _run(game, out, **kw):
    opts = Options(workers=kw.pop("workers", 1), movies=False, **kw)
    plan = build_plan(load_project(game), opts)
    return plan, Runner(plan, out).run()


@pytest.mark.parametrize("engine,encrypted", [("MZ", False), ("MV", False), ("MZ", True), ("MV", True)])
def test_full_run(tmp_path, engine, encrypted):
    g = make_game(tmp_path / "g", engine, encrypted=encrypted)
    before = tree_hash(g)
    plan, res = _run(g, tmp_path / "out")
    assert res.success, res.failed
    assert tree_hash(g) == before  # source untouched
    out = tmp_path / "out"
    ext = {"MZ": ".png_", "MV": ".rpgmvp"}[engine] if encrypted else ".png"
    key = KEY if encrypted else None
    ic = imageops.load_image(out / f"img/system/IconSet{ext}", key)
    assert ic.size == (16 * 52, 2 * 52)  # 32*1.625 = 52
    chars = imageops.load_image(out / f"img/characters/Actor1{ext}", key)
    assert chars.size == (12 * 78, 8 * 78)
    a1 = imageops.load_image(out / f"img/tilesets/World_A1{ext}", key)
    assert a1.size == (32 * 39, 24 * 39)  # half tiles 24 -> 39
    title = imageops.load_image(out / f"img/titles1/Title{ext}", key)
    assert title.width >= 1920 and title.height >= 1080
    # Window.png untouched, audio copied
    assert (out / f"img/system/Window{ext}").read_bytes() == (g / f"img/system/Window{ext}").read_bytes()
    assert (out / "audio/bgm/Theme.ogg").read_bytes() == b"OggS-fake"
    if encrypted:
        assert (out / f"img/pictures/Palette{ext}").is_file()
    # patches
    pkg = json.loads((out / "package.json").read_text())
    assert pkg["window"]["width"] == 1920 and pkg["window"]["height"] == 1080
    prefix, entries = patcher.parse_plugins_js((out / "js/plugins.js").read_text())
    assert [e["name"] for e in entries] == ["Foo", "Bar", "UpscalerPatch"]
    assert (out / "js/plugins/UpscalerPatch.js").is_file()
    if engine == "MZ":
        s = json.loads((out / "data/System.json").read_text())
        assert s["advanced"]["screenWidth"] == 1920 and s["tileSize"] == 78
        assert s["advanced"]["uiAreaWidth"] == 1326 and s["advanced"]["fontSize"] == 42


def test_patch_idempotent_and_plugin_syntax(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    plan, res = _run(g, tmp_path / "out")
    plan2 = build_plan(load_project(g), Options(workers=1, movies=False))
    patcher.apply_patches(plan2, tmp_path / "out")
    _, entries = patcher.parse_plugins_js((tmp_path / "out/js/plugins.js").read_text())
    assert [e["name"] for e in entries].count("UpscalerPatch") == 1
    node = shutil.which("node") or "/opt/node22/bin/node"
    if Path(node).exists() or shutil.which("node"):
        for eng in ("MZ", "MV"):
            f = tmp_path / f"p_{eng}.js"
            f.write_text(patcher.render_plugin(plan2, "lanczos"))
            r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr


def test_patch_survives_mz19_getter_metrics(tmp_path):
    """MZ 1.9+ computes ImageManager.iconWidth etc. from $dataSystem, which does not exist when plugins load."""
    node = shutil.which("node") or "/opt/node22/bin/node"
    if not (shutil.which("node") or Path(node).exists()):
        pytest.skip("needs node")
    g = make_game(tmp_path / "g", "MZ")
    _run(g, tmp_path / "out")
    plan = build_plan(load_project(g), Options(workers=1, movies=False))
    (tmp_path / "plugin.js").write_text(patcher.render_plugin(plan, "lanczos"))
    (tmp_path / "run.js").write_text("""
const vm = require("vm"), fs = require("fs");
const errors = [];
const ctx = { console: { error: (...a) => errors.push(a.join(" ")), warn() {}, info() {}, log() {} } };
ctx.Utils = { RPGMAKER_NAME: "MZ" };
ctx.ImageManager = {};
for (const [name, base] of [["iconWidth", 32], ["iconHeight", 32], ["faceWidth", 144], ["faceHeight", 144]])
    Object.defineProperty(ctx.ImageManager, name, { get() { if (!("iconSize" in ctx.$dataSystem)) return base; return base; }, configurable: true });
ctx.window = ctx;
const stubs = {};                                       // any other engine global becomes an empty class
const scope = new Proxy(ctx, { has: () => true, get: (t, k) => typeof k === "symbol" ? undefined : k in t ? t[k] : k in globalThis ? globalThis[k] : (stubs[k] ||= class {}) });
vm.createContext(ctx);
vm.runInContext("with (scope) {" + fs.readFileSync(process.argv[2], "utf8") + "}", Object.assign(ctx, { scope }));
ctx.$dataSystem = {};                                   // data is loaded after the plugins ran
const out = { errors, icon: ctx.ImageManager.iconWidth, face: ctx.ImageManager.faceHeight };
console.log(JSON.stringify(out));
""")
    r = subprocess.run([node, str(tmp_path / "run.js"), str(tmp_path / "plugin.js")], capture_output=True, text=True)
    res = json.loads(r.stdout)
    assert not res["errors"], res["errors"]
    assert res["icon"] == round(32 * plan.scale.n) and res["face"] == round(144 * plan.scale.n)


def test_plain_images_option(tmp_path):
    g = make_game(tmp_path / "g", "MZ", encrypted=True)
    plan, res = _run(g, tmp_path / "out", reencrypt=False)
    assert res.success, res.failed
    assert (tmp_path / "out/img/faces/Actor1.png").is_file()
    assert not list((tmp_path / "out/img").rglob("*.png_"))
    assert json.loads((tmp_path / "out/data/System.json").read_text())["hasEncryptedImages"] is False


def test_dry_run_writes_nothing_and_warnings(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    plan = build_plan(load_project(g), Options(movies=False, scale="2"))
    assert plan.jobs and any("exceeds the target" in w for w in plan.warnings)
    assert any("hud" in w for w in plan.warnings)
    assert "img/system/Window.png" in plan.skipped_windowskins
    assert not (tmp_path / "out").exists()


def test_output_validation(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    for bad in (g, g / "sub", tmp_path):
        with pytest.raises(RunError):
            validate_output(g, bad)


def test_resume_skips_and_parallel(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    plan, res = _run(g, tmp_path / "out", workers=2)
    assert res.success and res.ok == len(plan.jobs)
    f = tmp_path / "out/img/faces/Actor1.png"
    m = f.stat().st_mtime_ns
    _, res2 = _run(g, tmp_path / "out", workers=2)
    assert res2.skipped == len(plan.jobs) and res2.ok == 0 and f.stat().st_mtime_ns == m
    _, res3 = _run(g, tmp_path / "out", workers=1, scale="2")  # changed options invalidate
    assert res3.ok == len(plan.jobs)


def test_cancel_leaves_no_tmp(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    plan = build_plan(load_project(g), Options(workers=1, movies=False))
    r = Runner(plan, tmp_path / "out")
    r.on_progress = lambda d, t, n: r.cancel() if d > 0 and n.endswith("png") and not r.cancel_event.is_set() else None
    res = r.run()
    assert res.cancelled and not list((tmp_path / "out").rglob("*.tmp"))
    assert json.loads((tmp_path / "out/.upscaler/manifest.json").read_text()) is not None


def test_failing_job_reported(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    (g / "img/pictures/Bad.png").write_bytes(b"not a png")
    plan, res = _run(g, tmp_path / "out")
    assert any("Bad.png" in w for w in plan.warnings) or res.failed  # unreadable -> copied or failed
    assert (tmp_path / "out/img/pictures/Bad.png").is_file()


def test_video(tmp_path):
    from rpgm_upscaler.core import video
    if not video.available():
        pytest.skip("ffmpeg missing")
    g = make_game(tmp_path / "g", "MZ")
    src = g / "movies/Intro.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                        "-pix_fmt", "yuv420p", str(src)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create test video: " + r.stderr.decode()[-200:])
    plan = build_plan(load_project(g), Options(workers=1))
    assert any(j.kind == "video" for j in plan.jobs)
    res = Runner(plan, tmp_path / "out").run()
    assert res.success, res.failed
    w, h = video.probe_size(tmp_path / "out/movies/Intro.mp4")
    assert w <= 1920 and h <= 1080 and w % 2 == 0 and (w == 1440 or h == 1080)


def test_cli(tmp_path, capsys):
    g = make_game(tmp_path / "g", "MZ")
    assert cli_main(["analyze", str(g)]) == 0
    assert "MZ" in capsys.readouterr().out
    assert cli_main(["plan", str(g), "--json", "--no-movies"]) == 0
    assert json.loads(capsys.readouterr().out)["scale"] == 1.625
    assert cli_main(["run", str(g), "-o", str(tmp_path / "o"), "--no-movies", "--workers", "1"]) == 0
    assert cli_main(["run", str(g), "-o", str(g / "o")]) == 2


# ---- tilemap texture limits / idempotent patching ------------------------------------------------
def test_tex_multiplier_and_lib_patch():
    assert patcher.tex_multiplier(1.0) == 1 and patcher.tex_multiplier(1.5) == 2
    assert patcher.tex_multiplier(1.625) == 2 and patcher.tex_multiplier(2.75) == 3
    src = "a = 1024 * (p & 1); RenderTexture.create(2048, 2048); s = 1.0 / 2048; n = 'x1024y'; f = 1.1024;"
    out, n = patcher.patch_tilemap_lib(src, 2)
    assert n == 4 and "2048 * (p & 1)" in out and "create(4096, 4096)" in out and "1.0 / 4096" in out
    assert "'x1024y'" in out and "1.1024" in out          # unrelated lookalikes untouched
    assert patcher.patch_tilemap_lib(src, 2)[0] == out    # derived from the original: deterministic


def test_mv_tilemap_lib_patched_and_resume_does_not_compound(tmp_path):
    g = make_game(tmp_path / "g", "MV")
    out = tmp_path / "out"
    for _ in range(3):                                    # resumed runs must keep the patch stable
        _, res = _run(g, out)
        assert res.success, res.failed
    lib = (out / "js/libs/pixi-tilemap.js").read_text()
    assert "2048 * (points[i + 8] & 1)" in lib and "create(4096, 4096)" in lib and "8192" not in lib
    assert "1024" in (g / "js/libs/pixi-tilemap.js").read_text()   # source untouched


def test_mz_system_json_not_compounded_on_resume(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    out = tmp_path / "out"
    for _ in range(3):
        _run(g, out)
    s = json.loads((out / "data/System.json").read_text())
    assert s["advanced"]["fontSize"] == 42 and s["tileSize"] == 78


def test_large_scale_warns_about_gpu_memory(tmp_path):
    g = make_game(tmp_path / "g", "MZ")
    plan = build_plan(load_project(g), Options(movies=False, scale="3"))
    assert any("map textures" in w for w in plan.warnings)
    assert not any("map textures" in w for w in build_plan(load_project(g), Options(movies=False)).warnings)
