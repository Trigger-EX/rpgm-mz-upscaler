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
CJK_SUBS = ["IPAexGothic", "IPAGothic", "IPAPGothic", "VL Gothic", "Noto Sans CJK JP", "Noto Sans JP", "Source Han Sans",
            "VL PGothic", "Takao Gothic", "TakaoPGothic", "Sazanami Gothic", "Kochi Gothic", "Unifont", "WenQuanYi Zen Hei",
            "WenQuanYi Micro Hei", "Droid Sans Fallback", "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK KR"]
SERIF_SUBS = ["IPAexMincho", "IPAMincho", "IPAPMincho", "Noto Serif CJK JP", "Noto Serif JP", "Source Han Serif",
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
    """Font names literally written in scripts: any quoted text on a line that mentions Font/font (Font.exist?, default_name,
    Font.new, .name =, FONT_NAME ...), and any literal that is one of the well-known names."""
    found: dict[str, None] = {}
    known = {n.lower() for n in COMMON_NAMES}

    def add(raw: str) -> None:
        t = raw.strip()
        if t and len(t) < 64 and not re.search(r"[\\/#{}\n]|\.(png|jpg|bmp|ogg|rb|txt|ini)$", t, re.I):
            found.setdefault(t)

    for src in sources:
        lines = src.splitlines()
        near = 0                                     # lines left in the window after a line that mentions Font (multi-line arrays)
        for line in lines:
            if re.search(r"font", line, re.I):
                near = 3
            if near > 0 and re.search(r"font|^\s*[\[\]\"',]|\]\s*$", line, re.I):
                near -= 1
                for lit in re.finditer(_STR, line):
                    add(lit.group(1) or lit.group(2) or "")
            else:
                near = 0
                for lit in re.finditer(_STR, line):
                    text = lit.group(1) or lit.group(2) or ""
                    if text.strip().lower() in known:
                        add(text)
    return list(found)


_BAD_STYLE = re.compile(r"italic|oblique|bold|black|light|thin|condensed|narrow|semi|extra|heavy|demi", re.I)


def _style_rank(style: str) -> int:
    """0 = plain Regular/Book, 1 = unknown style, 2 = italic/bold/... (never a stand-in unless nothing else exists)."""
    if re.fullmatch(r"\s*(regular|book|roman|normal|medium)?\s*", style, re.I):
        return 0
    return 2 if _BAD_STYLE.search(style) else 1


def _fc_list() -> dict[str, Path]:
    """normalized family -> its Regular file, through fontconfig when it exists."""
    best: dict[str, tuple[int, Path]] = {}
    if shutil.which("fc-list"):
        try:
            r = subprocess.run(["fc-list", ":", "family", "style", "file"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return {}
        for line in r.stdout.splitlines():
            path, _, rest = line.partition(": ")
            fam, _, style = rest.partition(":style=")
            rank = _style_rank(style.split(",")[0])
            for f in fam.split(","):
                key = _norm(f)
                if key and (key not in best or rank < best[key][0]):
                    best[key] = (rank, Path(path))
    return {k: v[1] for k, v in best.items()}


def font_family(path: Path) -> str:
    """The family name mkxp-z will know this file by (lowercase): fontconfig's first family, else Pillow's, else the file name.
    mkxp-z keys fonts by the name stored inside the file, never by the file name."""
    if shutil.which("fc-scan"):
        try:
            r = subprocess.run(["fc-scan", "--format", "%{family[0]}\\n", str(path)], capture_output=True, text=True, timeout=20)
            first = r.stdout.splitlines()[0].strip() if r.stdout.strip() else ""
            if first:
                return first.lower()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        from PIL import ImageFont
        return ImageFont.truetype(str(path), 12).getname()[0].lower()
    except Exception:  # noqa: BLE001
        return path.stem.lower()


def japanese_fonts() -> list[Path]:
    """Installed font files that cover Japanese (fontconfig's lang=ja), best-looking families first."""
    if not shutil.which("fc-list"):
        return []
    try:
        r = subprocess.run(["fc-list", ":lang=ja", "file", "family"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in r.stdout.splitlines():
        path = line.partition(": ")[0].strip()
        if Path(path).suffix.lower() in FONT_EXTS and Path(path).is_file():
            out.append(Path(path))
    return out


INSTALL_HINT = "install a Japanese font (Linux Mint/Ubuntu: sudo apt install fonts-noto-cjk fonts-ipafont) and run the upscale again"


def installed_fonts(extra_dirs: list[Path] | None = None) -> dict[str, Path]:
    """normalized family or file stem -> font file (fontconfig, plus a plain scan of the usual font folders)."""
    out = _fc_list()
    dirs = [Path(d).expanduser() for d in SYSTEM_DIRS] + list(extra_dirs or [])
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in FONT_EXTS and p.is_file() and _style_rank(p.stem.rpartition("-")[2]) < 2:
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
    if cjk:
        for p in japanese_fonts():                    # any font fontconfig says covers Japanese
            return p
    for p in installed.values():                      # anything is better than the error box
        return p
    return None


def _drop_misnamed_stand_ins(out: Path) -> None:
    """Older versions copied a stand-in as 'Fonts/UmePlus Gothic.ttf'. mkxp-z ignores file names, and such a file made the tool
    believe the font was provided. Remove files named like a well-known font whose own family is something else."""
    known = {_norm(n) for n in COMMON_NAMES}
    for f in list(game_font_files(out).values()):
        if _norm(f.stem) in known and font_family(f) != f.stem.strip().lower() and _norm(font_family(f)) != _norm(f.stem):
            try:
                f.unlink()
            except OSError:
                pass


def provide_fonts(base: Path, out: Path, cfg: dict, sources: list[str], extra_dirs: list[Path] | None = None) -> list[str]:
    """Make every font the game names resolvable by mkxp-z. Returns warnings/notes.

    mkxp-z lowercases a requested name and applies `fontSub` ("from>to") once, but stores the `from` keys as written, so both
    sides must be lowercase; `to` must be the family name stored inside a font file that sits in the game's Fonts/ folder."""
    notes: list[str] = []
    if not japanese_fonts() and not any(re.search(r"cjk|ipa|gothic|takao|han", k) for k in installed_fonts(extra_dirs)):
        notes.append("this computer has no Japanese font, so Japanese text (and mkxp-z's own error boxes) shows as squares: " + INSTALL_HINT)
    wanted = script_font_names(sources)
    names = list(dict.fromkeys(wanted + COMMON_NAMES))
    _drop_misnamed_stand_ins(out)
    own_files = list(game_font_files(base).values())
    own_families = {font_family(f) for f in own_files}                # mkxp-z keys fonts by the family inside the file, not the file name
    installed = installed_fonts(extra_dirs)
    fonts_dir = out / "Fonts"
    # entries an older version wrote (mixed-case keys, file-name targets) can never match in mkxp-z: drop them, keep valid ones
    present = {font_family(f) for f in game_font_files(out).values()}
    subs = [x for x in cfg.get("fontSub", []) if isinstance(x, str) and ">" in x and x == x.lower() and x.split(">")[1] in present]
    have_sub = {x.split(">")[0].strip().lower() for x in subs}
    wanted_keys = {_norm(n) for n in wanted}
    copied: dict[Path, str] = {}                     # stand-in file -> its family (lowercase), one copy each

    def put(src: Path) -> str | None:
        if src in copied:
            return copied[src]
        dst = fonts_dir / src.name
        try:
            fonts_dir.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copyfile(src, dst)
        except OSError as e:
            notes.append(f"could not write font {dst.name}: {e}")
            return None
        copied[src] = font_family(src)
        return copied[src]

    for name in names:
        key = _norm(name)
        low = name.strip().lower()
        if low in have_sub or low in own_families or key in {_norm(x) for x in own_families}:
            continue
        exact = installed.get(key)
        if exact is not None and key in wanted_keys:                                   # the very font is installed here: ship it
            fam = put(exact)
            if fam:
                continue
        sub = pick_substitute(name, installed)
        if sub is None:
            if key in wanted_keys:
                notes.append(f"no font file available to stand in for '{name}'")
            continue
        fam = put(sub)
        if fam and fam != low:
            subs.append(f"{low}>{fam}")
            have_sub.add(low)
    if subs:
        cfg["fontSub"] = subs
    if copied:
        notes.append(f"missing fonts get a stand-in: {len(copied)} font file(s) in Fonts/ and {len(subs)} fontSub entries in mkxp.json")
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
