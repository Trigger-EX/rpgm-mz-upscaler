"""What gets written next to an upscaled VX / VX Ace game.

hires     mkxp-z "Hires" pack: Hires/Graphics/** + mkxp.json (keys checked against mkxp-z's own source).
stock640  inserts a tiny script that sets 640x480 (the most stock RGSS can do).
"""
from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path

from ..detect import ci_child
from . import scripts as sc

HUB_TITLE = "▼ RPGM Hub Resolution"

README = """RPGM Hub: HD pack for {title}
=================================

Scale: x{n:g}  (game screen {w}x{h} -> window about {ww}x{wh})

What this folder contains
  Hires/Graphics/...   the upscaled copies of your game's graphics (the originals are still in Graphics/)
  mkxp.json            settings that tell mkxp-z to use them

How to play it
  1. Download mkxp-z (https://github.com/mkxp-z/mkxp-z/releases) for your system.
  2. Copy mkxp-z's program files into this folder (or point mkxp-z at it).
  3. If the original game used the RTP ({rtp}), put the RTP files somewhere and add the folder to the "RTP" list in
     mkxp.json. RTP graphics are NOT upscaled by this tool: copy the RTP's Graphics folder into this game
     first and run RPGM Hub again to get HD versions of them too.
  4. Start mkxp-z. Press F1 in-game for its options; Alt+Enter toggles fullscreen.

The stock RPG Maker player (Game.exe) cannot draw upscaled graphics: its screen is limited to 640x480 and its map
renderer is fixed at 32 pixel tiles. That is why an alternative player is used.
"""


def strip_json_comments(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True; out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


_RTP_DIRS = {"ACE": ("Enterbrain/RGSS3", "RGSS3"), "VX": ("Enterbrain/RGSS2", "RGSS2")}


def rtp_names(base: Path) -> list[str]:
    """The RTP packages a game's Game.ini asks for (RTP=, RTP1=, RTP2=, RTP3=); empty entries are ignored."""
    ini = ci_child(base, "Game.ini")
    text = ini.read_bytes().decode("cp932", errors="replace") if ini else ""
    out = []
    for key in ("RTP", "RTP1", "RTP2", "RTP3"):
        mt = re.search(rf"^\s*{key}\s*=\s*(.*?)\s*$", text, re.M | re.I)
        if mt and mt.group(1):
            out.append(mt.group(1))
    return out


def find_rtp(name: str, engine: str, extra: list[str] | None = None) -> Path | None:
    """Look for an installed RTP package folder (the one holding Graphics/ and Audio/) in the places Linux users keep it:
    $RPGM_RTP, a Wine prefix (the Windows installer's `Common Files/Enterbrain` folder), ~/RTP, ~/.local/share/rtp, /usr/share."""
    roots: list[Path] = [Path(x) for x in (extra or [])]
    roots += [Path(x) for x in os.environ.get("RPGM_RTP", "").split(os.pathsep) if x]
    home = Path.home()
    prefixes = [Path(os.environ["WINEPREFIX"])] if os.environ.get("WINEPREFIX") else []
    prefixes += [home / ".wine"]
    for pre in prefixes:
        for pf in ("Program Files (x86)", "Program Files"):
            for sub in _RTP_DIRS.get(engine, ()):
                roots.append(pre / "drive_c" / pf / "Common Files" / sub)
    roots += [home / "RTP", home / ".local/share/rtp", Path("/usr/share/rtp"), Path("/usr/share/rpgmaker-rtp")]
    for root in roots:
        cands = [ci_child(root, name)] if root.is_dir() else []
        if root.name.lower() == name.lower():
            cands.append(root)
        for cand in cands:
            if cand is not None and cand.is_dir() and ci_child(cand, "Graphics") is not None:
                return cand
    return None


def apply_hires(plan, out: Path) -> list[str]:
    n = plan.scale.n
    cfg_path = out / "mkxp.json"
    cfg: dict = {}
    src_cfg = plan.project.base / "mkxp.json"
    if src_cfg.is_file():
        try:
            cfg = json.loads(strip_json_comments(src_cfg.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            cfg = {}
    cfg.update({
        "rgssVersion": 3 if plan.project.engine == "ACE" else 2,
        "enableHires": True,
        "textureScalingFactor": n, "framebufferScalingFactor": n, "atlasScalingFactor": n,
        "fixedAspectRatio": True, "winResizable": True, "smoothScaling": 1, "vsync": True,
    })
    cfg.setdefault("RTP", [])
    found = []
    for name in rtp_names(plan.project.base):                       # point mkxp-z at an RTP that is already installed
        path = find_rtp(name, plan.project.engine)
        if path is not None and str(path) not in cfg["RTP"]:
            cfg["RTP"].append(str(path))
            found.append(f"{name} -> {path}")
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    w, h = plan.project.screen
    (out / "README-HUB.txt").write_text(README.format(title=plan.project.title or "your game", n=n, w=w, h=h, ww=round(w * n), wh=round(h * n),
                                                      rtp="RPGVXAce" if plan.project.engine == "ACE" else "RPGVX"), encoding="utf-8")
    plan.warnings.extend(f"RTP found and added to mkxp.json: {f}" for f in found)
    return ["mkxp.json", "README-HUB.txt"]


def render_script(width: int = 640, height: int = 480) -> str:
    tmpl = resources.files("rpgm_upscaler.rgss").joinpath("templates/HubResolution.rb").read_text(encoding="utf-8")
    return tmpl.replace("__WIDTH__", str(width)).replace("__HEIGHT__", str(height))


def apply_stock640(plan, out: Path) -> list[str]:
    from ..core.project import ProjectError  # noqa: F401 (keeps import errors obvious)
    rel = Path(plan.project.scripts_path.replace("\\", "/"))
    src = plan.project.base.joinpath(*rel.parts)
    if not src.is_file():
        found = ci_child(plan.project.base / (rel.parent.name or "Data"), rel.name)
        if found is None:
            raise FileNotFoundError(f"scripts file {rel} not found")
        src = found
    arr = sc.load(src.read_bytes())                       # always from the original, so re-runs never stack
    sc.insert_before_main(arr, HUB_TITLE, render_script())
    from . import marshal as m
    dst = out / src.relative_to(plan.project.base)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(m.dumps(arr))
    return [dst.relative_to(out).as_posix()]


def make_hook(mode: str):
    return apply_hires if mode == "hires" else apply_stock640
