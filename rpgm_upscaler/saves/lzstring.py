"""Port of lz-string 1.3.x compressToBase64 / decompressFromBase64 (what RPG Maker MV uses for .rpgsave).

JavaScript strings are UTF-16: text is processed as 16-bit code units, as lz-string does.
"""
from __future__ import annotations

def _units(text: str) -> str:
    raw = text.encode("utf-16-le", "surrogatepass")
    return "".join(chr(int.from_bytes(raw[i:i + 2], "little")) for i in range(0, len(raw), 2))


def _from_units(s: str) -> str:
    raw = b"".join(ord(c).to_bytes(2, "little") for c in s)
    return raw.decode("utf-16-le", "replace")


def _compress(unc: str, bits: int, get) -> str:
    if unc is None:
        return ""
    dic: dict[str, int] = {}
    to_create: dict[str, bool] = {}
    w = ""
    enlarge, dict_size, num_bits = 2, 3, 2
    data: list[str] = []
    val = pos = 0

    def put(bit: int) -> None:
        nonlocal val, pos
        val = (val << 1) | bit
        if pos == bits - 1:
            pos = 0
            data.append(get(val))
            val = 0
        else:
            pos += 1

    def emit_new(ch: str) -> None:
        nonlocal enlarge, num_bits
        code = ord(ch[0])
        if code < 256:
            for _ in range(num_bits):
                put(0)
            for _ in range(8):
                put(code & 1); code >>= 1
        else:
            v = 1
            for _ in range(num_bits):
                put(v); v = 0
            for _ in range(16):
                put(code & 1); code >>= 1
        enlarge -= 1
        if enlarge == 0:
            enlarge = 2 ** num_bits; num_bits += 1

    for c in unc:
        if c not in dic:
            dic[c] = dict_size; dict_size += 1
            to_create[c] = True
        wc = w + c
        if wc in dic:
            w = wc
            continue
        if w in to_create:
            emit_new(w)
            del to_create[w]
        else:
            v = dic[w]
            for _ in range(num_bits):
                put(v & 1); v >>= 1
        enlarge -= 1
        if enlarge == 0:
            enlarge = 2 ** num_bits; num_bits += 1
        dic[wc] = dict_size; dict_size += 1
        w = c
    if w != "":
        if w in to_create:
            emit_new(w)
            del to_create[w]
        else:
            v = dic[w]
            for _ in range(num_bits):
                put(v & 1); v >>= 1
        enlarge -= 1
        if enlarge == 0:
            enlarge = 2 ** num_bits; num_bits += 1
    v = 2
    for _ in range(num_bits):
        put(v & 1); v >>= 1
    while True:
        val <<= 1
        if pos == bits - 1:
            data.append(get(val))
            break
        pos += 1
    return "".join(data)


def _decompress(length: int, reset: int, nxt) -> str | None:
    dic: dict[int, str] = {0: "0", 1: "1", 2: "2"}
    enlarge, dict_size, num_bits = 4, 4, 3
    result: list[str] = []
    val, position, index = nxt(0), reset, 1

    def read(n: int) -> int:
        nonlocal val, position, index
        bits, power, maxp = 0, 1, 2 ** n
        while power != maxp:
            resb = val & position
            position >>= 1
            if position == 0:
                position = reset
                val = nxt(index); index += 1
            bits |= (1 if resb > 0 else 0) * power
            power <<= 1
        return bits

    first = read(2)
    if first == 0:
        c = chr(read(8))
    elif first == 1:
        c = chr(read(16))
    else:
        return ""
    dic[3] = c
    w = c
    result.append(c)
    while True:
        if index > length:
            return ""
        code = read(num_bits)
        if code == 0:
            dic[dict_size] = chr(read(8)); dict_size += 1
            code = dict_size - 1; enlarge -= 1
        elif code == 1:
            dic[dict_size] = chr(read(16)); dict_size += 1
            code = dict_size - 1; enlarge -= 1
        elif code == 2:
            return "".join(result)
        if enlarge == 0:
            enlarge = 2 ** num_bits; num_bits += 1
        if code in dic:
            entry = dic[code]
        elif code == dict_size:
            entry = w + w[0]
        else:
            return None
        result.append(entry)
        dic[dict_size] = w + entry[0]; dict_size += 1
        enlarge -= 1
        w = entry
        if enlarge == 0:
            enlarge = 2 ** num_bits; num_bits += 1


def compress_to_base64(text: str) -> str:
    """lz-string 1.3.x (the version RPG Maker MV bundles): 16-bit LZ stream, then standard base64 of its bytes."""
    import base64
    packed = _compress(_units(text), 16, chr)
    return base64.b64encode(b"".join(ord(c).to_bytes(2, "big") for c in packed)).decode("ascii")


def decompress_from_base64(b64: str) -> str:
    import base64
    import re
    clean = re.sub(r"[^A-Za-z0-9+/=]", "", b64)
    try:
        raw = base64.b64decode(clean + "=" * (-len(clean) % 4))
    except ValueError as e:
        raise ValueError(f"not base64 data: {e}") from None
    units = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
    if not units:
        raise ValueError("empty LZString data")
    out = _decompress(len(units), 32768, lambda i: units[i] if i < len(units) else 0)
    if out is None:
        raise ValueError("corrupt LZString data")
    return _from_units(out)
