#!/bin/bash
# Builds the Syncthing core that Nsync bundles, from the source the Android app also uses
# (the submodule in ../nsync-android). Needs Go. Output: .build/syncthing
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$PWD/../nsync-android/syncthing/src/github.com/syncthing/syncthing"
# Static (no cgo): the core then needs no particular C library on the target.
(cd "$SRC" && CGO_ENABLED=0 go run build.go -no-upgrade -goos linux -goarch amd64 build syncthing)
mkdir -p .build && cp "$SRC/syncthing" .build/syncthing
echo "built .build/syncthing ($(.build/syncthing --version | cut -d' ' -f1-2))"
