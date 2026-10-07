"""Fonts for mkxp-z output.

Stock RGSS players use the Windows font a script names (`Font.default_name = "UmePlus Gothic"`); mkxp-z only knows the
files in `Fonts/` and the system fonts, and shows an error box when the named font is missing. This module finds the
font names a game asks for, and for each one that cannot be found writes a substitute file into `Fonts/` (named after
the font, which is how mkxp-z looks fonts up) plus a `fontSub` entry in mkxp.json.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..detect import ci_child
from . import scripts as sc

FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")

# Names JP/EN RPG Maker games, scripts and the RTP commonly use (all get a substitute when missing).
COMMON_NAMES = [
    "UmePlus Gothic", "UmePlus P Gothic", "Umeplus Gothic", "UmePlus Mincho", "Umeplus P Gothic",
    "VL Gothic", "VL PGothic", "VL Pゴシック", "VL ゴシック",
    "MS Gothic", "MS PGothic", "MS Mincho", "MS PMincho", "ＭＳ ゴシック", "ＭＳ Ｐゴシック", "ＭＳ 明朝", "ＭＳ Ｐ明朝",
    "Meiryo", "Meiryo UI", "メイリオ", "Yu Gothic", "Yu Mincho", "Yu Gothic UI", "游ゴシック", "游明朝",
    "IPAGothic", "IPAPGothic", "IPAMincho", "IPAPMincho", "IPAexGothic", "IPAexMincho",
    "Takao Gothic", "TakaoPGothic", "TakaoGothic", "TakaoMincho", "Sazanami Gothic", "Sazanami Mincho",
    "Kochi Gothic", "Kochi Mincho", "Hiragino Kaku Gothic Pro", "Hiragino Maru Gothic Pro", "Osaka",
    "M+ 1c", "M+ 1p", "M+ 1m", "M+ 2c", "M+ 2p", "Mplus 1p", "rounded-mplus-1c", "Rounded M+ 1c",
    "Noto Sans CJK JP", "Noto Sans JP", "Noto Serif CJK JP", "Source Han Sans", "Source Han Serif",
    "Kosugi", "Kosugi Maru", "Sawarabi Gothic", "Sawarabi Mincho", "Mona", "Monapo", "Mona-Gothic",
    "TogaliteGothic", "HGGothicM", "HG Gothic", "HGMaruGothicMPRO", "Yu Gothic Medium",
    "Arial", "Arial Black", "Arial Narrow", "Times New Roman", "Courier New", "Verdana", "Tahoma", "Calibri",
    "Cambria", "Georgia", "Trebuchet MS", "Comic Sans MS", "Impact", "Segoe UI", "Lucida Console", "Consolas",
    "Palatino Linotype", "Book Antiqua", "Garamond", "Century Gothic", "Helvetica", "Helvetica Neue", "Open Sans", "Roboto",
    "Malgun Gothic", "Gulim", "Batang", "SimSun", "SimHei", "Microsoft YaHei", "Microsoft JhengHei", "PMingLiU", "MingLiU",
    "Liberation Sans", "Liberation Serif", "Liberation Mono", "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
]

# Substitute candidates, best coverage first: CJK families (they also cover Latin) then Latin ones.
CJK_SUBS = ["Noto Sans CJK JP", "Noto Sans JP", "Source Han Sans", "IPAexGothic", "IPAGothic", "IPAPGothic", "VL Gothic",
            "VL PGothic", "Takao Gothic", "TakaoPGothic", "Sazanami Gothic", "Kochi Gothic", "Unifont", "WenQuanYi Zen Hei",
            "WenQuanYi Micro Hei", "Droid Sans Fallback", "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK KR"]
SERIF_SUBS = ["Noto Serif CJK JP", "Noto Serif JP", "Source Han Serif", "IPAexMincho", "IPAMincho", "IPAPMincho",
              "Takao Mincho", "TakaoMincho", "Sazanami Mincho", "Kochi Mincho"]
LATIN_SUBS = ["Liberation Sans", "DejaVu Sans", "FreeSans", "Noto Sans", "Inter"]
MONO_SUBS = ["Liberation Mono", "DejaVu Sans Mono", "FreeMono", "Noto Sans Mono"]
SERIF_RE = re.compile(r"mincho|serif|明朝|times|georgia|garamond|batang|simsun|mingliu|palatino|cambria|book antiqua", re.I)
MONO_RE = re.compile(r"mono|courier|consolas|lucida console|gothic ?mono", re.I)
LATIN_ONLY_RE = re.compile(r"^[\x20-\x7e]+$")
SYSTEM_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts",
               "/Library/Fonts", "~/Library/Fonts", "/System/Library/Fonts"]

_STR = r"""(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')"""


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name).lower()


def script_font_names(sources: list[str]) -> list[str]:
    """Font names literally written in scripts: Font.default_name = ..., Font.new(...), `.name = ...` and font-ish constants."""
    found: dict[str, None] = {}

    def add(raw: str) -> None:
        s = raw.strip()
        if s and len(s) < 64 and not re.search(r"[\\/#{}\n]", s):
            found.setdefault(s)

    for src in sources:
        for mt in re.finditer(r"(?:default_name|\bFont\.new|\.name|\bfont_?name\w*|\bFONT\w*)\s*(?:=|\()\s*(\[[^\]]*\]|" + _STR + ")", src, re.I):
            for lit in re.finditer(_STR, mt.group(1)):
                add(lit.group(1) or lit.group(2) or "")
    return list(found)


def _fc_list() -> dict[str, Path]:
    """normalized family -> file, through fontconfig when it exists."""
    out: dict[str, Path] = {}
    if shutil.which("fc-list"):
        try:
            r = subprocess.run(["fc-list", ":", "family", "file"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return out
        for line in r.stdout.splitlines():
            path, _, fam = line.partition(": ")
            for f in fam.split(","):
                out.setdefault(_norm(f.split(":")[0]), Path(path))
    return out


def installed_fonts(extra_dirs: list[Path] | None = None) -> dict[str, Path]:
    """normalized family or file stem -> font file (fontconfig, plus a plain scan of the usual font folders)."""
    out = _fc_list()
    dirs = [Path(d).expanduser() for d in SYSTEM_DIRS] + list(extra_dirs or [])
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in FONT_EXTS and p.is_file():
                out.setdefault(_norm(p.stem), p)
    return out


def game_font_files(base: Path) -> dict[str, Path]:
    d = ci_child(base, "Fonts")
    if d is None or not d.is_dir():
        return {}
    return {_norm(p.stem): p for p in d.iterdir() if p.suffix.lower() in FONT_EXTS and p.is_file()}


def pick_substitute(name: str, installed: dict[str, Path], bundled: list[Path] | None = None) -> Path | None:
    """The file that should stand in for a missing font `name`: a CJK font for CJK-ish names, else a look-alike."""
    cjk = not LATIN_ONLY_RE.match(name) or re.search(r"gothic|mincho|plus|m\+|meiryo|mona|kosugi|sawarabi|han|noto|ipa|takao", name, re.I)
    if SERIF_RE.search(name):
        order = (SERIF_SUBS + CJK_SUBS) if cjk else (["Liberation Serif", "DejaVu Serif", "FreeSerif"] + SERIF_SUBS + CJK_SUBS)
    elif MONO_RE.search(name) and not cjk:
        order = MONO_SUBS + LATIN_SUBS
    elif cjk:
        order = CJK_SUBS + LATIN_SUBS
    else:
        order = LATIN_SUBS + CJK_SUBS
    for fam in order:
        p = installed.get(_norm(fam))
        if p is not None:
            return p
    for p in bundled or []:
        if p.is_file():
            return p
    for p in installed.values():                      # anything is better than the error box
        return p
    return None


def provide_fonts(base: Path, out: Path, cfg: dict, sources: list[str], extra_dirs: list[Path] | None = None) -> list[str]:
    """Make every font the game names resolvable by mkxp-z. Returns warnings/notes."""
    notes: list[str] = []
    wanted = script_font_names(sources)
    names = list(dict.fromkeys(wanted + COMMON_NAMES))
    own = game_font_files(base)
    installed = installed_fonts(extra_dirs)
    fonts_dir = out / "Fonts"
    subs = [s for s in cfg.get("fontSub", []) if isinstance(s, str)]
    have_sub = {s.split(">")[0].strip().lower() for s in subs}
    wanted_keys = {_norm(n) for n in wanted}
    copied: set[Path] = set()

    def put(src: Path, name: str) -> bool:
        dst = fonts_dir / (name + src.suffix.lower())
        if dst.exists() or ci_child(fonts_dir, dst.name):
            return True
        try:
            fonts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            copied.add(dst)
            return True
        except OSError as e:
            notes.append(f"could not write font {dst.name}: {e}")
            return False

    for name in names:
        key = _norm(name)
        if key in own or key in installed:
            continue
        sub = pick_substitute(name, installed)
        if sub is None:
            if key in wanted_keys:
                notes.append(f"no font file available to stand in for '{name}'")
            continue
        if not put(sub, sub.stem):                      # one copy per stand-in, so the game runs where that font is not installed
            continue
        if key in wanted_keys:                          # fonts the scripts name get a file of their own name too
            put(sub, name)
        if name.lower() not in have_sub:
            subs.append(f"{name}>{sub.stem}")
    if subs:
        cfg["fontSub"] = subs
    if copied:
        notes.append(f"missing fonts get a stand-in: {len(copied)} font file(s) in Fonts/, {len(subs)} fontSub entries in mkxp.json")
    return notes


def script_sources(base: Path, scripts_path: str) -> list[str]:
    rel = Path(scripts_path.replace("\\", "/"))
    src = base.joinpath(*rel.parts)
    if not src.is_file():
        src = ci_child(base / (rel.parent.name or "Data"), rel.name)
        if src is None:
            return []
    try:
        arr = sc.load(src.read_bytes())
        return [sc.source(e) for e in arr]
    except Exception:  # noqa: BLE001 - an unreadable script list just means fewer names to find
        return []
