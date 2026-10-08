#!/usr/bin/env bash
# Build Kronk's derived Music Assistant image from a clone/fork checkout.
#   ma/build.sh <checkout-dir> <N>     → kronk/music-assistant:2.11.0b2-kronk.N
# The checkout must be clean (the image label records its commit). Swap-in is
# a NAMED MA restart — see docs/plans/MA_LOCAL_PANDORA_FEATURES_PLAN.md §1.
set -euo pipefail
SRC="${1:?checkout dir}"; N="${2:?build number}"
BASE_TAG="${BASE_TAG:-2.11.0b2}"
REV=$(git -C "$SRC" rev-parse --short HEAD)
[ -z "$(git -C "$SRC" status --porcelain)" ] || { echo "checkout is dirty — commit first" >&2; exit 1; }
TAG="kronk/music-assistant:${BASE_TAG}-kronk.${N}"
docker build -f "$(dirname "$0")/Dockerfile" --build-arg BASE="ghcr.io/music-assistant/server:${BASE_TAG}" --build-arg FORK_REV="$REV" -t "$TAG" "$SRC"
echo "built $TAG from $REV"
docker image inspect "$TAG" --format '{{index .Config.Labels "io.kronk.ma.patch"}} @ {{index .Config.Labels "io.kronk.ma.fork_rev"}}'
