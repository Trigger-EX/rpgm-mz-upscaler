"""User options (picklable dataclass) and persisted app settings."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Options:
    target: tuple[int, int] = (1920, 1080)
    scale: str = "fit"                 # "fit" or a number
    engine: str = "lanczos"            # lanczos|nearest|sharp|realesrgan|waifu2x
    engine_path: str = ""
    model: str = ""
    resamplers: dict[str, str] = field(default_factory=dict)   # category -> resampler
    skip: list[str] = field(default_factory=list)              # categories to copy unchanged
    movies: bool = True
    patch: bool = True
    reencrypt: bool = True             # keep encrypted images encrypted
    scale_windowskin: bool = False
    ui_fill: bool = False
    anchor: str = "center"
    workers: int = 0
    resume: bool = True
    bundle_player: bool = True         # VX/Ace/XP hires: copy the mkxp-z player into the export
    allow_no_player: bool = False      # upscale even though no mkxp-z player is installed
    rtp_path: str = ""                 # VX/Ace/XP hires: folder of the RTP (or its parent), when it is not found by itself
    mkxp_path: str = ""                # a folder that already holds mkxp-z (else the downloaded copy)
    orig: tuple[int, int] | None = None   # the resolution the game was authored for, when it cannot be detected

    def digest(self, n: float) -> str:
        d = asdict(self)
        for k in ("workers", "resume", "patch", "bundle_player", "allow_no_player", "mkxp_path", "rtp_path"):
            d.pop(k)
        d["n"] = n
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]

    @staticmethod
    def from_dict(d: dict) -> "Options":
        o = Options()
        for k, v in d.items():
            if hasattr(o, k):
                setattr(o, k, tuple(v) if k in ("target", "orig") and v else v)
        return o


def settings_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "rpgm-upscaler" / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    """Merges into what is stored: every tab saves its own keys and must not erase the others'."""
    p = settings_path()
    merged = {**load_settings(), **data}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2))
        tmp.replace(p)
    except OSError:
        pass
