"""The mkxp-z player for VX / VX Ace / XP "hires" exports.

mkxp-z publishes no releases, only GitHub Actions builds. nightly.link serves those artifacts without a GitHub login. The Linux
builds are self-contained: the executable plus `scripts/` and `stdlib/`, with only libc linked dynamically. The player is
downloaded once into the user's data folder and then copied into every export, so the exported folder starts with `./Game`.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Callable

from ..translate.argos import ArgosError, download, open_url

PAGE = "https://nightly.link/mkxp-z/mkxp-z/workflows/autobuild/dev"
RELEASES_PAGE = PAGE                                                  # where a person can pick a build by hand
SOURCE_PAGE = "https://github.com/mkxp-z/mkxp-z"
# Artifact name stems per CPU, best first: the Ubuntu xenial build links the oldest glibc, so it runs on the most systems.
PREFERRED = {
    "x86_64": ["mkxp-z.linux.ubuntu.xenial.x86_64", "mkxp-z.linux.debian.trixie.x86_64"],
    "aarch64": ["mkxp-z.linux.ubuntu.xenial.arm64", "mkxp-z.linux.debian.trixie.arm64"],
    "armv7l": ["mkxp-z.linux.ubuntu.xenial.armv7", "mkxp-z.linux.debian.trixie.armv7"],
    "i686": ["mkxp-z.linux.ubuntu.xenial.i386"],
}
MARKER = "VERSION.txt"
RGSS_ENGINES = ("ACE", "VX", "XP")

HELP = (
    "The mkxp-z player lets a VX / VX Ace / XP game run at the upscaled size. Get it with the 'Install mkxp-z' button "
    "(Upscale tab, mkxp-z box): it downloads the newest Linux build once from nightly.link, a mirror of mkxp-z's own "
    "automatic builds, and every export then carries it. Other ways: 'Use existing…' to point at a folder that already "
    f"holds mkxp-z (or set RPGM_MKXPZ_DIR), pick a build yourself at {RELEASES_PAGE}, or build it from {SOURCE_PAGE}."
)


class MkxpError(Exception):
    pass


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "rpgm-upscaler" / "mkxp-z"


def player_exe(folder: Path) -> Path | None:
    """The mkxp-z executable inside `folder` (mkxp-z, mkxp-z.x86_64, mkxp-z.arm64 ...), else None."""
    if not folder.is_dir():
        return None
    for p in sorted(folder.iterdir()):
        if p.is_file() and re.fullmatch(r"mkxp-z(\.[A-Za-z0-9_\-]+)?", p.name) and p.suffix.lower() not in (".json", ".txt", ".md"):
            return p
    return None


def locate(custom: str = "") -> Path | None:
    """The folder holding a usable player: a folder the user chose, $RPGM_MKXPZ, else the downloaded copy."""
    for raw in (custom, os.environ.get("RPGM_MKXPZ_DIR", "")):
        if raw and player_exe(Path(raw).expanduser()) is not None:
            return Path(raw).expanduser()
    cur = cache_dir() / "current"
    return cur if player_exe(cur) is not None else None


def version(folder: Path | None) -> str:
    f = folder / MARKER if folder else None
    return f.read_text(encoding="utf-8").strip() if f and f.is_file() else ""


def supported() -> bool:
    return platform.system() == "Linux" and platform.machine() in PREFERRED


HELP_CLI = ("Get it with 'rpgm-hub mkxp install' (downloads the newest Linux build once from nightly.link, a mirror of mkxp-z's own "
            f"automatic builds), point --mkxp-path (or $RPGM_MKXPZ_DIR) at a folder that already holds mkxp-z, pick a build at {RELEASES_PAGE}, "
            f"or build it from {SOURCE_PAGE}.")


def requirement(engine: str, mode: str, opts, cli: bool = False) -> str | None:
    """None when an export may go ahead; else why not. Only VX / Ace / XP "hires" exports need the player."""
    if engine not in RGSS_ENGINES or mode != "hires":
        return None
    if getattr(opts, "allow_no_player", False) or not getattr(opts, "bundle_player", True):
        return None
    if locate(getattr(opts, "mkxp_path", "")) is not None:
        return None
    if cli:
        return ("the mkxp-z player is not installed, so the exported game could not start by itself. " + HELP_CLI +
                " To upscale anyway (you add the player yourself), pass --allow-no-player.")
    return ("The mkxp-z player is not installed, so the exported game could not start by itself.\n\n" + HELP +
            "\n\nTo upscale anyway (you add the player yourself), tick 'Upscale without mkxp-z'.")


def find_latest(page: str = PAGE, machine: str | None = None) -> tuple[str, str]:
    """(download url, build name) of the newest Linux build for this CPU. Fetches only the index page."""
    machine = machine or platform.machine()
    stems = PREFERRED.get(machine)
    if not stems:
        raise MkxpError(f"no prebuilt mkxp-z for {machine}; build it from source ({SOURCE_PAGE})")
    try:
        with open_url(page, 30) as r:
            html = r.read().decode("utf-8", errors="replace")
    except OSError as e:
        raise MkxpError(f"could not reach nightly.link: {e}") from e
    links = re.findall(r'href="(https://nightly\.link/mkxp-z/mkxp-z/workflows/autobuild/dev/([^"/]+?)\.zip)"', html)
    for stem in stems:
        for url, name in links:
            if name.startswith(stem + "."):
                return url, name
    raise MkxpError("nightly.link lists no Linux build for this CPU right now")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if root != target and root not in target.parents:
            raise MkxpError(f"unsafe path in the archive: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
            continue                                                  # links are not needed and could point anywhere
        zf.extract(info, dest)
        if not info.is_dir() and (info.external_attr >> 16) & 0o111:
            (dest / info.filename).chmod(0o755)


def install(progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
            page: str = PAGE, machine: str | None = None) -> Path:
    """Download the newest player into the cache (replacing an older copy) and return its folder."""
    url, name = find_latest(page, machine)
    base = cache_dir()
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as td:
        z = Path(td) / "player.zip"
        try:
            download(url, z, progress, cancel, timeout=60)
        except (ArgosError, OSError) as e:
            raise MkxpError(f"download failed: {e}") from e
        stage = Path(td) / "new"
        try:
            with zipfile.ZipFile(z) as zf:
                _safe_extract(zf, stage)
        except zipfile.BadZipFile as e:
            raise MkxpError("the download is not a valid zip") from e
        exe = player_exe(stage)
        if exe is None or exe.stat().st_size < 1_000_000:
            raise MkxpError("the download holds no mkxp-z player")
        exe.chmod(0o755)
        (stage / MARKER).write_text(name + "\n", encoding="utf-8")
        cur = base / "current"
        if cur.exists():
            shutil.rmtree(cur)
        shutil.move(str(stage), str(cur))
    return cur


def copy_into(out: Path, src: Path) -> list[str]:
    """Put the player next to the game: `Game`, plus `scripts/` and `stdlib/`. The export's own mkxp.json is never touched."""
    exe = player_exe(src)
    if exe is None:
        raise MkxpError("the mkxp-z folder holds no player")
    done = []
    game = out / "Game"
    shutil.copyfile(exe, game)
    game.chmod(0o755)
    done.append("Game")
    for sub in ("scripts", "stdlib"):
        if (src / sub).is_dir():
            shutil.copytree(src / sub, out / sub, dirs_exist_ok=True)
            done.append(sub)
    lic = src / "LICENSE.txt"
    if lic.is_file():
        shutil.copyfile(lic, out / "mkxp-z-LICENSE.txt")
    return done
