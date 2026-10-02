"""nsync command line: show / pair / unpair / peers / run."""

import argparse
import asyncio
import logging
import sys

from .config import Config, Peer, home
from .core import Core, ensure_settings
from .daemon import Daemon
from .nostr import Keypair
from .pairing import parse_pairing
from .syncthing import Syncthing, find_api_key


def _syncthing(cfg: Config, recheck: bool = False) -> Syncthing:
    if cfg.managed:
        ensure_settings(cfg, recheck)
        return Syncthing(cfg.syncthing_url, cfg.syncthing_api_key)
    key = cfg.syncthing_api_key or find_api_key()
    if not key:
        sys.exit("Syncthing API key not found: set syncthing_api_key in " + str(cfg.path))
    return Syncthing(cfg.syncthing_url, key)


def _keys() -> Keypair:
    return Keypair.load_or_create(home() / "identity.nsec")


def cmd_show(args, cfg: Config) -> None:
    # One string to paste on the other device: both halves of the identity.
    device_id = Core(cfg).device_id() if cfg.managed else _syncthing(cfg).device_id()
    print(f"{_keys().npub}:{device_id}")


def cmd_pair(args, cfg: Config) -> None:
    try:
        npub, device_id = parse_pairing(args.peer)
    except ValueError as exc:
        sys.exit(f"{exc} (as printed by `nsync show`)")
    cfg.peers = [p for p in cfg.peers if p.npub != npub]
    cfg.peers.append(Peer(npub=npub, syncthing_id=device_id, name=args.name or ""))
    cfg.save()
    try:
        st = _syncthing(cfg)
        if st.device(device_id) is None:
            st.add_device(device_id, args.name or device_id[:7])
            print("added the device to Syncthing")
    except Exception:
        # Not running: a managed core gets the device from the peer list when it next starts.
        if not cfg.managed:
            raise
    print(f"paired with {args.name or npub[:16]}; restart `nsync run` to pick it up")


def cmd_unpair(args, cfg: Config) -> None:
    before = len(cfg.peers)
    cfg.peers = [p for p in cfg.peers if args.who not in (p.npub, p.name, p.syncthing_id)]
    cfg.save()
    print("removed" if len(cfg.peers) < before else "no such peer")


def cmd_peers(args, cfg: Config) -> None:
    for p in cfg.peers:
        print(f"{p.name or '-':<16} {p.npub}  {p.syncthing_id}")


def cmd_run(args, cfg: Config) -> None:
    if cfg.managed:
        st = _syncthing(cfg, recheck=True)
    else:
        key = cfg.syncthing_api_key or find_api_key()
        if not key:
            # Keep going: the web page can then say what is wrong, which a silent exit cannot.
            logging.getLogger("nsync").warning("Syncthing API key not found: is Syncthing installed and running?")
        st = Syncthing(cfg.syncthing_url, key or "")
    try:
        asyncio.run(Daemon(cfg, _keys(), st).run())
    except KeyboardInterrupt:
        pass


def cmd_open(args, cfg: Config) -> None:
    """Start the daemon if it is not running, then open its page. Used by the menu entry."""
    import shutil
    import socket
    import subprocess
    import time
    import webbrowser

    port = cfg.web_port or 8385

    def up() -> bool:
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0

    if not up():
        started = False
        if shutil.which("systemctl"):
            # A unit installed after the user manager started is unknown to it until reloaded.
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            started = subprocess.run(["systemctl", "--user", "start", "nsync.service"], capture_output=True).returncode == 0
        if not started:
            home().mkdir(parents=True, exist_ok=True)
            log = open(home() / "nsync.log", "a")
            subprocess.Popen([shutil.which("nsync") or sys.argv[0], "run"], stdout=log, stderr=log, start_new_session=True)
        for _ in range(20):
            if up():
                break
            time.sleep(0.5)
    if up():
        webbrowser.open(f"http://127.0.0.1:{port}")
        return
    msg = f"Nsync did not start. See {home() / 'nsync.log'} or: journalctl --user -u nsync"
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "Nsync", msg])
    sys.exit(msg)


def main() -> None:
    parser = argparse.ArgumentParser(prog="nsync", description="Syncthing peer discovery over Nostr relays")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print this device's pairing string").set_defaults(fn=cmd_show)
    p = sub.add_parser("pair", help="trust a peer, given its pairing string")
    p.add_argument("peer")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_pair)
    p = sub.add_parser("unpair", help="forget a peer (by npub, name or device id)")
    p.add_argument("who")
    p.set_defaults(fn=cmd_unpair)
    sub.add_parser("peers", help="list paired peers").set_defaults(fn=cmd_peers)
    sub.add_parser("run", help="run the daemon").set_defaults(fn=cmd_run)
    sub.add_parser("open", help="start the daemon if needed and open its page").set_defaults(fn=cmd_open)
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args.fn(args, Config.load())
