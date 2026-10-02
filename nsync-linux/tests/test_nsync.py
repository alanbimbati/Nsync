import asyncio
import json
import os
import struct

import pytest
import websockets

from nsync.config import Config, Peer
from nsync.daemon import KIND, KIND_RELAYS, Daemon, build_announcement, build_relay_list, valid_address, valid_own_relay, valid_relay
from nsync.nostr import Event, Keypair, hex_to_npub, npub_to_hex
from nsync.relay import RelayPool
from nsync.stun import MAGIC, parse_binding_response

def free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PEER_ID = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"


class FakeSyncthing:
    def __init__(self):
        self.addresses = {}

    def set_addresses(self, device_id, addresses):
        changed = self.addresses.get(device_id) != addresses
        self.addresses[device_id] = addresses
        return changed

    def device_id(self):
        return "ME"

    def listen_port(self):
        return 22000


def make_daemon(peer_keys, **kw):
    cfg = Config(peers=[Peer(npub=peer_keys.npub, syncthing_id=PEER_ID, name="peer")], managed=False, **kw)
    st = FakeSyncthing()
    return Daemon(cfg, Keypair(os.urandom(32)), st), st


def test_nip19_vector():
    hexkey = "7e7e9c42a91bfef19fa929e5fda1b72e0ebc1a4c1141673e2794234d86addf4e"
    npub = "npub10elfcs4fr0l0r8af98jlmgdh9c8tcxjvz9qkw038js35mp4dma8qzvjptg"
    assert hex_to_npub(hexkey) == npub
    assert npub_to_hex(npub) == hexkey


def test_event_sign_verify_and_tamper():
    kp = Keypair(os.urandom(32))
    ev = kp.sign(Event(kind=1, content="hi"))
    assert ev.verify()
    ev.content = "changed"
    assert not ev.verify()


def test_keypair_persisted_0600(tmp_path):
    p = tmp_path / "id.nsec"
    a = Keypair.load_or_create(p)
    assert Keypair.load_or_create(p).pubkey_hex == a.pubkey_hex
    assert oct(p.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("addr,ok", [
    ("tcp://203.0.113.5:22000", True),
    ("tcp://[2001:db8::1]:22000", True),
    ("tcp://192.168.1.20:22000", True),
    ("tcp://127.0.0.1:22000", False),
    ("tcp://0.0.0.0:22000", False),
    ("tcp://example.com:22000", False),
    ("tcp://203.0.113.5:0", False),
    ("quic://203.0.113.5:22000", False),
])
def test_valid_address(addr, ok):
    assert valid_address(addr) is ok


def test_accepts_announcement_from_paired_peer():
    peer = Keypair(os.urandom(32))
    d, st = make_daemon(peer)
    ev = build_announcement(peer, PEER_ID, ["tcp://203.0.113.5:22000", "tcp://127.0.0.1:1"], 1200)
    assert d.handle_event(ev)
    assert st.addresses[PEER_ID] == ["tcp://203.0.113.5:22000"]


def test_rejects_stranger_wrong_device_replay_and_expired():
    peer, stranger = Keypair(os.urandom(32)), Keypair(os.urandom(32))
    d, st = make_daemon(peer)
    addrs = ["tcp://203.0.113.5:22000"]
    assert not d.handle_event(build_announcement(stranger, PEER_ID, addrs, 1200))
    assert not d.handle_event(build_announcement(peer, "ZZZZZZZ-" + PEER_ID[8:], addrs, 1200))
    assert st.addresses == {}

    new = build_announcement(peer, PEER_ID, addrs, 1200, now=2_000_000_000)
    old = build_announcement(peer, PEER_ID, ["tcp://203.0.113.9:22000"], 1200, now=1_999_999_000)
    assert d.handle_event(new, now=2_000_000_001)
    assert not d.handle_event(old, now=2_000_000_001)  # replayed older announcement
    assert st.addresses[PEER_ID] == addrs

    gone = build_announcement(peer, PEER_ID, addrs, 1200, now=1_000_000_000)
    d2, _ = make_daemon(peer)
    assert not d2.handle_event(gone, now=1_000_000_000 + 3601)  # past its expiration


def test_rejects_forged_content():
    peer = Keypair(os.urandom(32))
    d, st = make_daemon(peer)
    ev = build_announcement(peer, PEER_ID, ["tcp://203.0.113.5:22000"], 1200)
    ev.content = ev.content.replace("203.0.113.5", "203.0.113.6")
    assert not d.handle_event(ev)


def test_stun_parse():
    txid = os.urandom(12)
    ip = bytes([203, 0, 113, 7])
    xored = struct.pack("!I", int.from_bytes(ip, "big") ^ MAGIC)
    attr = struct.pack("!HH", 0x0020, 8) + b"\x00\x01" + struct.pack("!H", 0x1234) + xored
    resp = struct.pack("!HHI", 0x0101, len(attr), MAGIC) + txid + attr
    assert parse_binding_response(resp, txid) == "203.0.113.7"
    assert parse_binding_response(resp, os.urandom(12)) is None


async def test_relay_roundtrip_with_fake_relay():
    """A minimal NIP-01 relay: stores the newest event, serves it to subscribers."""
    stored: list[dict] = []
    subs: list = []

    async def handler(ws):
        subs.append(ws)
        async for raw in ws:
            msg = json.loads(raw)
            if msg[0] == "EVENT":
                stored[:] = [msg[1]]
                for s in subs:
                    await s.send(json.dumps(["EVENT", "nsync", msg[1]]))
            elif msg[0] == "REQ" and stored:
                await ws.send(json.dumps(["EVENT", msg[1], stored[0]]))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        alice, bob = Keypair(os.urandom(32)), Keypair(os.urandom(32))
        d, st = make_daemon(alice)
        d.config.relays = [url]
        received = asyncio.Queue()
        pool = RelayPool([url], [{"kinds": [KIND]}], received.put_nowait)
        pool.start()
        await asyncio.sleep(0.3)
        await pool.publish(build_announcement(alice, PEER_ID, ["tcp://203.0.113.5:22000"], 1200))
        ev = await asyncio.wait_for(received.get(), 3)
        assert d.handle_event(Event.from_dict(ev.to_dict()))
        assert st.addresses[PEER_ID] == ["tcp://203.0.113.5:22000"]
        await pool.stop()


async def test_web_page_loads_and_explains_when_syncthing_is_unreachable():
    import requests

    from nsync.web import start_web

    class Down(FakeSyncthing):
        def device_id(self):
            raise ConnectionError("connection refused")

    d = Daemon(Config(peers=[], relays=[], managed=False), Keypair(os.urandom(32)), Down())
    PORT = free_port()
    server = start_web(d, PORT)
    try:
        page = await asyncio.to_thread(requests.get, f"http://127.0.0.1:{PORT}/nsync/")
        status = await asyncio.to_thread(requests.get, f"http://127.0.0.1:{PORT}/nsync/api/status")
        assert page.status_code == 200
        assert status.status_code == 502 and "Syncthing unreachable" in status.json()["error"]
    finally:
        server.shutdown()


async def test_web_page_pair_unpair_and_guards():
    import requests

    from nsync.web import start_web

    class St(FakeSyncthing):
        added = []

        def device(self, device_id):
            return None

        def add_device(self, device_id, name):
            self.added.append(device_id)

    peer = Keypair(os.urandom(32))
    cfg = Config(peers=[], relays=[], managed=False)
    cfg.save = lambda: None  # keep the test off the real config file
    st = St()
    d = Daemon(cfg, Keypair(os.urandom(32)), st)
    PORT = free_port()
    server = start_web(d, PORT)
    base = f"http://127.0.0.1:{PORT}"
    try:
        def get(path, **kw):
            return asyncio.to_thread(requests.get, base + path, **kw)

        def post(path, body, **kw):
            return asyncio.to_thread(requests.post, base + path, json=body, **kw)

        assert (await get("/nsync/")).status_code == 200
        assert (await get("/nsync/logo.svg")).headers["Content-Type"] == "image/svg+xml"
        assert (await get("/nsync/api/status")).json()["npub"] == d.keys.npub
        assert (await get("/nsync/api/status", headers={"Host": f"evil.example:{PORT}"})).status_code == 403
        assert (await post("/nsync/api/pair", {}, headers={"Origin": "http://evil.example"})).status_code == 403
        assert (await asyncio.to_thread(requests.post, base + "/nsync/api/pair", data="x")).status_code == 415
        assert (await post("/nsync/api/pair", {"pairing": "garbage"})).status_code == 400

        r = await post("/nsync/api/pair", {"pairing": f"{peer.npub}:{PEER_ID}", "name": "phone"})
        assert r.status_code == 200 and st.added == [PEER_ID]
        assert (await get("/nsync/api/status")).json()["peers"][0]["name"] == "phone"
        assert (await post("/nsync/api/unpair", {"npub": peer.npub})).status_code == 200
        assert (await get("/nsync/api/status")).json()["peers"] == []
    finally:
        server.shutdown()


@pytest.mark.parametrize("url,ok", [
    ("wss://relay.damus.io", True),
    ("wss://nos.lol/", True),
    ("wss://relay.example.com:8443/path", True),
    ("ws://relay.damus.io", False),        # cleartext
    ("wss://127.0.0.1", False),            # IP literals: a peer must not aim this machine at the LAN
    ("wss://192.168.1.1:7777", False),
    ("wss://localhost", False),
    ("wss://user:pass@relay.example.com", False),
    ("https://relay.example.com", False),
    ("wss://relay.example.com:99999", False),
])
def test_valid_relay(url, ok):
    assert valid_relay(url) is ok


def test_relay_list_from_a_paired_peer_is_learned_and_filtered():
    peer, stranger = Keypair(os.urandom(32)), Keypair(os.urandom(32))
    d, _ = make_daemon(peer, relays=["wss://mine.example.org"])
    d.config.save = lambda: None
    lst = ["wss://a.example.com", "ws://insecure.example.com", "wss://10.0.0.1", "wss://a.example.com",
           "wss://b.example.com", "wss://c.example.com", "wss://d.example.com", "wss://e.example.com", "wss://f.example.com"]
    assert not d.handle_event(build_relay_list(stranger, lst, now=1000), now=1001)
    assert d.handle_event(build_relay_list(peer, lst, now=1000), now=1001)
    learned = d.effective_relays()
    assert learned[0] == "wss://mine.example.org"  # ours first
    assert learned[1:] == ["wss://a.example.com", "wss://b.example.com", "wss://c.example.com",
                           "wss://d.example.com", "wss://e.example.com"]  # valid, deduped, capped at five
    # an older list does not roll it back, a tampered one is refused
    assert not d.handle_event(build_relay_list(peer, ["wss://old.example.com"], now=900), now=1001)
    forged = build_relay_list(peer, ["wss://x.example.com"], now=2000)
    forged.tags = [["r", "wss://evil.example.com"]]
    assert not d.handle_event(forged, now=2001)
    assert "wss://evil.example.com" not in d.effective_relays()
    # a newer list replaces the old one
    assert d.handle_event(build_relay_list(peer, ["wss://new.example.com"], now=3000), now=3001)
    assert d.effective_relays() == ["wss://mine.example.org", "wss://new.example.com"]


async def test_a_relay_that_joins_later_gets_the_latest_announcement():
    stored = {"a": [], "b": []}

    def make(name):
        async def handler(ws):
            async for raw in ws:
                msg = json.loads(raw)
                if msg[0] == "EVENT":
                    stored[name].append(msg[1]["kind"])
        return handler

    async with websockets.serve(make("a"), "127.0.0.1", 0) as sa, websockets.serve(make("b"), "127.0.0.1", 0) as sb:
        url_a = f"ws://127.0.0.1:{sa.sockets[0].getsockname()[1]}"
        url_b = f"ws://127.0.0.1:{sb.sockets[0].getsockname()[1]}"
        kp = Keypair(os.urandom(32))
        pool = RelayPool([url_a], [{"kinds": [KIND]}], lambda e: None)
        pool.start()
        await asyncio.sleep(0.3)
        await pool.publish(build_announcement(kp, PEER_ID, ["tcp://203.0.113.5:22000"], 1200))
        await pool.publish(build_relay_list(kp, ["wss://x.example.com"]))
        await pool.set_urls([url_a, url_b])
        await asyncio.sleep(0.6)
        assert sorted(stored["b"]) == [KIND_RELAYS, KIND]  # both replaceable events reached the newcomer
        await pool.set_urls([url_b])
        assert [r.url for r in pool.relays] == [url_b]
        await pool.stop()


async def test_the_proxy_passes_the_cores_cookies_and_posts_through():
    """The core issues its CSRF cookie only to a client that has none; the proxy must not hold one for everybody."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import requests

    from nsync.web import start_web

    seen = []

    class Core(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"<html><body>hi</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if "CSRF=" not in (self.headers.get("Cookie") or ""):
                self.send_header("Set-Cookie", "CSRF=abc")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append((self.path, self.rfile.read(int(self.headers["Content-Length"])), self.headers.get("X-CSRF-Token")))
            self.send_response(204)
            self.end_headers()

    core = HTTPServer(("127.0.0.1", 0), Core)
    threading.Thread(target=core.serve_forever, daemon=True).start()
    cfg = Config(peers=[], relays=[], managed=True, gui_port=core.server_address[1])
    d = Daemon(cfg, Keypair(os.urandom(32)), FakeSyncthing())
    PORT = free_port()
    server = start_web(d, PORT)
    try:
        for _ in range(2):  # the second request must be issued a cookie too: the proxy keeps none of its own
            r = await asyncio.to_thread(requests.get, f"http://127.0.0.1:{PORT}/")
            assert r.headers.get("Set-Cookie") == "CSRF=abc"
            assert b"nsync/inject.js" in r.content
        r = await asyncio.to_thread(requests.post, f"http://127.0.0.1:{PORT}/rest/x", data=b"payload", headers={"X-CSRF-Token": "t"})
        assert r.status_code == 204 and seen == [("/rest/x", b"payload", "t")]
    finally:
        server.shutdown()
        core.shutdown()


def test_there_are_at_least_five_default_relays_and_an_untouched_old_default_is_upgraded(tmp_path, monkeypatch):
    from nsync.config import DEFAULT_RELAYS
    assert len(DEFAULT_RELAYS) >= 5 and len(set(DEFAULT_RELAYS)) == len(DEFAULT_RELAYS)
    assert all(valid_relay(u) for u in DEFAULT_RELAYS)
    monkeypatch.setenv("NSYNC_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"relays": ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net"]}))
    assert Config.load().relays == DEFAULT_RELAYS          # the old three, never edited: new defaults
    (tmp_path / "config.json").write_text(json.dumps({"relays": ["wss://mine.example.org"]}))
    assert Config.load().relays == ["wss://mine.example.org"]  # a list the user chose is left alone


@pytest.mark.parametrize("url,ok", [
    ("wss://relay.example.com", True), ("ws://127.0.0.1:7777", True), ("ws://localhost:7777", True), ("ws://192.168.1.5:7777", True),
    ("ws://relay.example.com", False), ("ws://8.8.8.8:7777", False), ("http://relay.example.com", False), ("relay.example.com", False),
    ("wss://user:pw@relay.example.com", False),
])
def test_valid_own_relay(url, ok):
    assert valid_own_relay(url) is ok


class FakePool:
    def __init__(self, urls):
        self.urls, self.sent, self.filters = list(urls), [], 0
        self.relays = []

    async def publish(self, event):
        self.sent.append(event.kind)

    async def set_urls(self, urls):
        self.urls = list(urls)

    async def set_filters(self, filters):
        self.filters += 1


async def test_relay_actions_from_the_page(monkeypatch):
    import requests

    from nsync import daemon as dmod
    from nsync.web import start_web

    async def no_public_ip():
        return None
    monkeypatch.setattr(dmod, "public_ipv4", no_public_ip)
    monkeypatch.setattr(dmod, "lan_ipv4", lambda: "192.168.1.9")
    cfg = Config(peers=[], relays=["wss://a.example.com", "wss://b.example.com"], managed=False, announce_public=False)
    cfg.save = lambda: None
    d = Daemon(cfg, Keypair(os.urandom(32)), FakeSyncthing())
    d.pool = FakePool(cfg.relays)
    PORT = free_port()
    server = start_web(d, PORT)
    base = f"http://127.0.0.1:{PORT}/nsync/api"
    post = lambda path, body: asyncio.to_thread(requests.post, base + path, json=body)
    try:
        r = await post("/relays", {"add": "wss://c.example.com"})
        assert r.status_code == 200 and cfg.relays[-1] == "wss://c.example.com"
        assert d.pool.urls[-1] == "wss://c.example.com" and KIND in d.pool.sent  # the newcomer hears from us at once
        for bad, why in (({"add": "http://x.example.com"}, "wss://"), ({"add": "wss://c.example.com"}, "already"), ({"remove": "wss://nope.example.com"}, "not one")):
            r = await post("/relays", bad)
            assert r.status_code == 400 and why in r.json()["error"], (bad, r.text)
        assert (await post("/relays", {"remove": "wss://a.example.com"})).status_code == 200
        assert (await post("/relays", {"remove": "wss://b.example.com"})).status_code == 200
        r = await post("/relays", {"remove": "wss://c.example.com"})  # the last one stays
        assert r.status_code == 400 and "at least one" in r.json()["error"]
        assert (await post("/relays", {"reset": True})).status_code == 200
        from nsync.config import DEFAULT_RELAYS
        assert cfg.relays == DEFAULT_RELAYS

        assert (await post("/settings", {"heartbeat_minutes": 3})).status_code == 400
        assert (await post("/settings", {"heartbeat_minutes": 30})).status_code == 200 and cfg.heartbeat == 1800
        assert (await post("/settings", {"announce_public": True})).status_code == 200 and cfg.announce_public is True

        before = d.pool.sent.count(KIND)
        assert (await post("/refresh", {})).status_code == 200
        assert d.pool.sent.count(KIND) == before + 1 and d.pool.filters >= 1   # published again, peers asked for again
        summary = (await asyncio.to_thread(requests.get, base + "/summary")).json()
        assert summary["republish_minutes"] == 30 and summary["announced"]
    finally:
        server.shutdown()


async def test_a_relay_reports_whether_it_accepted_the_announcement():
    async def handler(ws):
        async for raw in ws:
            m = json.loads(raw)
            if m[0] == "EVENT":
                refuse = m[1]["content"].startswith("refuse")
                await ws.send(json.dumps(["OK", m[1]["id"], not refuse, "blocked: not allowed" if refuse else ""]))

    from nsync.relay import Relay
    async with websockets.serve(handler, "127.0.0.1", 0) as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        relay = Relay(url, [{"kinds": [KIND]}], lambda e: None)
        relay.start()
        await asyncio.sleep(0.3)
        kp = Keypair(os.urandom(32))
        assert relay.status()["accepted_at"] is None
        await relay.publish(build_announcement(kp, PEER_ID, ["tcp://203.0.113.5:22000"], 1200))
        await asyncio.sleep(0.4)
        st = relay.status()
        assert st["connected"] and st["sent_at"] and st["accepted_at"] and st["error"] == ""
        await relay.publish(kp.sign(Event(kind=KIND, content="refuse", tags=[["d", "x"]])))
        await asyncio.sleep(0.4)
        assert relay.status()["error"] == "blocked: not allowed"
        await relay.stop()
