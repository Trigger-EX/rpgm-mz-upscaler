"""Bundled JA->EN glossary, longest-match segmentation and kana -> romaji transliteration."""
from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

from .detect import _CODES, is_japanese, normalize

_HIRA = "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔ"
_ROM = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o", "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so", "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no", "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo", "や": "ya", "ゆ": "yu", "よ": "yo", "ら": "ra", "り": "ri",
    "る": "ru", "れ": "re", "ろ": "ro", "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n", "ゔ": "vu",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go", "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do", "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
}
_COMBO = {"ゃ": "ya", "ゅ": "yu", "ょ": "yo"}
_SMALL_VOWEL = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
# foreign-sound katakana digraphs (after conversion to hiragana)
_SPECIAL = {"てぃ": "ti", "でぃ": "di", "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
            "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo", "しぇ": "she", "じぇ": "je", "ちぇ": "che", "つぁ": "tsa",
            "とぅ": "tu", "どぅ": "du", "くぁ": "kwa", "いぇ": "ye"}
_PUNCT = {"・": " ", "　": " ", "、": ", ", "。": ". ", "！": "!", "？": "?", "「": '"', "」": '"', "『": '"', "』": '"',
          "（": "(", "）": ")", "〜": "~", "～": "~", "…": "...", "＝": "=", "＋": "+", "ー": ""}


def _to_hiragana(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        out.append(chr(o - 0x60) if 0x30A1 <= o <= 0x30F6 else ch)
    return "".join(out)


def kana_only(s: str) -> bool:
    return bool(s) and all(("\u3040" <= c <= "\u30ff") or c in "ー・゛゜ 　" for c in s)


def romanize(text: str) -> str:
    """Hepburn-style romaji for a kana string. Long vowels are written doubled (no macrons)."""
    s = _to_hiragana(normalize(text))
    out: list[str] = []
    i = 0
    double = False
    while i < len(s):
        ch = s[i]
        pair = s[i:i + 2]
        piece = None
        if pair in _SPECIAL:
            piece, i = _SPECIAL[pair], i + 2
        elif ch == "っ":
            double = True
            i += 1
            continue
        elif ch == "ー":
            if out and out[-1] and out[-1][-1] in "aiueo":
                out.append(out[-1][-1])
            i += 1
            continue
        elif ch in _ROM:
            base = _ROM[ch]
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in _COMBO and base[-1] == "i":
                stem = base[:-1]
                if base in ("shi", "chi", "ji"):
                    piece = base[:-1] + _COMBO[nxt][1:]
                else:
                    piece = stem + _COMBO[nxt]
                i += 2
            elif nxt in _SMALL_VOWEL and base not in ("a", "i", "u", "e", "o"):
                piece = base[:-1] + _SMALL_VOWEL[nxt]
                i += 2
            else:
                piece = base
                i += 1
            if piece == "n" and (s[i:i + 1] and (s[i] in "あいうえおやゆよ")):
                piece = "n'"
        elif ch in _PUNCT:
            piece, i = _PUNCT[ch], i + 1
        else:
            piece, i = ch, i + 1
        if double and piece and piece[0] not in "aiueon' ":
            piece = (("t" if piece.startswith("ch") else piece[0]) + piece)
        double = False
        out.append(piece)
    word = "".join(out).strip()
    return " ".join(w[:1].upper() + w[1:] for w in re.sub(r"\s+", " ", word).split(" ")) if word else word


def load_glossary(extra: Path | None = None) -> dict[str, str]:
    text = resources.files("rpgm_upscaler.translate").joinpath("data/glossary_ja_en.tsv").read_text(encoding="utf-8")
    g: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        ja, en = line.split("\t", 1)
        g.setdefault(normalize(ja.strip()), en.strip())
    if extra and Path(extra).is_file():
        try:
            g.update({normalize(k): v for k, v in json.loads(Path(extra).read_text(encoding="utf-8")).items()})
        except (OSError, ValueError):
            pass
    return g


_PARTICLES = "のをがはにでとも"
_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff]+|\s+|.", re.S)


class Glossary:
    def __init__(self, entries: dict[str, str] | None = None):
        self.entries = entries if entries is not None else load_glossary()
        self.maxlen = max((len(k) for k in self.entries), default=1)

    def __len__(self) -> int:
        return len(self.entries)

    def exact(self, text: str) -> str | None:
        return self.entries.get(normalize(text).strip())

    def translate(self, text: str) -> tuple[str | None, float]:
        """Longest-match segmentation. Returns (english, confidence); (None, 0) unless every Japanese chunk is covered
        by the glossary or is kana-only (romanised). Confidence: 1.0 exact, 0.8 fully segmented, 0.5 if romaji was used."""
        text = normalize(text).strip()
        if not text:
            return None, 0.0
        hit = self.entries.get(text)
        if hit is not None:
            return hit, 1.0
        words: list[str] = []
        used_romaji = False
        whole_kana = kana_only(_CODES.sub("", text).replace(" ", ""))
        pos = 0
        # keep control codes verbatim
        pieces = []
        last = 0
        for m in _CODES.finditer(text):
            pieces.append((text[last:m.start()], False))
            pieces.append((m.group(0), True))
            last = m.end()
        pieces.append((text[last:], False))
        for chunk, is_code in pieces:
            if is_code:
                words.append(chunk)
                continue
            for tok in _TOKEN.findall(chunk):
                if not is_japanese(tok):
                    if tok.strip():
                        words.append(tok.strip())
                    continue
                i = 0
                while i < len(tok):
                    for n in range(min(self.maxlen, len(tok) - i), 0, -1):
                        en = self.entries.get(tok[i:i + n])
                        if en is not None:
                            words.append(en)
                            i += n
                            break
                    else:
                        if tok[i] in _PARTICLES and words and (i + 1 >= len(tok) or any(
                                tok[i + 1:i + 1 + n] in self.entries for n in range(1, min(self.maxlen, len(tok) - i - 1) + 1))):
                            i += 1                      # a particle between known words ("鉄の剣" -> "Iron Sword")
                            continue
                        j = i
                        kata = "\u30a0" <= tok[i] <= "\u30ff"
                        while j < len(tok) and not any(tok[j:j + n] in self.entries for n in range(1, min(self.maxlen, len(tok) - j) + 1)) \
                                and kana_only(tok[j]) and (("\u30a0" <= tok[j] <= "\u30ff") == kata or tok[j] == "ー"):
                            j += 1
                        if j > i and (kata or whole_kana):    # hiragana runs inside real sentences are never guessed
                            words.append(romanize(tok[i:j]))
                            used_romaji = True
                            i = j
                        else:
                            return None, 0.0          # an unknown kanji: let a model (or the user) handle it
        joined, prev_code = "", False
        for w in words:
            if not w:
                continue
            code = bool(_CODES.fullmatch(w))
            joined += w if (not joined or code or prev_code) else " " + w
            prev_code = code
        return (joined or None), (0.5 if used_romaji else 0.8)
