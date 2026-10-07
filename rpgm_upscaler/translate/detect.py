"""Japanese detection and protection of RPG Maker control codes."""
from __future__ import annotations

import re
import unicodedata

_JA = re.compile("[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f\u3400-\u4dbf\u4e00-\u9fff]")
# \C[2] \V[1] \N[1] \I[64] \G \. \| \! \{ \} \\, printf-style %1 %2, and plugin codes with any argument such as
# \F[reia_normal] \FF[pose_01] \AA[FF] (standing pictures, name tags): everything the engine or a plugin parses must survive.
_CODES = re.compile(r"\\[A-Za-z]+\[[^\]\n]*\]|\\[A-Za-z]+|\\[.|!><^${}]|\\\\|%\d")


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
    """Split on Japanese/Western sentence ends and newlines, keeping the delimiters attached. Closing quotes and brackets stay
    with their sentence, and fragments with no letters in them ("!", "」") are merged into the previous piece: a model asked to
    translate bare punctuation makes something up."""
    parts = [p for p in re.split(r"(?<=[。！？!?\n])(?![」』）)】\]\"”!?！？])", text) if p != ""]
    out: list[str] = []
    for p in parts:
        if out and not any(c.isalnum() for c in re.sub(r"\[\[\d+\]\]", "", p)):
            out[-1] += p
        else:
            out.append(p)
    return out


_PUNCT = str.maketrans({"【": "[", "】": "]", "「": '"', "」": '"', "『": '"', "』": '"', "〈": "(", "〉": ")", "《": "(", "》": ")",
                        "〜": "~", "～": "~", "・": " ", "―": "-", "ー": "ー", "“": '"', "”": '"'})


def soften_punct(text: str) -> str:
    """CJK brackets and quotes become ASCII so the model does not see (and garble) characters it has no vocabulary for."""
    return text.translate(_PUNCT)


def codes_intact(text: str, n: int) -> bool:
    """Every protected placeholder [[0]]..[[n-1]] appears exactly once (a model may drop, repeat or invent them)."""
    found = re.findall(r"\[\[\s*(\d+)\s*\]\]", text)
    return sorted(int(x) for x in found) == list(range(n))


_REPEAT_RUN = re.compile(r"\b(\w[\w']*)([,.!?\s-]+)\1\b(?:\2\1\b)+", re.I)


def squash_repeats(text: str) -> str:
    """Models loop on interjections ("please, please, please, please"): keep at most two in a row."""
    return _REPEAT_RUN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}", text)
