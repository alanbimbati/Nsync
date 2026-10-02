#!/bin/bash
# Builds dist/nsync_<version>_amd64.deb: a self-contained Nsync binary (no Python needed on the target)
# plus the Syncthing core it runs (.build/syncthing, from packaging/build-syncthing.sh).
#
# The binary is built in a Debian 11 container (glibc 2.31) so it runs on Ubuntu 20.04+ and Debian 11+;
# one built on a newer system refuses to start on an older one. Docker is required for that reason.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
PKG="nsync_${VERSION}_amd64"
BUILD=.build
[[ -x "$BUILD/syncthing" ]] || { echo "missing $BUILD/syncthing: run packaging/build-syncthing.sh first"; exit 1; }
command -v docker >/dev/null || { echo "docker is needed to build a binary that runs on older systems"; exit 1; }

rm -rf "$BUILD/bin" && mkdir -p "$BUILD/bin"
docker run --rm -v "$PWD:/src:ro" -v "$PWD/$BUILD/bin:/out" python:3.12-slim-bullseye bash -c '
    set -e
    apt-get update -qq && apt-get install -y -qq binutils >/dev/null
    mkdir /tmp/s && cp -r /src/nsync /src/pyproject.toml /src/packaging /tmp/s && cd /tmp/s
    python -m venv /tmp/v && /tmp/v/bin/pip install -q . pyinstaller
    /tmp/v/bin/pyinstaller --onefile --name nsync --distpath /out --workpath /tmp/w --specpath /tmp \
        --add-data /tmp/s/nsync/static:nsync/static --collect-all coincurve --noconfirm --log-level WARN packaging/entry.py
    chown '"$(id -u):$(id -g)"' /out/nsync'

rm -rf "$BUILD/$PKG" && mkdir -p "$BUILD/$PKG"/{DEBIAN,usr/bin,usr/lib/nsync,usr/share/doc/nsync,usr/lib/systemd/user,usr/share/applications,usr/share/icons/hicolor/scalable/apps}
install -m 755 "$BUILD/bin/nsync" "$BUILD/$PKG/usr/bin/nsync"
install -m 755 "$BUILD/syncthing" "$BUILD/$PKG/usr/lib/nsync/syncthing"
install -m 755 packaging/nsync-launch "$BUILD/$PKG/usr/bin/nsync-launch"
install -m 644 packaging/copyright "$BUILD/$PKG/usr/share/doc/nsync/copyright"
install -m 644 packaging/nsync.service "$BUILD/$PKG/usr/lib/systemd/user/nsync.service"
install -m 644 packaging/nsync.desktop "$BUILD/$PKG/usr/share/applications/nsync.desktop"
install -m 644 nsync/static/logo.svg "$BUILD/$PKG/usr/share/icons/hicolor/scalable/apps/nsync.svg"
cat > "$BUILD/$PKG/DEBIAN/control" <<CTL
Package: nsync
Version: $VERSION
Section: net
Priority: optional
Architecture: amd64
Depends: libc6 (>= 2.31)
Maintainer: Alan Bimbati <alan.bimbati@gmail.com>
Description: Syncthing with peer discovery over Nostr relays
 Nsync bundles Syncthing and runs it in a home of its own; it does not touch a Syncthing
 installed separately. Each device announces its address, signed with a Nostr key, on
 relays and finds its peers there. Files move directly between the devices.
CTL
cat > "$BUILD/$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
echo "Nsync installed. Open it from the application menu, or run: systemctl --user enable --now nsync"
POST
chmod 755 "$BUILD/$PKG/DEBIAN/postinst"
# xz: older dpkg (Debian 11) cannot read the zstd that newer dpkg-deb uses by default.
mkdir -p dist && dpkg-deb -Zxz --root-owner-group --build "$BUILD/$PKG" "dist/$PKG.deb" >/dev/null
mkdir -p ../releases && cp "dist/$PKG.deb" ../releases/
echo "built dist/$PKG.deb (copied to ../releases/)"
