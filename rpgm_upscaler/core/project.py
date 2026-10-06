"""Detect an RPG Maker MV/MZ HTML5 project and read its metadata."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import crypto


class ProjectError(Exception):
    pass


@dataclass
class Project:
    base: Path                 # directory the user selected (mirrored into the output)
    web: str                   # web root relative to base ("" or "www")
    engine: str                # "MZ" | "MV"
    version: str = ""
    screen: tuple[int, int] = (816, 624)
    ui_area: tuple[int, int] = (816, 624)
    tile_size: int = 48
    font_size: int = 26
    has_encrypted_images: bool = False
    has_encrypted_audio: bool = False
    key: bytes | None = None
    tileset_names: list[list[str]] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def web_root(self) -> Path:
        return self.base / self.web if self.web else self.base

    @property
    def package_json(self) -> Path | None:
        for p in (self.web_root / "package.json", self.base / "package.json"):
            if p.is_file():
                return p
        return None


def find_web_root(base: Path) -> str:
    def ok(p: Path) -> bool:
        return (p / "index.html").is_file() and (p / "js").is_dir() and (p / "data").is_dir()
    if ok(base):
        return ""
    if ok(base / "www"):
        return "www"
    raise ProjectError(f"{base} does not look like an RPG Maker MV/MZ project "
                       "(need index.html, js/ and data/). Packaged games must be extracted first.")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _detect_engine(web: Path) -> tuple[str, str]:
    js = web / "js"
    if (js / "rmmz_core.js").is_file():
        text = (js / "rmmz_core.js").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'''RPGMAKER_VERSION\s*=\s*["']([^"']+)["']''', text)
        return "MZ", m.group(1) if m else ""
    if (js / "rpg_core.js").is_file():
        text = (js / "rpg_core.js").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'''RPGMAKER_VERSION\s*=\s*["']([^"']+)["']''', text)
        return "MV", m.group(1) if m else ""
    raise ProjectError("Neither js/rmmz_core.js nor js/rpg_core.js found.")


def _mv_screen(web: Path) -> tuple[int, int]:
    f = web / "js" / "rpg_managers.js"
    w, h = 816, 624
    if f.is_file():
        text = f.read_text(encoding="utf-8", errors="replace")
        mw = re.search(r"SceneManager\._screenWidth\s*=\s*(\d+)", text)
        mh = re.search(r"SceneManager\._screenHeight\s*=\s*(\d+)", text)
        if mw and mh:
            w, h = int(mw.group(1)), int(mh.group(1))
    return w, h


def load_project(path: str | Path) -> Project:
    base = Path(path).expanduser().resolve()
    if not base.is_dir():
        raise ProjectError(f"{base} is not a directory")
    web_rel = find_web_root(base)
    web = base / web_rel if web_rel else base
    engine, version = _detect_engine(web)
    p = Project(base=base, web=web_rel, engine=engine, version=version)

    sysfile = web / "data" / "System.json"
    system = read_json(sysfile) if sysfile.is_file() else {}
    p.has_encrypted_images = bool(system.get("hasEncryptedImages"))
    p.has_encrypted_audio = bool(system.get("hasEncryptedAudio"))
    if system.get("encryptionKey"):
        try:
            p.key = crypto.key_from_hex(system["encryptionKey"])
        except (ValueError, crypto.CryptoError):
            p.warnings.append("System.json encryptionKey is invalid.")

    if engine == "MZ":
        adv = system.get("advanced", {})
        p.screen = (int(adv.get("screenWidth", 816)), int(adv.get("screenHeight", 624)))
        p.ui_area = (int(adv.get("uiAreaWidth", p.screen[0])), int(adv.get("uiAreaHeight", p.screen[1])))
        p.font_size = int(adv.get("fontSize", 26))
        p.tile_size = int(system.get("tileSize", 48))
    else:
        p.screen = _mv_screen(web)
        p.ui_area = p.screen
        p.font_size = 28

    ts = web / "data" / "Tilesets.json"
    if ts.is_file():
        p.tileset_names = [t["tilesetNames"] for t in read_json(ts) if t and t.get("tilesetNames")]

    pj = web / "js" / "plugins.js"
    if pj.is_file():
        from .patcher import parse_plugins_js
        try:
            p.plugins = [e.get("name", "") for e in parse_plugins_js(pj.read_text(encoding="utf-8"))[1]]
        except Exception as exc:  # noqa: BLE001
            p.warnings.append(f"Could not parse js/plugins.js: {exc}")

    if p.has_encrypted_images and p.key is None:
        for f in (web / "img").rglob("*"):
            if f.suffix.lower() in crypto.ENCRYPTED_IMAGE_EXTS:
                try:
                    p.key = crypto.recover_key(f.read_bytes())
                    p.warnings.append("Encryption key recovered from an image (not found in System.json).")
                except crypto.CryptoError:
                    pass
                break
    if any(base.glob("package.nw")) or any(base.glob("*.nw")):
        p.warnings.append("A packaged archive (.nw) was found next to the project; make sure you selected the extracted game.")
    return p
