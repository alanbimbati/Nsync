"""This machine's LAN address, for peers on the same network."""

import socket


def lan_ipv4() -> str | None:
    # connect() on a UDP socket sends nothing; it only makes the kernel pick
    # the interface (and so the source address) it would route through.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never actually contacted
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith(("127.", "0.")) else ip
