"""Japanese detection and protection of RPG Maker control codes."""
from __future__ import annotations

import re
import unicodedata

_JA = re.compile("[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f\u3400-\u4dbf\u4e00-\u9fff]")
# \C[2] \V[1] \N[1] \I[64] \G \. \| \! \{ \} \\ and printf-style %1 %2
_CODES = re.compile(r"\\[A-Za-z]+\[\d+\]|\\[A-Za-z]|\\[.|!><^${}]|\\\\|%\d")


def is_japanese(text: str) -> bool:
    return bool(text) and bool(_JA.search(text))


def normalize(text: str) -> str:
    """NFKC folds full-width latin/digits (ＡＢＣ１２３ -> ABC123) and half-width kana to full width."""
    return unicodedata.normalize("NFKC", text)


def protect(text: str) -> tuple[str, list[str]]:
    """Replace control codes with placeholders a translation model will not touch."""
    codes: list[str] = []

    def sub(m: re.Match) -> str:
        codes.append(m.group(0))
        return f"[[{len(codes) - 1}]]"

    return _CODES.sub(sub, text), codes


def restore(text: str, codes: list[str]) -> str:
    for i, c in enumerate(codes):
        text = re.sub(rf"\[\[\s*{i}\s*\]\]", lambda _m, c=c: c, text)
    return text


def split_sentences(text: str) -> list[str]:
    """Split on Japanese/Western sentence ends and newlines, keeping the delimiters attached."""
    parts = re.split(r"(?<=[。！？!?\n])", text)
    return [p for p in parts if p != ""]
