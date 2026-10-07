"""NLLB-200 (distilled 600M, CTranslate2 int8) as a second, stronger offline ja->en model.

It reads short colloquial lines far better than the small Argos model (which answers many of them with unknown-token
markers) but needs about 620 MB of disk and is several times slower. Licence: CC-BY-NC 4.0 (fine for personal use).
Runs on ctranslate2 + sentencepiece only, like the Argos backend.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .argos import ArgosError, data_dir, deps_available, download

REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
BASE_URL = f"https://huggingface.co/{REPO}/resolve/main"
FILES = {"config.json": 159, "shared_vocabulary.txt": 2568098, "sentencepiece.bpe.model": 4852054, "model.bin": 622595991}
DIR_NAME = "nllb-200-600m-int8"


def model_dir() -> Path:
    return data_dir() / DIR_NAME


def find_installed() -> Path | None:
    d = model_dir()
    return d if all((d / f).is_file() for f in FILES) else None


def install(progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None,
            base_url: str = BASE_URL, files: dict[str, int] | None = None) -> Path:
    """Download the model files (about 630 MB) once; afterwards nothing touches the network."""
    files = files or FILES
    total = sum(files.values())
    dest = model_dir()
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        for name, size in files.items():
            def sub(d, t, base=done):
                if progress:
                    progress(min(base + d, total), total)
            download(f"{base_url}/{name}", Path(td) / name, sub, cancel)
            if (Path(td) / name).stat().st_size < min(size, 1000):
                raise ArgosError(f"{name}: download looks incomplete")
            done += size
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(td, str(dest))
        Path(td).mkdir()                      # let the TemporaryDirectory clean up an existing path
    return dest


_REPEAT = re.compile(r"^(.{3,}?[.!?])\s+\1$")


def _tidy(text: str) -> str:
    """NLLB sometimes says a short line twice ("Thank you. Thank you."): collapse an exact doubling."""
    m = _REPEAT.match(text.strip())
    return m.group(1) if m else text.strip()


class NllbBackend:
    name = "nllb"
    SRC, TGT = "jpn_Jpan", "eng_Latn"

    def __init__(self, directory: Path):
        ok, why = deps_available()
        if not ok:
            raise ArgosError(why)
        import ctranslate2
        import sentencepiece as spm
        self.dir = Path(directory)
        self._sp = spm.SentencePieceProcessor(model_file=str(self.dir / "sentencepiece.bpe.model"))
        self._tr = ctranslate2.Translator(str(self.dir), device="cpu", inter_threads=1, intra_threads=os.cpu_count() or 4)
        self._lock = threading.Lock()

    @property
    def tag(self) -> str:
        return "nllb-200-600m-int8"

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        src = [[self.SRC] + self._sp.encode(t, out_type=str) + ["</s>"] for t in texts]
        order = sorted(range(len(src)), key=lambda i: len(src[i]))
        with self._lock:
            res = self._tr.translate_batch([src[i] for i in order], target_prefix=[[self.TGT]] * len(order), beam_size=4,
                                           max_decoding_length=200, max_batch_size=16, no_repeat_ngram_size=5)
        out = [""] * len(texts)
        for i, r in zip(order, res):
            out[i] = _tidy(self._sp.decode(r.hypotheses[0][1:]))
        return out
