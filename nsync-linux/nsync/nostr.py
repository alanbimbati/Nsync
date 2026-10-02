"""Nostr primitives: bech32 (NIP-19) keys and signed events (NIP-01)."""

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from coincurve import PrivateKey, PublicKeyXOnly

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _polymod(values: list[int]) -> int:
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= _GEN[i] if (top >> i) & 1 else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert(data: bytes | list[int], frm: int, to: int, pad: bool) -> list[int]:
    acc = bits = 0
    out = []
    maxv = (1 << to) - 1
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & maxv)
        acc &= (1 << bits) - 1
    if pad and bits:
        out.append((acc << (to - bits)) & maxv)
    elif not pad and (bits >= frm or acc):
        raise ValueError("invalid bech32 padding")
    return out


def bech32_encode(hrp: str, data: bytes) -> str:
    five = _convert(data, 8, 5, True)
    chk = _polymod(_hrp_expand(hrp) + five + [0] * 6) ^ 1
    checksum = [(chk >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in five + checksum)


def bech32_decode(text: str) -> tuple[str, bytes]:
    text = text.strip().lower()
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text):
        raise ValueError("invalid bech32 string")
    hrp, tail = text[:pos], text[pos + 1:]
    if any(c not in _CHARSET for c in tail):
        raise ValueError("invalid bech32 character")
    five = [_CHARSET.index(c) for c in tail]
    if _polymod(_hrp_expand(hrp) + five) != 1:
        raise ValueError("invalid bech32 checksum")
    return hrp, bytes(_convert(five[:-6], 5, 8, False))


def npub_to_hex(npub: str) -> str:
    hrp, data = bech32_decode(npub)
    if hrp != "npub" or len(data) != 32:
        raise ValueError(f"not an npub: {npub}")
    return data.hex()


def hex_to_npub(pubkey_hex: str) -> str:
    return bech32_encode("npub", bytes.fromhex(pubkey_hex))


class Keypair:
    def __init__(self, secret: bytes):
        self._key = PrivateKey(secret)
        self.secret = secret
        self.pubkey_hex = self._key.public_key_xonly.format().hex()

    @property
    def npub(self) -> str:
        return hex_to_npub(self.pubkey_hex)

    @property
    def nsec(self) -> str:
        return bech32_encode("nsec", self.secret)

    @classmethod
    def load_or_create(cls, path: Path) -> "Keypair":
        if path.exists():
            hrp, data = bech32_decode(path.read_text())
            if hrp != "nsec":
                raise ValueError(f"{path} does not hold an nsec")
            return cls(data)
        kp = cls(os.urandom(32))
        path.parent.mkdir(parents=True, exist_ok=True)
        # Created 0600 up front: writing then chmod would leave a window where
        # the secret is world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as f:
            f.write(kp.nsec + "\n")
        return kp

    def sign(self, event: "Event") -> "Event":
        event.pubkey = self.pubkey_hex
        event.id = event.compute_id()
        event.sig = self._key.sign_schnorr(bytes.fromhex(event.id)).hex()
        return event


@dataclass
class Event:
    kind: int
    content: str
    tags: list[list[str]] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))
    pubkey: str = ""
    id: str = ""
    sig: str = ""

    def compute_id(self) -> str:
        raw = json.dumps(
            [0, self.pubkey, self.created_at, self.kind, self.tags, self.content],
            separators=(",", ":"), ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def verify(self) -> bool:
        try:
            if self.compute_id() != self.id:
                return False
            return PublicKeyXOnly(bytes.fromhex(self.pubkey)).verify(
                bytes.fromhex(self.sig), bytes.fromhex(self.id))
        except (ValueError, TypeError):
            return False

    def tag(self, name: str) -> str | None:
        return next((t[1] for t in self.tags if len(t) > 1 and t[0] == name), None)

    def to_dict(self) -> dict:
        return {"id": self.id, "pubkey": self.pubkey, "created_at": self.created_at,
                "kind": self.kind, "tags": self.tags, "content": self.content, "sig": self.sig}

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(kind=int(d["kind"]), content=d["content"], tags=d.get("tags", []),
                   created_at=int(d["created_at"]), pubkey=d["pubkey"], id=d["id"], sig=d["sig"])
