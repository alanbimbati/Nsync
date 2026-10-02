# Nsync

Syncthing with peer discovery over Nostr relays. It is a standalone app: it bundles the Syncthing
core, runs it in a home of its own (`~/.config/nsync/syncthing`, so a Syncthing you already run is
untouched) and serves Syncthing's interface, with an Nsync panel on top. Each device has a Nostr
keypair; the relays only carry a signed "my address is X" note. Files move peer to peer.

## Use

Install `nsync_*_amd64.deb` (see `../releases`) and open **Nsync** from the menu: it starts and
shows http://127.0.0.1:8385. The Nsync button (bottom right) opens the panel: your pairing QR and
string, the paired devices and the relays. From a terminal:

    nsync show                              # prints <npub>:<SYNCTHING-DEVICE-ID>
    nsync pair <npub>:<ID> --name laptop    # on the other device, and vice versa
    nsync run                               # or: systemctl --user enable --now nsync

From a checkout: `./packaging/build-syncthing.sh` builds the bundled core (needs Go), then
`pip install -e .`. `"managed": false` in `~/.config/nsync/config.json` drives a Syncthing you
already run instead (its address and API key are then read from your own configuration).

## Protocol

One kind 30078 (NIP-78) event per device, `d` = `nsync/v1`, plaintext JSON
`{"v":1,"device_id":...,"addresses":["tcp://IP:PORT"]}`, with a NIP-40
`expiration` of three heartbeats. A receiver accepts it only if the author is
a paired npub, the signature is valid, the device id matches the one paired,
it is newer than the last seen, and the addresses are literal routable IPs.
Nostr never authenticates the connection itself: Syncthing's TLS still checks
the device id.

The daemon asks STUN for the public IPv4 every `poll_interval` seconds and
republishes on change, and every `heartbeat` seconds regardless. The port is
Syncthing's listen port (override with `announce_port`), so it assumes a
forwarded or UPnP-mapped port. Behind CGNAT this does not help; Syncthing's
own relays remain the fallback (not disabled).

`nsync` owns the address list of paired devices in Syncthing (`dynamic` is
kept last).

## Not done yet

Hole-punching via Nostr signalling, IPv6 announcement, optional NIP-44.

## Android

The Android app lives in `../nsync-android` (a fork of Syncthing-Fork with Nsync built in). It speaks
the protocol in `PROTOCOL.md`. An earlier companion app is kept in `../archive/companion-android`.
