"""Public IPv4 discovery with a single STUN binding request (RFC 5389)."""

import asyncio
import ipaddress
import os
import socket
import struct

MAGIC = 0x2112A442
SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)]


def parse_binding_response(data: bytes, txid: bytes) -> str | None:
    if len(data) < 20:
        return None
    mtype, length, magic = struct.unpack("!HHI", data[:8])
    if mtype != 0x0101 or magic != MAGIC or data[8:20] != txid:
        return None
    pos = 20
    while pos + 4 <= min(len(data), 20 + length):
        atype, alen = struct.unpack("!HH", data[pos:pos + 4])
        body = data[pos + 4:pos + 4 + alen]
        # XOR-MAPPED-ADDRESS, IPv4 only: the port is not used (Syncthing's own
        # listen port is what peers dial), so just the address is decoded.
        if atype == 0x0020 and alen >= 8 and body[1] == 0x01:
            return str(ipaddress.IPv4Address(struct.unpack("!I", body[4:8])[0] ^ MAGIC))
        pos += 4 + alen + (-alen % 4)
    return None


def _query(server: tuple[str, int], timeout: float) -> str | None:
    txid = os.urandom(12)
    request = struct.pack("!HHI", 0x0001, 0, MAGIC) + txid
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(request, server)
        data, _ = s.recvfrom(2048)
    return parse_binding_response(data, txid)


async def public_ipv4(timeout: float = 3.0) -> str | None:
    for server in SERVERS:
        try:
            ip = await asyncio.to_thread(_query, server, timeout)
            if ip:
                return ip
        except OSError:
            continue
    return None
