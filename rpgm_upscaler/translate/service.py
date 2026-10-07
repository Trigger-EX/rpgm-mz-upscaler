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
from .detect import codes_intact, soften_punct, squash_repeats, is_japanese, normalize, protect, restore, split_sentences
from .glossary import Glossary, kana_only, romanize


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


PIPELINE = "+p6"      # bump when protection/splitting rules change so stale cached results are not reused


_HIRA_ONLY = re.compile("[\u3041-\u309f]+")
_KATA_ONLY = re.compile("[\u30a1-\u30fc]+")
_KATA = re.compile("[\u30a1-\u30fc]")


def hash_text(s: str) -> int:
    import zlib
    return zlib.crc32(s.encode("utf-8"))


def _is_term_code(c: str) -> bool:
    """A protected entry that is an English name rather than a control code (those start with a backslash or %)."""
    return not c.startswith(("\\", "%"))


def _sound_effect(text: str) -> bool:
    core = re.sub(r"[…・~～!?！？.。、\s]|\\[A-Za-z]+(\[[^\]]*\])?", "", normalize(text))
    if not core or len(core) > 4 or not kana_only(core):
        return False
    return bool(_KATA_ONLY.fullmatch(core)) or core[-1] in "ゃゅょっぁぃぅぇぉー" or bool(re.search(r"[…~～ー]$", text.strip()))


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
        self.terms: dict[str, str] = {}               # game-specific names (ja -> en) that dialogue must reuse verbatim
        self._load_overrides()
        self._backend = backend
        self._secondary = None
        self.beam: int | None = None                  # None = each backend's own default
        self._load_lock = threading.RLock()
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
        if self._backend_tried:
            return self._backend
        with self._load_lock:                                  # two workers must not each load a copy of the model
            if self._backend_tried:
                return self._backend
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
            self._apply_beam()
            self._secondary = loaded[1] if len(loaded) > 1 else None
            self._backend_tried = True
        return self._backend

    def set_beam(self, beam: int | None) -> None:
        """Search width of the neural model: 1-2 trades some quality for speed. Only backends that have a beam use it."""
        self.beam = beam
        self._apply_beam()

    def _apply_beam(self) -> None:
        if self.beam:
            for b in (self._backend, self._secondary):
                if b is not None and hasattr(b, "beam"):
                    b.beam = self.beam

    def reload_backend(self) -> None:
        self._backend, self._secondary, self._backend_tried, self._backend_error = None, None, False, ""

    def status(self) -> dict:
        ok, why = argos.deps_available()
        a, n = argos.find_installed(), nllb.find_installed()
        model = n or a
        # report what is installed without loading it: this is called from the UI thread, and loading takes seconds
        state = "ready" if (ok and model and not self._backend_error) else ("deps-missing" if model and not ok else "missing")
        if ok and self._backend_tried and self._backend is None and model:
            state = "missing"
        injected = self._backend if (self._backend_tried and not model) else None      # a backend handed in by the caller
        if injected is not None:
            state = "ready"
        return {"model": state, "model_path": str(model) if model else None, "deps": ok, "deps_hint": why,
                "models": {"nllb": bool(n), "argos": bool(a)}, "active": getattr(injected, "name", None) or ("nllb" if n else "argos" if a else None),
                "error": self._backend_error, "glossary_size": len(self.glossary), "overrides": len(self.overrides)}

    # ---- game terms (character names): translated once, then kept identical wherever they occur in running text
    def set_terms(self, terms: dict[str, str]) -> None:
        """Names too short or too common in kana to be told apart from ordinary words are left out: hiragana-only names, and
        anything under two characters."""
        self.terms = {normalize(ja): en for ja, en in terms.items()
                      if en and en != ja and len(normalize(ja)) >= 2 and not _HIRA_ONLY.fullmatch(normalize(ja))}

    def _term_spans(self, text: str) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        taken = [False] * len(text)
        for ja in sorted(self.terms, key=len, reverse=True):
            kata = bool(_KATA_ONLY.fullmatch(ja))
            start = text.find(ja)
            while start != -1:
                end = start + len(ja)
                free = not any(taken[start:end])
                edge = not kata or ((start == 0 or not _KATA.match(text[start - 1])) and (end == len(text) or not _KATA.match(text[end])))
                if free and edge:
                    spans.append((start, end, ja))
                    for k in range(start, end):
                        taken[k] = True
                start = text.find(ja, end)
        return sorted(spans)

    def _terms_sig(self, text: str) -> str:
        """Cache suffix naming the terms a text contains, so a result is only reused with the same names."""
        used = self._term_spans(normalize(text)) if self.terms else []
        if not used:
            return ""
        return "+t" + format(hash_text("|".join(sorted({f"{ja}>{self.terms[ja]}" for _, _, ja in used}))), "x")

    # ---- translation
    def _local(self, text: str, romaji: bool = True) -> Result | None:
        """Overrides and glossary only: instant, no model."""
        if text in self.overrides:
            return Result(self.overrides[text], "override", 1.0)
        en, conf = self.glossary.translate(text, romaji)
        if en is not None and conf >= 0.5:
            return Result(en, "glossary", conf)
        if not romaji and _sound_effect(text):         # "むにゃ", "ゴゴゴ": a model invents words for these
            core, tail = re.match(r"(.*?)([…~～!?！？.。、\s]*)$", normalize(text), re.S).groups()
            return Result((romanize(core).capitalize() + tail.replace("。", ".").replace("、", ",")).strip(), "glossary", 0.5)
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
            tag = backend.tag + PIPELINE
            fresh: list[str] = []
            for t in todo:
                hit = self.cache.get(t, tag + self._terms_sig(t))
                if hit is not None:
                    for i in pending[t]:
                        out[i] = Result(hit, "cache", 0.6)
                    done += len(pending[t])
                else:
                    fresh.append(t)
            fresh.sort(key=len)                       # similar lengths share a batch: far less padding, so much faster on a big game
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
                    self.cache.put(t, tag + self._terms_sig(t), en)
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
        return squash_repeats(restore(joined, codes).replace("⁇", "").replace("  ", " ")).strip()

    @staticmethod
    def _usable(en: str, src: str) -> bool:
        """A model that does not know the text answers with nothing, the source again, or mostly unknown-token debris."""
        if not en or en == src:
            return False
        return any(c.isascii() and c.isalpha() for c in en)

    def _protect_all(self, text: str) -> tuple[str, list[str]]:
        """Control codes and known names become placeholders the model copies through; the codes list holds what goes back."""
        protected, codes = protect(normalize(text))
        spans = self._term_spans(protected) if self.terms else []
        for a, b, ja in reversed(spans):
            codes.append(self.terms[ja])
            protected = f"{protected[:a]}[[{len(codes) - 1}]]{protected[b:]}"
        if spans:                                      # indices must run in reading order
            order = [int(x) for x in re.findall(r"\[\[(\d+)\]\]", protected)]
            remap = {old: new for new, old in enumerate(order)}
            protected = re.sub(r"\[\[(\d+)\]\]", lambda mt: f"[[{remap[int(mt.group(1))]}]]", protected)
            codes = [codes[o] for o in order]
        return protected, codes

    def _prepare(self, text: str) -> tuple[list[str], str, list[str]]:
        protected, codes = self._protect_all(text)
        protected = soften_punct(protected)
        return codes, protected, split_sentences(protected) or [protected]

    _NAMES = ["Aldric", "Bryn", "Calyx", "Dorian", "Elowen", "Fenwick", "Garrick", "Hollis"]

    def _retry_without_codes(self, backend, text: str, codes: list[str]) -> str:
        """The model dropped or invented a placeholder. Retry with name codes (\\N[1]) and number codes (\\V[1]) replaced by
        a name-like / number-like stand-in that models copy through, then swapped back. Formatting codes that started or ended
        the text (colour switches, picture codes) are put back at the edges; any other code is dropped rather than guessed."""
        protected, found = self._protect_all(text)
        content = lambda i: bool(re.fullmatch(r"\\[NnPpVv]\[\d+\]|%\d", found[i])) or _is_term_code(found[i])      # noqa: E731  (filled in by the engine)
        ph = re.compile(r"\[\[(\d+)\]\]\s*")
        lead = ""
        pos = 0
        while (mt := ph.match(protected, pos)) and not content(int(mt.group(1))):      # only formatting codes are peeled off the edges
            lead += mt.group(0)
            pos = mt.end()
        end = len(protected)
        trail = ""
        while (mt := re.search(r"\s*\[\[(\d+)\]\]$", protected[pos:end])) and not content(int(mt.group(1))):
            trail = mt.group(0) + trail
            end = pos + mt.start()
        core = protected[pos:end]
        tokens: dict[str, str] = {}

        def sub(mt):
            i = int(mt.group(1))
            c = found[i]
            if re.fullmatch(r"\\[NnPp]\[\d+\]|%\d", c) or _is_term_code(c):          # actor/skill names filled in by the engine: %1 %2
                tok = self._NAMES[len(tokens) % len(self._NAMES)]
            elif re.fullmatch(r"\\[Vv]\[\d+\]", c):
                tok = str(7000 + 13 * len(tokens))
            else:
                return ""
            tokens[tok] = f"[[{i}]]"
            return tok
        bare = soften_punct(re.sub(r"\[\[(\d+)\]\]", sub, core))
        sents = split_sentences(bare) or [bare]
        out = " ".join(p for p in backend.translate_batch(sents) if p).strip()
        if all(out.count(tok) == 1 for tok in tokens):
            for tok, ph in tokens.items():
                out = out.replace(tok, ph)
        else:                                                # the stand-ins did not survive either: drop those codes
            out = re.sub(r"\[\[\d+\]\]", "", re.sub("|".join(map(re.escape, tokens)) or "(?!x)x", "", out)) if tokens else out
        keep = lambda part: "".join(re.findall(r"\[\[\d+\]\]", part))      # noqa: E731
        return f"{keep(lead)}{out}{keep(trail)}"

