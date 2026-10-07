"""A private virtualenv the hub can create for itself to hold the optional pip packages.

Distros such as Linux Mint / Debian 12+ / Ubuntu 23.04+ mark the system Python "externally managed" (PEP 668), so a bare
`pip install` is refused. Instead of asking the user to build a venv by hand, the hub creates one under its data dir,
installs the packages there with that venv's pip, and puts its site-packages on sys.path (`activate`) so the packages are
importable without restarting. When the hub already runs inside a virtualenv (./run.sh), pip works there as is and is used.

The user can also name a virtualenv of their own (the Setup page, setting `python_env`); it then takes precedence over both.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

TRANSLATE_PACKAGES = ["ctranslate2>=4,<5", "sentencepiece>=0.2,<0.3"]       # keep in sync with requirements-translate.txt
OCR_PACKAGES = ["opencv-python-headless>=4.8"]                              # keep in sync with requirements-ocr.txt


class EnvError(Exception):
    pass


def custom_env() -> Path | None:
    """The virtualenv the user chose on the Setup page, or None for the default behaviour."""
    from ..core.settings import load_settings
    v = load_settings().get("python_env")
    return Path(v).expanduser() if isinstance(v, str) and v.strip() else None


def set_custom_env(path: str | None) -> None:
    from ..core.settings import save_settings
    save_settings({"python_env": (path or "").strip()})


def describe_target() -> str:
    """Where install_packages would put things right now, for display."""
    c = custom_env()
    if c is not None:
        return f"{c} (your virtual environment)"
    if in_venv():
        return f"{sys.prefix} (the environment the hub runs in)"
    return f"{venv_dir()} (the hub's private virtual environment)"


def venv_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "rpgm-upscaler" / "venv"


def in_venv() -> bool:
    """True when pip can install into the running interpreter's own environment (a venv, not a frozen bundle)."""
    return sys.prefix != sys.base_prefix and not getattr(sys, "frozen", False)


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def site_packages(venv: Path | None = None) -> list[Path]:
    venv = venv or venv_dir()
    if os.name == "nt":
        return [p for p in [venv / "Lib" / "site-packages"] if p.is_dir()]
    return sorted(p for p in (venv / "lib").glob("python3*/site-packages") if p.is_dir())


def activate() -> bool:
    """Make the chosen venv's packages importable (the user's own, else the managed one). Safe to call repeatedly; does
    nothing when the hub runs inside a venv and none was chosen."""
    custom = custom_env()
    if custom is None and in_venv():
        return False
    added = False
    for p in site_packages(custom):
        s = str(p)
        if s not in sys.path:
            sys.path.append(s)
            added = True
    if added:
        import importlib
        importlib.invalidate_caches()
    return added


def _base_python() -> str:
    """The interpreter that creates the venv: the running one, or a system python3 when frozen into a bundle."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    exe = shutil.which("python3") or shutil.which("python")
    if not exe:
        raise EnvError("no python3 found on this system to create the environment with (install python3)")
    return exe


def _run(cmd: list[str], log: Callable[[str], None], cancel: threading.Event | None) -> int:
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(line)
        if cancel is not None and cancel.is_set():
            p.kill()
            p.wait()
            raise EnvError("cancelled")
    return p.wait()


def install_packages(packages: list[str], log: Callable[[str], None] = lambda s: None,
                     cancel: threading.Event | None = None) -> str:
    """pip-install `packages`. Into the user's chosen venv when there is one, else inside a running venv, else into the
    managed venv. A missing or empty venv folder is created first. Returns a short description of where they went."""
    custom = custom_env()
    if custom is not None and Path(_python(custom)).is_file():
        target, py = str(custom), str(_python(custom))
    elif custom is not None and custom.exists() and any(custom.iterdir()):
        raise EnvError(f"{custom} is not a virtual environment (no {_python(custom).relative_to(custom)} in it). "
                       "Pick the folder that contains bin/python (Scripts\\python.exe on Windows), or an empty one to create it.")
    elif custom is None and in_venv():
        target, py = "the current virtual environment", sys.executable
    else:
        venv = custom or venv_dir()
        py = str(_python(venv))
        if not Path(py).is_file():
            log(f"Creating a virtual environment in {venv}")
            venv.parent.mkdir(parents=True, exist_ok=True)
            if venv.exists():
                shutil.rmtree(venv, ignore_errors=True)      # a half-made one from an earlier failed attempt
            if _run([_base_python(), "-m", "venv", str(venv)], log, cancel) != 0 or not Path(py).is_file():
                shutil.rmtree(venv, ignore_errors=True)
                raise EnvError("could not create a virtual environment. On Linux Mint / Ubuntu / Debian install the venv module "
                               "first: sudo apt install python3-venv")
        target = str(venv)
    log("Installing " + ", ".join(packages))
    if _run([py, "-m", "pip", "install", "--disable-pip-version-check", *packages], log, cancel) != 0:
        raise EnvError("pip could not install the packages (see the log above; an internet connection is needed)")
    activate()
    return target
