# Nsync protocol v1

The contract between implementations (Linux daemon, Android app). Test vector:
`spec/announcement.json` (both sides must verify that signature and compute
that id).

## Announcement

A Nostr event (NIP-01), kind **30078**, tags `["d","nsync/v1"]` and
`["expiration", <created_at + 3*heartbeat>]`, content JSON:

    {"v":1,"device_id":"<SYNCTHING-DEVICE-ID>","addresses":["tcp://IP:PORT", ...]}

Addresses are literal IPv4/IPv6, LAN and public. One event per device: the
newest replaces the older one on the relay.

## Publishing

Publish when the address set changes, and at least once per heartbeat.
Android: on network change plus a periodic worker (default 30 min, min 15).

## Receiving

Subscribe (or one-shot REQ, closing on EOSE) with
`{"kinds":[30078],"#d":["nsync/v1"],"authors":[<paired pubkeys>]}`.
Accept an event only if ALL hold:

1. author is a paired pubkey; kind and `d` tag match;
2. the signature and id verify;
3. `expiration` is not in the past;
4. `created_at` is newer than the last accepted from that author;
5. `device_id` equals the Syncthing id recorded at pairing;
6. each address is `tcp://<literal IP>:<1-65535>`, not loopback,
   unspecified, multicast or link-local (at most 8 are kept).

Then set the paired device's addresses in Syncthing to the accepted list plus
`dynamic` last.

## Pairing string

`<npub>:<SYNCTHING-DEVICE-ID>`, exchanged out of band (QR or paste), both ways.

## Relay list

Besides its announcement, a device publishes a NIP-65 relay list: kind **10002**, empty content, one
`["r", "wss://..."]` tag per relay it uses (its own, not the ones learned from peers). It is published
whenever the announcement is. Test vector: `relay_list_event` in `spec/announcement.json`.

A receiver reads the lists of its paired peers (same subscription: `{"kinds":[10002],"authors":[...]}`)
and treats the relays in them as additional relays, both to publish to and to read from. Rules:

1. author is a paired pubkey, kind is 10002, the signature and id verify;
2. `created_at` is newer than the last list accepted from that author;
3. each URL is `wss://` with a hostname containing a dot, no credentials, not an IP literal, at most
   200 characters; at most 5 per peer are kept, and at most 12 relays are used in total (own first);
4. the learned relays are kept, so a restart still reaches them, and dropped when the peer is unpaired.

This means two devices find each other as long as they still share one relay with the last list the
other published; changing the relay setting on one device does not strand it.

## Privacy

Announcements are plaintext: a relay, or anyone reading it, sees the addresses (LAN and public IP) and
the times a device is online, under a device-specific npub that is not the user's own Nostr identity.
This is no more than Syncthing's global discovery already publishes by default for any device ID.
Encrypting announcements per paired peer (NIP-44) is possible later as a new version.
