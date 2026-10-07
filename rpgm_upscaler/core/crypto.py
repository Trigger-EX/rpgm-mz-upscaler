"""RPG Maker MV/MZ asset encryption (fake 16-byte header + XOR of first 16 bytes)."""
from __future__ import annotations

HEADER = bytes.fromhex("5250474d560000000003010000000000")
PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a0000000d49484452")

ENCRYPTED_IMAGE_EXTS = {".rpgmvp": ".png", ".png_": ".png"}
ENCRYPTED_AUDIO_EXTS = {".rpgmvo": ".ogg", ".rpgmvm": ".m4a", ".ogg_": ".ogg", ".m4a_": ".m4a"}


class CryptoError(ValueError):
    pass


def key_from_hex(hexstr: str) -> bytes:
    key = bytes.fromhex(hexstr)
    if len(key) != 16:
        raise CryptoError("encryption key must be 16 bytes (32 hex chars)")
    return key


def _xor16(data: bytes, key: bytes) -> bytes:
    head = bytes(a ^ b for a, b in zip(data[:16], key))
    return head + data[16:]


def decrypt(data: bytes, key: bytes) -> bytes:
    if data[:5] != HEADER[:5]:
        raise CryptoError("not an RPG Maker encrypted file")
    return _xor16(data[16:], key)


def encrypt(data: bytes, key: bytes) -> bytes:
    return HEADER + _xor16(data, key)


def looks_like_png(data: bytes) -> bool:
    return data[:8] == PNG_MAGIC[:8]


def recover_key(encrypted: bytes) -> bytes:
    """Recover the key from an encrypted PNG (its first 16 plaintext bytes are known)."""
    if len(encrypted) < 32:
        raise CryptoError("file too short")
    return bytes(a ^ b for a, b in zip(encrypted[16:32], PNG_MAGIC))


def png_size_from_head(head: bytes, key: bytes | None = None, encrypted: bool = False) -> tuple[int, int] | None:
    """Width and height of a PNG from its first bytes, without reading or decrypting the rest. For an encrypted file the
    encrypted part is only the 16-byte signature + IHDR header, so the size itself (bytes 16-24 of the real PNG) is readable
    even without the key; with a key the signature is checked as well, which tells a wrong key from a right one."""
    if encrypted:
        if len(head) < 16 + 24 or head[:5] != HEADER[:5]:
            return None
        if key is not None and not looks_like_png(_xor16(head[16:32], key)):
            return None
        png = bytes(16) + head[32:40]                       # only bytes 16..24 are used below
    else:
        if len(head) < 24 or not looks_like_png(head):
            return None
        png = head
    w, h = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return (w, h) if w > 0 and h > 0 else None


def key_decrypts(encrypted_head: bytes, key: bytes) -> bool:
    return len(encrypted_head) >= 32 and encrypted_head[:5] == HEADER[:5] and looks_like_png(_xor16(encrypted_head[16:32], key))
