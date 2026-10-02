# Publishing Nsync

Everything is prepared; publishing is yours to do, because it signs with your Nostr identity. **Never put your
personal `nsec` in a chat, a file, an environment variable on a shared machine, or a repository.** A Nostr key
cannot be revoked or rotated: if it leaks, it is gone for good.

## Keys

- **Use a remote signer, not a pasted key.** Keep the key in a signer app (Amber on Android, nsec.app, or any
  NIP-46 bunker) and let `ngit` and `zsp` ask it to sign. The CLI never sees the key; you approve each signature.
- **Consider a project key.** A separate key just for the Nsync repository and the Zapstore listing means a leak
  does not touch your personal identity. Announce it once from your profile ("Nsync releases are signed by npub…").
- **The Android signing key is a different secret**: `~/.local/share/nsync-signing/` (keystore and `signing.env`).
  Back both up somewhere safe (a password manager). Lose them and you can never publish an update that installs
  over the old app. Never commit them.

## 1. Commit

No git identity is configured here, and authorship becomes public history, so set your own first:

    cd ~/Coding/Nsync            && git config user.name "…" && git config user.email "…" && git commit -m "Nsync: Syncthing with peer discovery over Nostr relays"
    cd nsync-android             && git config user.name "…" && git config user.email "…" && git commit -m "Add Nsync: discovery over Nostr relays, QR pairing, branding"

(Both repositories already have everything staged. `nsync-android` is on the `nsync` branch, on top of
Syncthing-Fork's history.)

## 2. Announce the repositories on ngit

Install ngit (https://ngit.dev), then in each repository run `ngit init` and log in with the remote signer when
asked. Check `ngit --help` for the exact flags of your version. Use the repository identifiers you like
(for example `nsync` and `nsync-android`) and push with `git push -u nostr main` (or the `nsync` branch).

Then put the clone URL in `nsync-android/zapstore.yaml` (`repository:`) and in the forum post.

## 3. Release on Zapstore

    cd nsync-android && ./release-nsync.sh      # builds and signs ../releases/Nsync-release.apk, writes SHA256SUMS
    zsp publish                                 # reads zapstore.yaml; sign through the remote signer when asked

Check the field names in `zapstore.yaml` against your `zsp` version (`zsp --help`). The Android application id is
`app.nsync`; it is permanent on Zapstore.

## 4. The Syncthing forum

`docs/forum-post.md` is a draft reply for https://forum.syncthing.net/t/nostr-for-peer-discovery-instead-of-dht/26616.
Post it from your own account: replace `[REPO URL]`, upload the screenshots from `assets/screenshots/` in the
editor, and decide whether to keep the closing line about AI assistance.

## Release checklist

1. `cd nsync-linux && .venv/bin/pytest` (all green).
2. `./packaging/build-syncthing.sh && ./packaging/build-deb.sh` (needs Go and Docker).
3. `cd nsync-android && ./release-nsync.sh`.
4. Install both on a clean machine; pair two devices.
5. Update `CHANGELOG.md`, bump versions (`nsync-linux/pyproject.toml`, `nsync-android/gradle/libs.versions.toml`).
