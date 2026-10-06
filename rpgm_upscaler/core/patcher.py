"""Edit the OUTPUT copy so the engine runs at the target resolution with the upscaled assets."""
from __future__ import annotations

import json
import math
import re
from importlib import resources
from pathlib import Path

from . import crypto
from .planner import Plan

PLUGIN_NAME = "UpscalerPatch"


def parse_plugins_js(text: str) -> tuple[str, list[dict]]:
    """Return (prefix before the array, entries). Tolerates comments and trailing commas."""
    start = text.index("[", text.index("$plugins"))
    end = text.rindex("]")
    body = text[start:end + 1]
    body = re.sub(r",(\s*[\]}])", r"\1", body)
    return text[:start], json.loads(body)


def format_plugins_js(prefix: str, entries: list[dict]) -> str:
    lines = ",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries)
    return f"{prefix}[\n{lines}\n];\n"


def tex_multiplier(n: float) -> int:
    """Factor by which the tilemap renderer's 1024px sheet slots / 2048px textures must grow.
    The largest default tileset sheet side is 768px."""
    return max(1, math.ceil(768 * n / 1024 - 1e-9))


_TILEMAP_NUM = re.compile(r"(?<![\w.])(1024|2048)(?![\w.])")


def patch_tilemap_lib(source: str, k: int) -> tuple[str, int]:
    """MV's js/libs/pixi-tilemap.js packs every tileset sheet into a fixed 1024px slot of 2048px textures.
    Grow both by k. Always applied to the ORIGINAL text, so repeated runs never compound."""
    return _TILEMAP_NUM.subn(lambda m: str(int(m.group(1)) * k), source)


def render_plugin(plan: Plan, engine: str) -> str:
    sp = plan.scale
    cfg = {"n": sp.n, "orig": list(sp.orig), "target": list(sp.target), "ui": list(sp.ui_area),
           "offset": list(sp.offset), "tile": sp.tile, "texMult": tex_multiplier(sp.n), "engine": engine}
    tmpl = resources.files("rpgm_upscaler.core").joinpath("templates/UpscalerPatch.js.tmpl").read_text(encoding="utf-8")
    return (tmpl.replace("__CONFIG__", json.dumps(cfg)).replace("__N_TEXT__", f"{sp.n:g}")
            .replace("__TW__", str(sp.target[0])).replace("__TH__", str(sp.target[1])))


def _write_json(path: Path, data, compact: bool = True) -> None:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":") if compact else None, indent=None if compact else 2)
    path.write_text(text, encoding="utf-8")


def apply_patches(plan: Plan, out: Path) -> list[str]:
    """Returns list of patched relative paths."""
    project, sp, opts = plan.project, plan.scale, plan.options
    web = out / project.web if project.web else out
    done: list[str] = []

    # Patched files are always derived from the ORIGINAL (source) file, so resumed runs never compound.
    src_web = project.web_root
    sysfile = web / "data" / "System.json"
    if sysfile.is_file():
        system = json.loads((src_web / "data" / "System.json").read_text(encoding="utf-8-sig"))
        changed = False
        if project.engine == "MZ":
            adv = system.setdefault("advanced", {})
            adv["screenWidth"], adv["screenHeight"] = sp.target
            adv["uiAreaWidth"], adv["uiAreaHeight"] = sp.ui_area
            if "fontSize" in adv:
                adv["fontSize"] = max(1, int(round(adv["fontSize"] * sp.n)))
            system["tileSize"] = sp.tile
            changed = True
        if system.get("hasEncryptedImages") and not opts.reencrypt:
            remaining = any(f.suffix.lower() in crypto.ENCRYPTED_IMAGE_EXTS for f in (web / "img").rglob("*"))
            if not remaining:
                system["hasEncryptedImages"] = False
                changed = True
        if changed:
            _write_json(sysfile, system)
            done.append(sysfile.relative_to(out).as_posix())

    pkg = project.package_json
    if pkg is not None:
        pkg_out = out / pkg.relative_to(project.base)
        if pkg_out.is_file():
            data = json.loads(pkg_out.read_text(encoding="utf-8-sig"))
            win = data.setdefault("window", {})
            win["width"], win["height"] = sp.target
            _write_json(pkg_out, data, compact=False)
            done.append(pkg_out.relative_to(out).as_posix())

    lib = web / "js" / "libs" / "pixi-tilemap.js"
    k = tex_multiplier(sp.n)
    if k > 1 and lib.is_file():
        text, count = patch_tilemap_lib((src_web / "js" / "libs" / "pixi-tilemap.js").read_text(encoding="utf-8"), k)
        if count:
            lib.write_text(text, encoding="utf-8")
            done.append(lib.relative_to(out).as_posix())

    plugin = web / "js" / "plugins" / f"{PLUGIN_NAME}.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(render_plugin(plan, opts.engine), encoding="utf-8")
    done.append(plugin.relative_to(out).as_posix())

    pj = web / "js" / "plugins.js"
    text = pj.read_text(encoding="utf-8")
    prefix, entries = parse_plugins_js(text)
    entries = [e for e in entries if e.get("name") != PLUGIN_NAME]
    entries.append({"name": PLUGIN_NAME, "status": True,
                    "description": "Generated by RPGM Upscaler", "parameters": {}})
    pj.write_text(format_plugins_js(prefix, entries), encoding="utf-8")
    done.append(pj.relative_to(out).as_posix())
    return done
