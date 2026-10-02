<!-- Draft reply for https://forum.syncthing.net/t/nostr-for-peer-discovery-instead-of-dht/26616
     To be posted from your own account. Upload the screenshots from assets/screenshots/
     with the editor's upload button (Discourse hosts them) and keep their order. Remove this comment. -->

I read this thread with interest and ended up building a proof of concept: **Nsync**, Syncthing with peer discovery over Nostr relays. It is a standalone app (Linux, plus an Android fork of Syncthing-Fork) that bundles the unmodified Syncthing core and adds the Nostr layer beside it. I am not asking Syncthing to change; this is a third-party experiment that happens to answer some of the questions raised here.

![Syncthing's interface with an Nsync entry in the top bar](upload://linux-gui.png)

**How it deals with the concerns in the thread**

- **First contact (@bt90, @mraneri).** Nsync does not try to find a device from its device ID alone. Pairing exchanges the device ID *and* a Nostr public key in one string (shown as a QR code), so the bootstrap problem goes away. The price is that both sides need Nsync, and it is no help when all you know is a device ID.
- **Which relays (@mraneri, @roseen).** It ships a few default relays, the user can edit the list, and paired devices publish their relay list (NIP-65) so changing relays on one device does not strand it, as long as one relay is shared with the last list the other published. Mobile does a short one-shot fetch every few minutes rather than keeping sockets open.
- **"Announce to all and query them all" (@calmh).** Yes, that is what it does, with a handful of relays; it is cheap at that scale.
- **Trust.** Announcements are signed and expire (NIP-40); a peer only accepts one from the paired key, with the paired device ID, and Syncthing's TLS still authenticates the connection itself. A relay can drop or delay events, not forge them. It *can* read them: they are plaintext, so a relay sees the device's LAN and public IP and when it is online, under a key that exists only for that device. As far as I can tell that is no more than the global discovery servers already see for any device ID. I left encryption out for now; there is an option to announce the LAN address only.

![The Nsync panel: pairing QR code, paired device and relays](upload://linux-panel.png)

![The Android app: Nsync pairing QR code in the device ID dialog, and the Nsync settings](upload://android-device-qr.png)
![](upload://android-settings.png)
![](upload://android-relays.png)

**Status.** Early. Two instances on one machine, with Syncthing's own discovery turned off, found each other through a relay and connected. The Android app runs on an emulator (QR pairing, settings); it has not been through real-world use yet. It does not do NAT traversal or hole punching.

The protocol is short and written down in `PROTOCOL.md`; I would value feedback on it, especially from the maintainers' side. Code and builds: https://github.com/alanbimbati/Nsync (Linux app, protocol, installers) and https://github.com/alanbimbati/nsync-android (Android), MPL-2.0.

<!-- Optional, your call: a line such as "Built with help from an AI coding assistant (Claude Code)." Some
     communities expect that to be disclosed. -->
