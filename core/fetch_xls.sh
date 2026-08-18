#!/usr/bin/env bash
# Put the XLS release and the amd64 rootfs where core/codegen.sh expects them.
#
#   source core/fetch_xls.sh   (or: bash core/fetch_xls.sh WORKROOT XLS_TAG IMG)
#
# Both board scripts run codegen in an amd64 container -- XLS ships linux-x64 only -- against an
# unpacked release under a work root outside the repo. Neither of them can assume the release is
# there: the work root is /tmp, and macOS cleans /tmp. Before this file, boards/basys3 downloaded
# it and boards/tiliqua did not, so the first Tiliqua build after a reboot died inside the
# container with
#
#   /w/codegen.sh: line 34: /w/xls-.../ir_converter_main: No such file or directory
#
# which reads as a broken image and is actually a missing 1 GB download (issue #33).
#
# Idempotent and quiet when there is nothing to do, so it is cheap to call on every build.
set -euo pipefail

WORKROOT="${1:-${WORKROOT:-/tmp/xls-synth-work}}"
XLS_TAG="${2:-${XLS_TAG:-v0.0.0-10214-gcf49d0e31}}"
UBUNTU_IMG="${3:-${UBUNTU_IMG:-xls-ubuntu:24.04}}"
XLS_DIR="$WORKROOT/xls-$XLS_TAG-linux-x64"

mkdir -p "$WORKROOT"

if [ ! -x "$XLS_DIR/codegen_main" ]; then
  echo "==> downloading XLS $XLS_TAG (~1 GB, once per work root)"
  curl -fsSL -o "$WORKROOT/xls.tar.gz" \
    "https://github.com/google/xls/releases/download/$XLS_TAG/xls-$XLS_TAG-linux-x64.tar.gz"
  tar xzf "$WORKROOT/xls.tar.gz" -C "$WORKROOT"
  rm -f "$WORKROOT/xls.tar.gz"
  [ -x "$XLS_DIR/codegen_main" ] || { echo "XLS unpacked but $XLS_DIR/codegen_main is missing" >&2; exit 1; }
fi

if ! docker image inspect "$UBUNTU_IMG" >/dev/null 2>&1; then
  echo "==> importing ubuntu-base rootfs as $UBUNTU_IMG"
  curl -fsSL -o "$WORKROOT/ubuntu-base.tar.gz" \
    "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz"
  docker import --platform linux/amd64 "$WORKROOT/ubuntu-base.tar.gz" "$UBUNTU_IMG" >/dev/null
  rm -f "$WORKROOT/ubuntu-base.tar.gz"
fi
