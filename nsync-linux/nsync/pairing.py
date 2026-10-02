"""The pairing string `<npub>:<SYNCTHING-DEVICE-ID>` shared by the CLI and the web page."""

import re

from .nostr import npub_to_hex

_DEVICE_ID = re.compile(r"^[A-Z2-7]{7}(-[A-Z2-7]{7}){7}$")


def parse_pairing(text: str) -> tuple[str, str]:
    npub, sep, device_id = text.strip().partition(":")
    if not sep:
        raise ValueError("expected <npub>:<SYNCTHING-DEVICE-ID>")
    npub_to_hex(npub)
    if not _DEVICE_ID.match(device_id):
        raise ValueError("not a Syncthing device id")
    return npub, device_id
