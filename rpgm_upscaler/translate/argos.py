"""Offline neural JA->EN with an Argos Translate model, run directly through ctranslate2 + sentencepiece.

The heavy argostranslate package (spacy, stanza, torch) is NOT needed: a .argosmodel is a zip holding a
CTranslate2 model and a SentencePiece model. Installing downloads it once; afterwards nothing touches the network.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


class ArgosError(Exception):
    pass


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "rpgm-upscaler" / "models"


def _is_model_dir(p: Path) -> bool:
    return (p / "model" / "model.bin").is_file() and (p / "sentencepiece.model").is_file()


def find_installed(src: str = "ja", dst: str = "en") -> Path | None:
    roots = [data_dir(), Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "argos-translate" / "packages"]
    best = None
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted([*root.glob(f"translate-{src}_{dst}*"), *root.glob(f"{src}_{dst}*")]):   # the real package unpacks as plain "ja_en"
            if p.is_dir() and _is_model_dir(p):
                best = p
    return best


def deps_available() -> tuple[bool, str]:
    from . import pyenv
    pyenv.activate()
    try:
        import ctranslate2  # noqa: F401
        import sentencepiece  # noqa: F401
        return True, ""
    except ImportError as e:
        return False, f"missing {e.name}. Install with: pip install ctranslate2 sentencepiece (or use the Translation tab's Install Python packages button)"


def package_version(model_dir: Path) -> str:
    try:
        return str(json.loads((model_dir / "metadata.json").read_text(encoding="utf-8")).get("package_version", "?"))
    except (OSError, ValueError):
        return "?"


def validate_zip(path: Path) -> str:
    """Return the zip's top-level folder; raise unless it holds a CTranslate2 model + sentencepiece.model."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArgosError(f"{path.name} is not a readable .argosmodel (zip) file: {e}") from e
    with z:
        names = z.namelist()
        for n in names:
            parts = Path(n).parts
            if n.startswith("/") or ".." in parts:
                raise ArgosError(f"unsafe path in package: {n}")
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(tops) != 1:
            raise ArgosError("package must contain exactly one top-level folder")
        top = tops.pop()
        for need in (f"{top}/model/model.bin", f"{top}/sentencepiece.model"):
            if need not in names:
                raise ArgosError(f"package is missing {need}")
        return top


def import_package(path: str | Path, dest: Path | None = None) -> Path:
    """Install a local .argosmodel file (or an already extracted package folder)."""
    path = Path(path)
    dest = dest or data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        if not _is_model_dir(path):
            raise ArgosError(f"{path} does not look like an extracted Argos model")
        target = dest / path.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(path, target)
        return target
    top = validate_zip(path)
    with tempfile.TemporaryDirectory(dir=dest) as td:
        with zipfile.ZipFile(path) as z:
            z.extractall(td)
        target = dest / top
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(Path(td) / top), str(target))
    return target


def fetch_index_entry(src: str = "ja", dst: str = "en", url: str = INDEX_URL, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (fixed https index)
        index = json.load(r)
    cands = [p for p in index if p.get("from_code") == src and p.get("to_code") == dst]
    if not cands:
        raise ArgosError(f"no {src}->{dst} model in the Argos index")
    return max(cands, key=lambda p: tuple(int(x) if x.isdigit() else 0 for x in str(p.get("package_version", "0")).split(".")))


def download(url: str, dest: Path, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None, timeout: int = 60) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as r, open(dest, "wb") as f:  # noqa: S310
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise ArgosError("cancelled")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def install(progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
            index_url: str = INDEX_URL, entry: dict | None = None) -> Path:
    """Download the newest ja->en model into the data dir (the only networked step)."""
    entry = entry or fetch_index_entry(url=index_url)
    links = [u for u in entry.get("links", []) if u.startswith(("http://", "https://"))]
    if not links:
        raise ArgosError("the index lists no downloadable link for this model")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "model.argosmodel"
        last = None
        for url in links:
            try:
                download(url, f, progress, cancel)
                break
            except ArgosError:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
        else:
            raise ArgosError(f"download failed: {last}")
        return import_package(f)


class ArgosBackend:
    name = "argos"

    def __init__(self, model_dir: Path):
        ok, why = deps_available()
        if not ok:
            raise ArgosError(why)
        import ctranslate2
        import sentencepiece as spm
        self.dir = Path(model_dir)
        self.version = package_version(self.dir)
        self._sp = spm.SentencePieceProcessor(model_file=str(self.dir / "sentencepiece.model"))
        self._tr = ctranslate2.Translator(str(self.dir / "model"), device="cpu", inter_threads=1, intra_threads=os.cpu_count() or 4)
        self._lock = threading.Lock()

    @property
    def tag(self) -> str:
        return f"argos-{self.dir.name}-{self.version}"

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        toks = [self._sp.encode(t, out_type=str) for t in texts]
        order = sorted(range(len(toks)), key=lambda i: len(toks[i]))          # similar lengths batch together: less padding
        with self._lock:
            res = self._tr.translate_batch([toks[i] for i in order], beam_size=2, max_decoding_length=160, max_batch_size=32,
                                           no_repeat_ngram_size=4, repetition_penalty=1.1)
        out = [""] * len(texts)
        for i, r in zip(order, res):
            out[i] = self._sp.decode(r.hypotheses[0]).strip()
        return out
