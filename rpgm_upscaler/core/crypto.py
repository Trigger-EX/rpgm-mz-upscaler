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
