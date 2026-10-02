#!/bin/sh
# Writes SHA256SUMS for the installers next to this script.
cd "$(dirname "$0")" && sha256sum *.deb *.apk > SHA256SUMS && cat SHA256SUMS
