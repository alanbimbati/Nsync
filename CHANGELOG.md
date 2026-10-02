# Changelog

## 0.2.3 (Linux) / first Android build

- Linux: a standalone app. It bundles the Syncthing core (v2.1.6, static), runs it in its own home and serves
  Syncthing's interface with an Nsync panel (pairing QR, paired devices, relays).
- NIP-65 relay lists: devices publish the relays they use and read their peers', so changing the relay
  setting on one device no longer strands it.
- Option `announce_public: false` to announce only the LAN address.
- The `.deb` builds in a Debian 11 container and runs on Ubuntu 20.04+ / Debian 11+; the menu entry now
  reports a failed start instead of doing nothing.
- Fix: the web page lost Syncthing's CSRF cookie, which left its interface empty.
- Fix: two instances on one machine no longer pick the same ports.
- Android: a fork of Syncthing-Fork with Nsync built in; the device QR carries the Nsync pairing code.

## 0.1.0

- First version: a Linux daemon announcing a device's address on Nostr relays and applying its peers' to Syncthing.
