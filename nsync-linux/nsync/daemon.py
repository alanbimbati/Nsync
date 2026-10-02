"""Announce this device's address on Nostr and apply the peers' to Syncthing."""

import asyncio
import ipaddress
import json
import logging
import re
import time

import segno

from .config import DEFAULT_RELAYS, Config, Peer, home
from .core import Core
from .lan import lan_ipv4
from .nostr import Event, Keypair, npub_to_hex
from .pairing import parse_pairing
from .relay import RelayPool
from .stun import public_ipv4
from .syncthing import Syncthing
from .web import start_web

log = logging.getLogger(__name__)

# NIP-78 application-specific data: addressable, so a relay keeps only the
# newest announcement per (author, d) and a device never piles up stale ones.
KIND = 30078
# NIP-65 relay list: a replaceable event listing where this device reads and writes. A peer that
# changed its relays can still be found as long as one relay is shared with the last list it published.
KIND_RELAYS = 10002
MAX_PEER_RELAYS = 5
MAX_RELAYS = 12
D_TAG = "nsync/v1"
MAX_ADDRESSES = 8
_ADDR = re.compile(r"^tcp://(\[[0-9a-fA-F:]+\]|[0-9.]+):(\d{1,5})$")


def valid_address(addr: str) -> bool:
    """Only a literal, routable IP: a peer's announcement must not be able to
    steer this machine's Syncthing at loopback or at a name it resolves."""
    m = _ADDR.match(addr)
    if not m or not 0 < int(m.group(2)) < 65536:
        return False
    try:
        ip = ipaddress.ip_address(m.group(1).strip("[]"))
    except ValueError:
        return False
    return not (ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local)


def valid_relay(url: str) -> bool:
    """A public wss:// relay by name. A peer's list must not make this machine connect to an IP
    literal, localhost or a host with credentials in the URL."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme != "wss" or not host or "." not in host or parts.username or parts.password or len(url) > 200:
            return False
        parts.port  # raises on a bad port
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True
    except ValueError:
        return False


def valid_own_relay(url: str) -> bool:
    """A relay the user adds. Same rules as a peer's, plus plain ws:// for this machine or a private
    network, so Nsync can be tried against a relay of one's own."""
    if valid_relay(url):
        return True
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "ws" or parts.username or parts.password or len(url) > 200:
            return False
        parts.port
        if host == "localhost":
            return True
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


MAX_OWN_RELAYS = 20
HEARTBEAT_MINUTES = (5, 120)


def build_relay_list(keys: Keypair, relays: list[str], now: int | None = None) -> Event:
    event = Event(kind=KIND_RELAYS, content="", created_at=now or int(time.time()),
                  tags=[["r", url] for url in relays])
    return keys.sign(event)


def build_announcement(keys: Keypair, device_id: str, addresses: list[str],
                       heartbeat: int, now: int | None = None) -> Event:
    now = now or int(time.time())
    content = json.dumps({"v": 1, "device_id": device_id, "addresses": addresses})
    # Expires after a few missed heartbeats, so a powered-off device drops out
    # of the relays by itself instead of advertising a dead address.
    event = Event(kind=KIND, content=content, created_at=now,
                  tags=[["d", D_TAG], ["expiration", str(now + 3 * heartbeat)]])
    return keys.sign(event)


class Daemon:
    def __init__(self, config: Config, keys: Keypair, syncthing: Syncthing):
        self.config = config
        self.keys = keys
        self.syncthing = syncthing
        self._peers: dict[str, Peer] = {npub_to_hex(p.npub): p for p in config.peers}
        self._last_seen: dict[str, int] = {}
        self._relays_seen: dict[str, int] = {}
        # Relays learned from paired peers' lists, kept in the config so a restart still reaches them.
        self._peer_relays: dict[str, list[str]] = {k: [u for u in v if valid_relay(u)]
                                                   for k, v in config.learned_relays.items() if k in self._peers}
        self._announced: list[str] = []
        self._announced_at = 0.0
        self.announced_wall = 0  # unix time of the last publish, for the web page
        self.peer_state: dict[str, dict] = {}  # pubkey hex -> last accepted addresses and time
        self.pool: RelayPool | None = None
        self.core: Core | None = None
        self._force = False  # announce at the next check even if nothing changed and nothing is due
        self.last_check_wall = 0  # when the address was last looked at, changed or not
        self._core_ready = not config.managed  # an external Syncthing is assumed to be up

    def handle_event(self, event: Event, now: int | None = None) -> bool:
        """Apply a peer's announcement to Syncthing. Returns True if applied."""
        now = now or int(time.time())
        peer = self._peers.get(event.pubkey)
        if peer is not None and event.kind == KIND_RELAYS:
            return self._handle_relay_list(event, peer)
        if peer is None or event.kind != KIND or event.tag("d") != D_TAG:
            return False
        if not event.verify():
            log.warning("bad signature on event claiming to be from %s", peer.name or peer.npub[:12])
            return False
        expiry = event.tag("expiration")
        if expiry and expiry.isdigit() and int(expiry) < now:
            return False
        # A relay can replay an older announcement; only a newer one may win.
        if event.created_at <= self._last_seen.get(event.pubkey, 0):
            return False
        try:
            data = json.loads(event.content)
            device_id, addresses = data["device_id"], data["addresses"]
        except (ValueError, KeyError, TypeError):
            return False
        if device_id != peer.syncthing_id:
            log.warning("%s announced device %s, expected %s: ignoring",
                        peer.name or peer.npub[:12], device_id[:7], peer.syncthing_id[:7])
            return False
        addresses = [a for a in addresses if isinstance(a, str) and valid_address(a)][:MAX_ADDRESSES]
        if not addresses:
            return False
        self._last_seen[event.pubkey] = event.created_at
        self.peer_state[event.pubkey] = {"addresses": addresses, "at": event.created_at}
        changed = self.syncthing.set_addresses(device_id, addresses)
        if changed:
            log.info("%s is at %s", peer.name or device_id[:7], ", ".join(addresses))
        return True

    def _handle_relay_list(self, event: Event, peer: Peer) -> bool:
        if not event.verify() or event.created_at <= self._relays_seen.get(event.pubkey, 0):
            return False
        urls = []
        for tag in event.tags:
            if len(tag) > 1 and tag[0] == "r" and valid_relay(tag[1]) and tag[1] not in urls:
                urls.append(tag[1])
        self._relays_seen[event.pubkey] = event.created_at
        urls = urls[:MAX_PEER_RELAYS]
        if urls == self._peer_relays.get(event.pubkey):
            return False
        self._peer_relays[event.pubkey] = urls
        self.config.learned_relays[event.pubkey] = urls
        self.config.save()
        log.info("%s uses relays %s", peer.name or peer.npub[:12], ", ".join(urls))
        return True

    def effective_relays(self) -> list[str]:
        """Ours first, then the ones the paired peers list, without duplicates."""
        urls = list(self.config.relays)
        for peer_urls in self._peer_relays.values():
            urls += [u for u in peer_urls if u not in urls]
        return urls[:MAX_RELAYS]

    async def _own_addresses(self) -> list[str]:
        port = self.config.announce_port or self.syncthing.listen_port()
        addrs = list(self.config.extra_addresses)
        # LAN first: two devices on the same network often cannot reach each
        # other through the shared public address (no hairpin NAT).
        lan = lan_ipv4()
        if lan:
            addrs.append(f"tcp://{lan}:{port}")
        ip = await public_ipv4() if self.config.announce_public else None
        if ip:
            addrs.append(f"tcp://{ip}:{port}")
        return addrs

    async def _configure_core(self) -> None:
        """Once the bundled core answers: give it our listen port and make sure paired devices exist in it."""
        for _ in range(180):
            if await asyncio.to_thread(self.syncthing.healthy):
                break
            await asyncio.sleep(1)
        else:
            log.error("the Syncthing core did not come up; see %s", home() / "syncthing.log")
            return
        port = self.config.sync_port
        await asyncio.to_thread(self.syncthing.patch_options, {
            "listenAddresses": [f"tcp://0.0.0.0:{port}", f"quic://0.0.0.0:{port}", "dynamic+https://relays.syncthing.net/endpoint"],
            "urAccepted": -1,  # no usage-reporting prompt in a fresh GUI
            "startBrowser": False,
        })
        for peer in self.config.peers:
            if await asyncio.to_thread(self.syncthing.device, peer.syncthing_id) is None:
                await asyncio.to_thread(self.syncthing.add_device, peer.syncthing_id, peer.name or peer.syncthing_id[:7])
        self._core_ready = True
        log.info("Syncthing core ready, listening on port %s", port)
        try:
            await self.announce_if_needed()  # now, not after the next poll
        except Exception:
            log.exception("announce failed")

    async def announce_if_needed(self) -> None:
        if not self._core_ready:
            return
        addrs = await self._own_addresses()
        if not addrs:
            log.warning("could not determine a public address, nothing to announce")
            return
        self.last_check_wall = int(time.time())
        due = self._force or time.monotonic() - self._announced_at >= self.config.heartbeat
        if addrs == self._announced and not due:
            return
        self._force = False
        if addrs != self._announced:
            log.info("announcing %s", ", ".join(addrs))
        event = build_announcement(self.keys, self.syncthing.device_id(), addrs, self.config.heartbeat)
        await self.pool.publish(event)
        self._announced, self._announced_at = addrs, time.monotonic()
        self.announced_wall = int(time.time())
        await self.pool.publish(build_relay_list(self.keys, self.config.relays))

    async def refresh_now(self) -> None:
        """Announce now, and ask the relays again for what the peers published."""
        self._force = True
        await self.announce_if_needed()
        if self.pool:
            await self.pool.set_filters(self._filters())

    async def add_relay(self, url: str) -> None:
        url = url.strip()
        if not valid_own_relay(url):
            raise ValueError("a relay address looks like wss://relay.example.com")
        if url in self.config.relays:
            raise ValueError("that relay is already in the list")
        if len(self.config.relays) >= MAX_OWN_RELAYS:
            raise ValueError(f"at most {MAX_OWN_RELAYS} relays")
        self.config.relays.append(url)
        self.config.save()
        await self._relays_changed()

    async def remove_relay(self, url: str) -> None:
        if url not in self.config.relays:
            raise ValueError("not one of your relays")
        if len(self.config.relays) == 1:
            raise ValueError("keep at least one relay, or no device can find this one")
        self.config.relays.remove(url)
        self.config.save()
        await self._relays_changed()

    async def reset_relays(self) -> None:
        self.config.relays = list(DEFAULT_RELAYS)
        self.config.save()
        await self._relays_changed()

    async def _relays_changed(self) -> None:
        # The relay list is part of what peers read, and a new relay should hear from us now.
        if self.pool:
            await self.pool.set_urls(self.effective_relays())
            if self._core_ready:
                self._force = True
                await self.announce_if_needed()

    async def set_settings(self, heartbeat_minutes: int | None = None, announce_public: bool | None = None) -> None:
        if heartbeat_minutes is not None:
            lo, hi = HEARTBEAT_MINUTES
            if not lo <= heartbeat_minutes <= hi:
                raise ValueError(f"between {lo} and {hi} minutes")
            self.config.heartbeat = heartbeat_minutes * 60
        if announce_public is not None:
            self.config.announce_public = bool(announce_public)
        self.config.save()
        if announce_public is not None and self._core_ready:
            self._force = True
            await self.announce_if_needed()

    def _relay_states(self) -> list[dict]:
        return [{**r.status(), "own": r.url in self.config.relays} for r in (self.pool.relays if self.pool else [])]

    def _schedule(self) -> dict:
        nxt = self.announced_wall + self.config.heartbeat if self.announced_wall else None
        return {"check_seconds": self.config.poll_interval, "republish_minutes": self.config.heartbeat // 60,
                "last_check_at": self.last_check_wall, "announced_at": self.announced_wall, "next_republish_at": nxt}

    def summary(self) -> dict:
        """Cheap to compute (no call to the core): the strip on Syncthing's page polls this."""
        relays = self._relay_states()
        return {"core_ready": self._core_ready, "relays_total": len(relays),
                "relays_connected": sum(r["connected"] for r in relays),
                "relays_accepted": sum(1 for r in relays if r["accepted_at"] and r["accepted_at"] >= self.announced_wall - 5),
                "peers": len(self.config.peers), "announced": self._announced, **self._schedule()}

    def _filters(self) -> list[dict]:
        authors = list(self._peers) or [self.keys.pubkey_hex]
        return [{"kinds": [KIND], "#d": [D_TAG], "authors": authors}, {"kinds": [KIND_RELAYS], "authors": authors}]

    async def pair(self, text: str, name: str = "") -> None:
        npub, device_id = parse_pairing(text)
        self.config.peers = [p for p in self.config.peers if p.npub != npub] + [Peer(npub, device_id, name)]
        self.config.save()
        self._peers = {npub_to_hex(p.npub): p for p in self.config.peers}
        if await asyncio.to_thread(self.syncthing.device, device_id) is None:
            await asyncio.to_thread(self.syncthing.add_device, device_id, name or device_id[:7])
        if self.pool:
            await self.pool.set_filters(self._filters())

    async def unpair(self, npub: str) -> None:
        self.config.peers = [p for p in self.config.peers if p.npub != npub]
        self.config.save()
        self._peers = {npub_to_hex(p.npub): p for p in self.config.peers}
        self.peer_state.pop(npub_to_hex(npub), None)
        self._peer_relays.pop(npub_to_hex(npub), None)
        self.config.learned_relays.pop(npub_to_hex(npub), None)
        self.config.save()
        if self.pool:
            await self.pool.set_filters(self._filters())
            await self.pool.set_urls(self.effective_relays())

    def status(self) -> dict:
        sid = self.syncthing.device_id()
        return {
            "npub": self.keys.npub,
            "syncthing_id": sid,
            "qr_svg": segno.make(f"{self.keys.npub}:{sid}", error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff"),
            "core_ready": self._core_ready,
            "announced": self._announced,
            "announced_at": self.announced_wall,
            "relays": self._relay_states(),
            "schedule": self._schedule(),
            "announce_public": self.config.announce_public,
            "default_relays": DEFAULT_RELAYS,
            "peers": [
                {"name": p.name, "npub": p.npub, "syncthing_id": p.syncthing_id,
                 **(self.peer_state.get(npub_to_hex(p.npub)) or {"addresses": [], "at": 0})}
                for p in self.config.peers
            ],
        }

    def _on_event(self, event: Event) -> None:
        # Syncthing's REST client is blocking; keep it off the relay's read loop.
        async def apply():
            applied = await asyncio.to_thread(self.handle_event, event)
            if applied and event.kind == KIND_RELAYS and self.pool:
                await self.pool.set_urls(self.effective_relays())
        asyncio.get_running_loop().create_task(apply())

    async def run(self) -> None:
        if not self._peers:
            log.warning("no peers paired yet: run `nsync pair <npub>:<syncthing-id>`")
        self.pool = RelayPool(self.effective_relays(), self._filters(), self._on_event)
        self.pool.start()
        configure = None
        if self.config.managed:
            self.core = Core(self.config)
            self.core.start()
            configure = asyncio.create_task(self._configure_core())
        web = start_web(self, self.config.web_port) if self.config.web_port else None
        try:
            while True:
                try:
                    await self.announce_if_needed()
                except Exception:
                    log.exception("announce failed")
                await asyncio.sleep(self.config.poll_interval)
        finally:
            if web:
                web.shutdown()
            if configure:
                configure.cancel()
            if self.core:
                self.core.stop()
            await self.pool.stop()
