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
from . import fonts as fontmod
from . import mkxp as mkxpmod
from . import scripts as sc

HUB_TITLE = "▼ RPGM Hub Resolution"

PLAY_BUNDLED = "  Run 'Game' in this folder (double-click it, or ./Game in a terminal). The mkxp-z player is already included."
PLAY_MANUAL = """  1. Get the mkxp-z player: RPGM Hub's Upscale tab has an 'Install mkxp-z' button, or pick a Linux build at
     https://nightly.link/mkxp-z/mkxp-z/workflows/autobuild/dev (mkxp-z has no releases), or build it from
     https://github.com/mkxp-z/mkxp-z
  2. Copy the player, its scripts/ and stdlib/ folders into this folder and rename the program to 'Game' (chmod +x Game).
  3. Run Game."""

README = """RPGM Hub: HD pack for {title}
=================================

Scale: x{n:g}  (game screen {w}x{h} -> window about {ww}x{wh})

What this folder contains
  Hires/Graphics/...   the upscaled copies of your game's graphics (the originals are still in Graphics/)
  mkxp.json            settings that tell mkxp-z to use them

How to play it
{play}
  If the original game used the RTP ({rtp}), put the RTP files somewhere and add the folder to the "RTP" list in
     mkxp.json. RTP graphics are NOT upscaled by this tool: copy the RTP's Graphics folder into this game
     first and run RPGM Hub again to get HD versions of them too.
  Press F1 in-game for mkxp-z's options; Alt+Enter toggles fullscreen.

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


_RTP_DIRS = {"ACE": ("Enterbrain/RGSS3", "RGSS3"), "VX": ("Enterbrain/RGSS2", "RGSS2"), "XP": ("Enterbrain/RGSS", "RGSS")}


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


def wine_prefixes() -> list[Path]:
    """Wine prefixes where a Windows RTP installer may have put the RTP: $WINEPREFIX, ~/.wine, Lutris (~/Games/*), Steam Proton,
    Bottles, PlayOnLinux, and the Flatpak Lutris. A prefix is a folder with drive_c; one level of `prefix`/`pfx` is looked into."""
    home = Path.home()
    roots = [Path(os.environ["WINEPREFIX"])] if os.environ.get("WINEPREFIX") else []
    roots.append(home / ".wine")
    parents = [home / "Games", home / ".local/share/Steam/steamapps/compatdata", home / ".steam/steam/steamapps/compatdata",
               home / ".local/share/bottles/bottles", home / ".PlayOnLinux/wineprefix",
               home / ".var/app/net.lutris.Lutris/data/lutris/prefixes", home / ".local/share/lutris/prefixes",
               home / ".var/app/com.usebottles.bottles/data/bottles/bottles", home / "Games/Heroic/Prefixes/default"]
    for par in parents:
        try:
            roots += sorted(p for p in par.iterdir() if p.is_dir())
        except OSError:
            pass
    out, seen = [], set()
    for r in roots:
        for cand in (r, r / "prefix", r / "pfx", r / "wine_prefix"):
            if (cand / "drive_c").is_dir() and cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def find_rtp(name: str, engine: str, extra: list[str] | None = None) -> Path | None:
    """Look for an installed RTP package folder (the one holding Graphics/ and Audio/) in the places Linux users keep it:
    $RPGM_RTP, a Wine prefix (the Windows installer's `Common Files/Enterbrain` folder), ~/RTP, ~/.local/share/rtp, /usr/share."""
    roots: list[Path] = [Path(x) for x in (extra or [])]
    roots += [Path(x) for x in os.environ.get("RPGM_RTP", "").split(os.pathsep) if x]
    home = Path.home()
    for pre in wine_prefixes():
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
        "rgssVersion": {"ACE": 3, "VX": 2, "XP": 1}[plan.project.engine],
        "enableHires": True,
        "textureScalingFactor": n, "framebufferScalingFactor": n, "atlasScalingFactor": n,
        "fixedAspectRatio": True, "winResizable": True, "smoothScaling": 1, "vsync": True,
    })
    # mkxp-z builds the hires tile atlas with glBlitFramebuffer when smoothScaling and smoothScalingDown are both <= 1 (bilinear). That
    # native path binds the LOW-res source framebuffer but scales the source rectangle to hires coordinates, so the atlas is filled with
    # 1x tileset pixels at the wrong places: black rows, wrong tiles, tileset fragments on screen. Any value >= 2 on either key makes
    # mkxp-z draw the blit through its shader path, which reads the hires texture. smoothScalingDown (bicubic) only changes how the
    # 2.5x framebuffer is shrunk into a smaller window, so that is the key we raise; an existing higher value is kept.
    old = cfg.get("smoothScalingDown")
    cfg["smoothScalingDown"] = max(2, old) if isinstance(old, int) and not isinstance(old, bool) else 2
    sources = fontmod.script_sources(plan.project.base, plan.project.scripts_path)
    preload = [x for x in cfg.get("preloadScript", []) if isinstance(x, str)]
    for lib in ("ruby_classic_wrap", "mkxp_wrap", "win32_wrap"):       # Win32API and Ruby 1.8 stand-ins that mkxp-z ships but leaves off
        if f"scripts/preload/{lib}.rb" not in preload:
            preload.append(f"scripts/preload/{lib}.rb")
    if any("TRGSSX" in src for src in sources):
        (out / "scripts" / "preload").mkdir(parents=True, exist_ok=True)
        (out / "scripts" / "preload" / "hub_trgssx.rb").write_text(
            resources.files("rpgm_upscaler.rgss").joinpath("templates/hub_trgssx.rb").read_text(encoding="utf-8"), encoding="utf-8")
        if "scripts/preload/hub_trgssx.rb" not in preload:
            preload.append("scripts/preload/hub_trgssx.rb")
        plan.warnings.append("this game uses TRGSSX.dll (a Windows-only RGSS extension). A stand-in answers its version check so the game starts, "
                             "but anything the DLL draws (rotated/blended blits, polygons, anti-aliased text) will be missing.")
    cfg["preloadScript"] = preload
    cfg.setdefault("RTP", [])
    found, missing = [], []
    mine = getattr(plan.options, "rtp_path", "")
    if mine and (ci_child(Path(mine), "Graphics") is not None) and mine not in cfg["RTP"]:    # the folder itself is an RTP package
        cfg["RTP"].append(mine)
        found.append(f"{mine} (chosen by you)")
    for name in rtp_names(plan.project.base):                       # point mkxp-z at an RTP that is already installed
        path = find_rtp(name, plan.project.engine, [mine] if mine else None)
        if path is not None and str(path) not in cfg["RTP"]:
            cfg["RTP"].append(str(path))
            found.append(f"{name} -> {path}")
        elif path is None and not cfg["RTP"]:
            missing.append(name)
    if missing:
        plan.warnings.append(
            f"this game uses the {', '.join(missing)} RTP (shared RPG Maker assets such as Graphics/Characters/Vehicle), which is not in the game folder "
            "and was not found on this computer. mkxp-z will stop with 'file ... not found' on the first RTP asset. Point the hub at it: "
            "'RTP folder' in the mkxp-z box (CLI: --rtp DIR), or put it in ~/RTP/" + missing[0] + ". It is the folder holding Graphics/ and Audio/ "
            "(the Windows RTP installer puts it in Program Files/Common Files/Enterbrain/RGSS2/RPGVX inside a Wine/Lutris prefix).")
    bundled: list[str] = []
    if getattr(plan.options, "bundle_player", True):
        src = mkxpmod.locate(getattr(plan.options, "mkxp_path", ""))
        if src is None:
            plan.warnings.append("the mkxp-z player is not installed, so this export has no 'Game' to start. " + mkxpmod.HELP)
        else:
            try:
                bundled = mkxpmod.copy_into(out, src)
            except (mkxpmod.MkxpError, OSError) as e:
                plan.warnings.append(f"the mkxp-z player could not be added: {e}")
    try:
        plan.warnings.extend(fontmod.provide_fonts(plan.project.base, out, cfg, sources))
    except OSError as e:
        plan.warnings.append(f"font setup failed: {e}")
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    w, h = plan.project.screen
    (out / "README-HUB.txt").write_text(README.format(title=plan.project.title or "your game", n=n, w=w, h=h, ww=round(w * n), wh=round(h * n),
                                                      rtp={"ACE": "RPGVXAce", "VX": "RPGVX", "XP": "Standard"}[plan.project.engine],
                                                      play=PLAY_BUNDLED if "Game" in bundled else PLAY_MANUAL), encoding="utf-8")
    plan.warnings.extend(f"RTP found and added to mkxp.json: {f}" for f in found)
    return ["mkxp.json", "README-HUB.txt"] + bundled + (["Fonts"] if (out / "Fonts").is_dir() else [])


def restore_missing_files(out: Path, source: Path) -> int:
    """Copy files the original game has but the export lacks (an Audio/ or Movies/ folder an older version dropped). Nothing in the
    export is replaced, and the images the export already holds (upscaled, under their own names) are never touched."""
    from .pipeline import _merge_loose_dir
    from ..detect import detect_engine
    info = detect_engine(source)
    if info is None:
        raise ValueError("the original folder is not a game folder")
    before = sum(1 for p in out.rglob("*") if p.is_file())
    for d in info.root.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and d.name.lower() not in ("hires", "graphics", "data"):
            _merge_loose_dir(d, out / d.name)
    return sum(1 for p in out.rglob("*") if p.is_file()) - before


def refresh_export(out: Path, mkxp_path: str = "", source: str | Path | None = None, rtp_path: str = "") -> list[str]:
    """Re-run the cheap steps of a hires export on a folder that is already upscaled (no images are touched): the mkxp.json
    keys, Win32API preloads and the TRGSSX stand-in, the font stand-ins, the bundled mkxp-z player and README-HUB.txt.
    Returns what was done plus the warnings found."""
    from types import SimpleNamespace
    from ..core.settings import Options
    from ..detect import detect_engine
    from .project import load_rgss_project
    out = Path(out)
    info = detect_engine(out)
    if info is None or info.engine not in ("ACE", "VX", "XP"):
        raise ValueError("not an exported VX / VX Ace / XP game folder (no Game.ini and Data/ found)")
    if not (out / "mkxp.json").is_file() and not (out / "Hires").is_dir():
        raise ValueError("this folder is not a hires export (no Hires/ folder or mkxp.json); upscale the game first")
    project = load_rgss_project(out, out, info)
    cfg = {}
    try:
        cfg = json.loads(strip_json_comments((out / "mkxp.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    n = cfg.get("textureScalingFactor") or 2
    have_player = (out / "Game").is_file()
    opts = Options(mkxp_path=mkxp_path, rtp_path=rtp_path, bundle_player=not have_player or mkxpmod.locate(mkxp_path) is not None)
    plan = SimpleNamespace(project=project, options=opts, warnings=[], scale=SimpleNamespace(n=n))
    done = apply_hires(plan, out)
    notes = [f"updated {', '.join(done)}"]
    if source:
        n = restore_missing_files(out, Path(source))
        notes.append(f"restored {n} file(s) that the original game has and this export lacked (Audio, Movies ...)" if n else
                     "nothing to restore: every file of the original's other folders is already in the export")
    return notes + plan.warnings


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
