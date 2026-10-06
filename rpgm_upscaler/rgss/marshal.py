"""Ruby Marshal 4.8 codec that round-trips byte-exactly (object links, symbol links, floats, user types).

Edits are made on the loaded Python tree; shared objects stay shared, so Ruby aliasing survives a write.
"""
from __future__ import annotations

import math
import struct
from typing import Any

MAJOR, MINOR = 4, 8


class MarshalError(ValueError):
    def __init__(self, msg: str, offset: int | None = None):
        super().__init__(msg if offset is None else f"{msg} (at byte {offset})")


class Sym(str):
    """A Ruby Symbol."""
    __slots__ = ()

    def __repr__(self) -> str:
        return ":" + str.__repr__(self)[1:-1]


class _Ext:
    """Mixin holding the 'e' (extended modules), 'C' (user subclass) and 'I' (instance vars) wrappers."""
    ext: list[str] | None = None
    uclass: str | None = None
    ivars: dict[str, Any] | None = None
    had_i: bool = False


class RString(_Ext):
    def __init__(self, data: bytes = b"", ivars: dict | None = None):
        self.data, self.ivars = data, ivars if ivars is not None else {}

    @property
    def text(self) -> str:
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError:
            return self.data.decode("cp932", errors="replace")

    @text.setter
    def text(self, value: str) -> None:
        enc = self.ivars.get("E") if self.ivars else None
        legacy = not self.ivars          # Ruby 1.8 (VX) strings carry no encoding ivar
        self.data = value.encode("utf-8") if (enc is True or not legacy or value.isascii()) else value.encode("cp932")

    def __hash__(self) -> int:
        return hash(self.data)

    def __eq__(self, other) -> bool:
        return isinstance(other, RString) and other.data == self.data

    def __repr__(self) -> str:
        return f"RString({self.text!r})"


class RFloat(float, _Ext):
    """Float that remembers its original serialised text so unchanged values are written back verbatim."""
    raw: bytes | None = None

    def __new__(cls, value: float, raw: bytes | None = None):
        o = float.__new__(cls, value)
        o.raw = raw
        return o


class RBignum(int):
    """Integer outside the Fixnum range (kept as an object so its identity / links survive)."""


class RArray(list, _Ext):
    def __hash__(self) -> int:
        return hash(tuple(self))


class RHash(dict, _Ext):
    has_default = False
    default: Any = None

    def __hash__(self) -> int:
        return id(self)


class RObject(_Ext):
    def __init__(self, cls: str, ivars: dict | None = None):
        self.cls, self.ivars = cls, ivars if ivars is not None else {}

    def __repr__(self) -> str:
        return f"RObject({self.cls}, {list(self.ivars)})"


class RStruct(_Ext):
    def __init__(self, cls: str, members: dict | None = None):
        self.cls, self.members = cls, members if members is not None else {}


class RUserDef(_Ext):
    """'u': an object serialised by its own _dump (Table, Color, Tone, Rect). Bytes are kept verbatim."""
    def __init__(self, cls: str, data: bytes = b""):
        self.cls, self.data = cls, data
        self.ivars = {}


class RUserMarshal(_Ext):
    def __init__(self, cls: str, obj: Any = None):
        self.cls, self.obj = cls, obj


class RData(_Ext):
    def __init__(self, cls: str, obj: Any = None):
        self.cls, self.obj = cls, obj


class RRegexp(_Ext):
    def __init__(self, source: bytes, options: int, ivars: dict | None = None):
        self.source, self.options, self.ivars = source, options, ivars if ivars is not None else {}


class RClassRef(_Ext):
    def __init__(self, name: str, kind: str = "c"):
        self.name, self.kind = name, kind


# --------------------------------------------------------------------------------------- reader
class _Reader:
    def __init__(self, buf: bytes, pos: int = 0):
        self.b, self.p = buf, pos
        self.syms: list[Sym] = []
        self.objs: list[Any] = []

    def byte(self) -> int:
        if self.p >= len(self.b):
            raise MarshalError("unexpected end of data", self.p)
        v = self.b[self.p]
        self.p += 1
        return v

    def take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.b):
            raise MarshalError("unexpected end of data", self.p)
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def long(self) -> int:
        c = self.byte()
        c = c - 256 if c > 127 else c
        if c == 0:
            return 0
        if 4 < c < 128:
            return c - 5
        if -129 < c < -4:
            return c + 5
        if c > 0:
            x = 0
            for i in range(c):
                x |= self.byte() << (8 * i)
            return x
        n = -c
        x = -1
        for i in range(n):
            x &= ~(0xFF << (8 * i))
            x |= self.byte() << (8 * i)
        return x

    def bytes_(self) -> bytes:
        return self.take(self.long())

    def symbol(self) -> Sym:
        t = self.byte()
        if t == 0x3A:  # ':'
            s = Sym(self.bytes_().decode("utf-8", errors="surrogateescape"))
            self.syms.append(s)
            return s
        if t == 0x3B:  # ';'
            return self.syms[self.long()]
        if t == 0x49:  # 'I' wrapping a symbol (non-ascii symbol with encoding)
            s = self.symbol()
            for _ in range(self.long()):
                self.symbol()
                self.obj()
            return s
        raise MarshalError(f"expected symbol, got {chr(t)!r}", self.p - 1)

    def reg(self, o):
        self.objs.append(o)
        return o

    def ivars_into(self, o) -> None:
        iv = {}
        for _ in range(self.long()):
            k = self.symbol()
            iv[str(k)] = self.obj()
        if getattr(o, "ivars", None):
            o.ivars.update(iv)
        else:
            o.ivars = iv
        o.had_i = True

    def obj(self, _ivp: list | None = None):
        t = self.byte()
        c = chr(t)
        if c == "0":
            return None
        if c == "T":
            return True
        if c == "F":
            return False
        if c == "i":
            return self.long()
        if c == ":":
            self.p -= 1
            return self.symbol()
        if c == ";":
            return self.syms[self.long()]
        if c == "@":
            i = self.long()
            if i >= len(self.objs):
                raise MarshalError(f"bad object link {i}", self.p)
            return self.objs[i]
        if c == "I":
            ivp: list = [True]
            o = self.obj(ivp)
            if isinstance(o, Sym):                 # non-ascii symbol: encoding ivars carry no information we keep
                for _ in range(self.long()):
                    self.symbol()
                    self.obj()
            elif ivp[0]:
                self.ivars_into(o)
            return o
        if c == "e":
            mod = str(self.symbol())
            o = self.obj(_ivp)
            o.ext = [mod] + (o.ext or [])
            return o
        if c == "C":
            cname = str(self.symbol())
            o = self.obj(_ivp)
            o.uclass = cname
            return o
        if c == '"':
            return self.reg(RString(self.bytes_()))
        if c == "f":
            raw = self.bytes_()
            txt = raw.decode("ascii")
            v = {"inf": math.inf, "-inf": -math.inf, "nan": math.nan}.get(txt)
            if v is None:
                v = float(txt.split("\0")[0])
            return self.reg(RFloat(v, raw))
        if c == "l":
            sign = self.byte()
            n = self.long()
            mag = int.from_bytes(self.take(n * 2), "little")
            return self.reg(RBignum(-mag if sign == 0x2D else mag))
        if c == "[":
            a = self.reg(RArray())
            for _ in range(self.long()):
                a.append(self.obj())
            return a
        if c in "{}":
            h = self.reg(RHash())
            for _ in range(self.long()):
                k = self.obj()
                h[k] = self.obj()
            if c == "}":
                h.has_default, h.default = True, self.obj()
            return h
        if c == "o":
            name = str(self.symbol())
            o = self.reg(RObject(name))
            n = self.long()
            for _ in range(n):
                k = self.symbol()
                o.ivars[str(k)] = self.obj()
            if _ivp is not None:
                _ivp[0] = False      # ivars were this object's own, no trailing 'I' block follows
            return o
        if c == "S":
            name = str(self.symbol())
            o = self.reg(RStruct(name))
            for _ in range(self.long()):
                k = self.symbol()
                o.members[str(k)] = self.obj()
            return o
        if c == "u":
            name = str(self.symbol())
            return self.reg(RUserDef(name, self.bytes_()))
        if c == "U":
            name = str(self.symbol())
            o = self.reg(RUserMarshal(name))
            o.obj = self.obj()
            return o
        if c == "d":
            name = str(self.symbol())
            o = self.reg(RData(name))
            o.obj = self.obj()
            return o
        if c == "/":
            src = self.bytes_()
            return self.reg(RRegexp(src, self.byte()))
        if c in "cmM":
            return self.reg(RClassRef(self.bytes_().decode("utf-8"), c))
        raise MarshalError(f"unsupported type byte {c!r}", self.p - 1)


def _header(r: _Reader) -> None:
    if r.byte() != MAJOR or r.byte() > MINOR:
        raise MarshalError("not a Marshal 4.x stream", 0)


def load_at(buf: bytes, pos: int = 0) -> tuple[Any, int]:
    r = _Reader(buf, pos)
    _header(r)
    return r.obj(), r.p


def loads(buf: bytes) -> Any:
    o, end = load_at(buf)
    if end != len(buf):
        raise MarshalError(f"{len(buf) - end} trailing bytes (several streams? use load_all)", end)
    return o


def load_all(buf: bytes) -> list:
    out, pos = [], 0
    while pos < len(buf):
        o, pos = load_at(buf, pos)
        out.append(o)
    return out


# --------------------------------------------------------------------------------------- writer
def _fmt_float(v: float) -> bytes:
    if math.isnan(v):
        return b"nan"
    if math.isinf(v):
        return b"inf" if v > 0 else b"-inf"
    if v == 0:
        return b"-0" if math.copysign(1, v) < 0 else b"0"
    s = repr(float(v))
    if s.endswith(".0"):
        s = s[:-2]
    return s.encode("ascii")


class _Writer:
    def __init__(self):
        self.out = bytearray()
        self.syms: dict[str, int] = {}
        self.objs: dict[int, int] = {}
        self._keep: list = []

    def long(self, n: int) -> None:
        o = self.out
        if n == 0:
            o.append(0)
        elif 0 < n < 123:
            o.append(n + 5)
        elif -124 < n < 0:
            o.append((n - 5) & 0xFF)
        else:
            buf = bytearray()
            x = n
            for _ in range(4):
                buf.append(x & 0xFF)
                x >>= 8
                if x == 0 or x == -1:
                    break
            o.append(len(buf) if n > 0 else (-len(buf)) & 0xFF)
            o += buf

    def bytes_(self, b: bytes) -> None:
        self.long(len(b))
        self.out += b

    def symbol(self, name: str) -> None:
        i = self.syms.get(name)
        if i is not None:
            self.out.append(0x3B)
            self.long(i)
            return
        self.syms[name] = len(self.syms)
        raw = name.encode("utf-8", errors="surrogateescape")
        if raw.isascii():
            self.out.append(0x3A)
            self.bytes_(raw)
        else:                                  # non-ascii symbol: 'I' ':' name 1 :E T
            self.out.append(0x49)
            self.out.append(0x3A)
            self.bytes_(raw)
            self.long(1)
            self.symbol("E")
            self.out.append(0x54)

    def ivars(self, d: dict) -> None:
        self.long(len(d))
        for k, v in d.items():
            self.symbol(k)
            self.obj(v)

    def link(self, o) -> bool:
        i = self.objs.get(id(o))
        if i is not None:
            self.out.append(0x40)
            self.long(i)
            return True
        self.objs[id(o)] = len(self.objs)
        self._keep.append(o)
        return False

    def wrappers(self, o) -> None:
        for m in (o.ext or []):
            self.out.append(0x65)
            self.symbol(m)

    def obj(self, o) -> None:
        out = self.out
        if o is None:
            out.append(0x30)
        elif o is True:
            out.append(0x54)
        elif o is False:
            out.append(0x46)
        elif isinstance(o, Sym):
            self.symbol(str(o))
        elif isinstance(o, int) and not isinstance(o, RBignum):
            if -(1 << 30) <= o < (1 << 30):
                out.append(0x69)
                self.long(o)
            else:
                self.bignum(RBignum(o), fresh=True)
        elif isinstance(o, RBignum):
            if not self.link(o):
                self.bignum(o)
        elif isinstance(o, float):
            if self.link(o):
                return
            raw = getattr(o, "raw", None)
            if raw is None or (not math.isnan(o) and _parse_raw(raw) != float(o)):
                raw = _fmt_float(float(o))
            out.append(0x66)
            self.bytes_(raw)
        elif isinstance(o, str):                       # plain str: treat as a symbol-less text -> RString
            self.obj(RString(o.encode("utf-8"), {"E": True}))
        elif type(o) is list:
            self.ext_obj(RArray(o))
        elif type(o) is dict:
            self.ext_obj(RHash(o))
        elif isinstance(o, _Ext) or isinstance(o, (list, dict)):
            self.ext_obj(o)
        else:
            raise MarshalError(f"cannot serialise {type(o).__name__}")

    def bignum(self, o: int, fresh: bool = False) -> None:
        if fresh:
            self.objs[id(o)] = len(self.objs)
            self._keep.append(o)
        v = int(o)
        mag = abs(v)
        n = (mag.bit_length() + 15) // 16
        self.out.append(0x6C)
        self.out.append(0x2D if v < 0 else 0x2B)
        self.long(n)
        self.out += mag.to_bytes(n * 2, "little")

    def ext_obj(self, o) -> None:
        if self.link(o):
            return
        iv = getattr(o, "ivars", None)
        if isinstance(o, RObject):
            has_i = False
        elif isinstance(o, RUserDef):
            has_i = bool(iv) or o.had_i
        else:
            has_i = bool(iv) or getattr(o, "had_i", False)
        if has_i:
            self.out.append(0x49)
        self.wrappers(o)
        if isinstance(o, RString):
            if o.uclass:
                self.out.append(0x43); self.symbol(o.uclass)
            self.out.append(0x22); self.bytes_(o.data)
        elif isinstance(o, list):
            if o.uclass:
                self.out.append(0x43); self.symbol(o.uclass)
            self.out.append(0x5B); self.long(len(o))
            for x in o:
                self.obj(x)
        elif isinstance(o, dict):
            if o.uclass:
                self.out.append(0x43); self.symbol(o.uclass)
            self.out.append(0x7D if o.has_default else 0x7B); self.long(len(o))
            for k, v in o.items():
                self.obj(k)
                self.obj(v)
            if o.has_default:
                self.obj(o.default)
        elif isinstance(o, RObject):
            self.out.append(0x6F); self.symbol(o.cls); self.ivars(o.ivars)
            return
        elif isinstance(o, RStruct):
            self.out.append(0x53); self.symbol(o.cls); self.long(len(o.members))
            for k, v in o.members.items():
                self.symbol(k); self.obj(v)
        elif isinstance(o, RUserDef):
            self.out.append(0x75); self.symbol(o.cls); self.bytes_(o.data)
        elif isinstance(o, RUserMarshal):
            self.out.append(0x55); self.symbol(o.cls); self.obj(o.obj)
        elif isinstance(o, RData):
            self.out.append(0x64); self.symbol(o.cls); self.obj(o.obj)
        elif isinstance(o, RRegexp):
            self.out.append(0x2F); self.bytes_(o.source); self.out.append(o.options)
        elif isinstance(o, RClassRef):
            self.out.append(ord(o.kind)); self.bytes_(o.name.encode("utf-8"))
        else:
            raise MarshalError(f"cannot serialise {type(o).__name__}")
        if has_i:
            self.ivars(iv or {})


def _parse_raw(raw: bytes) -> float:
    t = raw.decode("ascii")
    return {"inf": math.inf, "-inf": -math.inf, "nan": math.nan}.get(t, None) if t in ("inf", "-inf", "nan") \
        else float(t.split("\0")[0])


def dumps(o: Any) -> bytes:
    w = _Writer()
    w.out += bytes([MAJOR, MINOR])
    w.obj(o)
    return bytes(w.out)


def dump_all(objs: list) -> bytes:
    return b"".join(dumps(o) for o in objs)


# --------------------------------------------------------------------------------------- helpers
class Table:
    """Decoder for RPG::Table / Table _dump payloads (dims + int16 cells)."""

    def __init__(self, data: bytes):
        self.dim, self.x, self.y, self.z, size = struct.unpack_from("<5i", data, 0)
        self.cells = list(struct.unpack_from(f"<{size}h", data, 20))

    def dump(self) -> bytes:
        return struct.pack("<5i", self.dim, self.x, self.y, self.z, len(self.cells)) + struct.pack(f"<{len(self.cells)}h", *self.cells)
