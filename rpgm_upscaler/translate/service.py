"""Layered offline translator: overrides -> glossary/romaji -> (cache) neural model -> unchanged."""
from __future__ import annotations

import json
import re
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import argos, nllb
from .cache import Cache
from .detect import codes_intact, soften_punct, is_japanese, normalize, protect, restore, split_sentences
from .glossary import Glossary


@dataclass
class Result:
    text: str                 # the English (or unchanged) text
    source: str               # passthrough | override | glossary | cache | argos | unchanged
    confidence: float = 1.0   # 0 = nothing could be translated

    @property
    def translated(self) -> bool:
        return self.source not in ("passthrough", "unchanged")


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rpgm-upscaler"


def cache_path() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rpgm-upscaler" / "translate.sqlite"


class Translator:
    def __init__(self, overrides_path: Path | None = None, cache: Cache | None = None, backend=None,
                 use_default_backend: bool = True, glossary: Glossary | None = None):
        self.overrides_path = overrides_path if overrides_path is not None else config_dir() / "translations.json"
        self.glossary = glossary or Glossary(None)
        extra = self.overrides_path.with_name("glossary.json")     # optional user glossary (JSON: {"ja": "en"})
        if extra.is_file():
            try:
                self.glossary = Glossary({**self.glossary.entries, **{normalize(k): v for k, v in
                                                                     json.loads(extra.read_text(encoding="utf-8")).items()}})
            except (OSError, ValueError):
                pass
        self.cache = cache if cache is not None else Cache(cache_path())
        self.overrides: dict[str, str] = {}
        self._load_overrides()
        self._backend = backend
        self._secondary = None
        self._backend_tried = backend is not None or not use_default_backend
        self._backend_error = ""

    # ---- overrides (user-editable exact-text translations)
    def _load_overrides(self) -> None:
        try:
            self.overrides = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.overrides = {}

    def set_override(self, text: str, english: str | None) -> None:
        if english:
            self.overrides[text] = english
        else:
            self.overrides.pop(text, None)
        try:
            self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
            self.overrides_path.write_text(json.dumps(self.overrides, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---- backend
    @property
    def backend(self):
        """Preferred model: NLLB if installed (better on colloquial lines), else Argos. The other one is kept as a fallback."""
        if not self._backend_tried:
            self._backend_tried = True
            loaded = []
            for finder, cls in ((nllb.find_installed, nllb.NllbBackend), (argos.find_installed, argos.ArgosBackend)):
                model = finder()
                if model is None:
                    continue
                try:
                    loaded.append(cls(model))
                except Exception as e:  # noqa: BLE001
                    self._backend_error = str(e)
            self._backend = loaded[0] if loaded else None
            self._secondary = loaded[1] if len(loaded) > 1 else None
        return self._backend

    def reload_backend(self) -> None:
        self._backend, self._secondary, self._backend_tried, self._backend_error = None, None, False, ""

    def status(self) -> dict:
        ok, why = argos.deps_available()
        a, n = argos.find_installed(), nllb.find_installed()
        model = n or a
        b = self.backend if (ok and model) else None
        state = "ready" if b else ("deps-missing" if model and not ok else "missing")
        return {"model": state, "model_path": str(model) if model else None, "deps": ok, "deps_hint": why,
                "models": {"nllb": bool(n), "argos": bool(a)}, "active": getattr(b, "name", None),
                "error": self._backend_error, "glossary_size": len(self.glossary), "overrides": len(self.overrides)}

    # ---- translation
    def _local(self, text: str, romaji: bool = True) -> Result | None:
        """Overrides and glossary only: instant, no model."""
        if text in self.overrides:
            return Result(self.overrides[text], "override", 1.0)
        en, conf = self.glossary.translate(text, romaji)
        if en is not None and conf >= 0.5:
            return Result(en, "glossary", conf)
        return None

    def translate(self, text: str) -> Result:
        return self.translate_many([text])[0]

    def translate_many(self, texts: list[str], progress: Callable[[int, int], None] | None = None,
                       cancel: threading.Event | None = None, romaji: bool = True) -> list[Result]:
        """`romaji=False` is for running text (dialogue, descriptions): kana-only strings go to the model instead of being
        transliterated, which is only right for names."""
        out: list[Result | None] = [None] * len(texts)
        pending: dict[str, list[int]] = {}
        for i, t in enumerate(texts):
            if not is_japanese(t):
                out[i] = Result(t, "passthrough", 1.0)
                continue
            r = self._local(t, romaji)
            if r is not None:
                out[i] = r
                continue
            pending.setdefault(t, []).append(i)
        total = len(texts)
        done = sum(1 for r in out if r is not None)
        if progress:
            progress(done, total)
        backend = self.backend if pending else None
        todo = list(pending)
        if backend is not None:
            tag = backend.tag
            fresh: list[str] = []
            for t in todo:
                hit = self.cache.get(t, tag)
                if hit is not None:
                    for i in pending[t]:
                        out[i] = Result(hit, "cache", 0.6)
                    done += len(pending[t])
                else:
                    fresh.append(t)
            for start in range(0, len(fresh), 32):
                if cancel is not None and cancel.is_set():
                    break
                batch = fresh[start:start + 32]
                prepared = [self._prepare(t) for t in batch]
                flat = [s for _, _, sents in prepared for s in sents]
                tr = backend.translate_batch(flat) if flat else []
                k = 0
                for t, (codes, _, sents) in zip(batch, prepared):
                    pieces = tr[k:k + len(sents)]
                    k += len(sents)
                    en, who = self._finish(backend, t, codes, pieces), backend.name
                    if not self._usable(en, t) and self._secondary is not None:
                        sec = self._secondary.translate_batch(sents)
                        alt = self._finish(self._secondary, t, codes, sec)
                        if self._usable(alt, t):
                            en, who = alt, self._secondary.name
                    if not self._usable(en, t):
                        continue
                    self.cache.put(t, tag, en)
                    for i in pending[t]:
                        out[i] = Result(en, who, 0.6)
                    done += len(pending[t])
                if progress:
                    progress(done, total)
        for t, idxs in pending.items():
            for i in idxs:
                if out[i] is None:
                    out[i] = Result(t, "unchanged", 0.0)
        return [r if r is not None else Result(texts[i], "unchanged", 0.0) for i, r in enumerate(out)]

    def _finish(self, backend, t: str, codes: list[str], pieces: list[str]) -> str:
        joined = " ".join(p for p in pieces if p).strip()
        if not codes_intact(joined, len(codes)):
            joined = self._retry_without_codes(backend, t, codes)
        return restore(joined, codes).replace("⁇", "").replace("  ", " ").strip()

    @staticmethod
    def _usable(en: str, src: str) -> bool:
        """A model that does not know the text answers with nothing, the source again, or mostly unknown-token debris."""
        if not en or en == src:
            return False
        return any(c.isascii() and c.isalpha() for c in en)

    @staticmethod
    def _prepare(text: str) -> tuple[list[str], str, list[str]]:
        protected, codes = protect(normalize(text))
        protected = soften_punct(protected)
        return codes, protected, split_sentences(protected) or [protected]

    @staticmethod
    def _retry_without_codes(backend, text: str, codes: list[str]) -> str:
        """The model dropped or invented a placeholder: translate the text without codes, then put back the codes that
        started or ended it (colour/format switches usually do) and drop the rest rather than guess where they went."""
        protected, found = protect(normalize(text))
        lead = re.match(r"^((?:\[\[\d+\]\]\s*)*)", protected).group(1)
        trail = re.search(r"((?:\s*\[\[\d+\]\])*)$", protected).group(1)
        bare = soften_punct(re.sub(r"\[\[\d+\]\]", "", protected))
        sents = split_sentences(bare) or [bare]
        out = " ".join(p for p in backend.translate_batch(sents) if p).strip()
        keep = lambda part: "".join(re.findall(r"\[\[\d+\]\]", part))      # noqa: E731
        return f"{keep(lead)}{out}{keep(trail)}"
