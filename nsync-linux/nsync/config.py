"""Configuration and peer list, stored as JSON next to the device's nsec."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Public relays that, when tried, accepted both event kinds Nsync uses (30078 and 10002); many refuse them.
DEFAULT_RELAYS = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net",
                  "wss://relay.snort.social", "wss://nostr.mom", "wss://offchain.pub"]
_OLD_DEFAULT_RELAYS = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net"]


def home() -> Path:
    # Overridable so two instances can run on one machine (tests, demos).
    return Path(os.environ.get("NSYNC_HOME", "~/.config/nsync")).expanduser()


@dataclass
class Peer:
    npub: str
    syncthing_id: str
    name: str = ""


@dataclass
class Config:
    relays: list[str] = field(default_factory=lambda: list(DEFAULT_RELAYS))
    # managed: Nsync runs its own Syncthing (bundled) in its own home, like the Android app.
    # Set false to drive a Syncthing you already run (then url and key below are yours).
    managed: bool = True
    syncthing_url: str = "http://127.0.0.1:8384"
    syncthing_api_key: str | None = None  # external mode: None reads it from Syncthing's config.xml
    gui_port: int | None = None  # managed: the internal GUI port, picked once when free
    sync_port: int | None = None  # managed: Syncthing's listen port, 22000 or the next free one, kept once picked
    announce_port: int | None = None  # None: Syncthing's own listen port
    extra_addresses: list[str] = field(default_factory=list)
    announce_public: bool = True  # false: announce only the LAN address, never the public IP
    poll_interval: int = 60
    heartbeat: int = 1200
    web_port: int = 8385  # 0 turns the page off; always bound to 127.0.0.1
    peers: list[Peer] = field(default_factory=list)
    learned_relays: dict[str, list[str]] = field(default_factory=dict)  # peer pubkey hex -> relays it lists

    @property
    def path(self) -> Path:
        return home() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        path = home() / "config.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        raw["peers"] = [Peer(**p) for p in raw.get("peers", [])]
        if raw.get("relays") == _OLD_DEFAULT_RELAYS:  # never changed by the user: take the new defaults
            raw["relays"] = list(DEFAULT_RELAYS)
        return cls(**raw)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self), indent=2) + "\n")
