#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=${1:?usage: verify-source-lock.sh SOURCE_DIR [LOCK_FILE]}
LOCK_FILE=${2:-"$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/SOURCE_LOCK.env"}
MANIFEST_FILE="$SOURCE_DIR/SOURCE_MANIFEST.env"

test -f "$LOCK_FILE" || { echo "missing source lock: $LOCK_FILE" >&2; exit 1; }
test -f "$MANIFEST_FILE" || { echo "missing source manifest: $MANIFEST_FILE" >&2; exit 1; }
test -f "$SOURCE_DIR/Cargo.lock" || { echo "missing Cargo.lock" >&2; exit 1; }
test -f "$SOURCE_DIR/.cargo/config.toml" || { echo "missing vendored Cargo config" >&2; exit 1; }
test -d "$SOURCE_DIR/vendor" || { echo "missing Cargo vendor directory" >&2; exit 1; }

# shellcheck disable=SC1090
source "$LOCK_FILE"
LOCK_VERSION=$AGENTIC_API_VERSION
LOCK_TAG=$AGENTIC_API_GIT_TAG
LOCK_COMMIT=$AGENTIC_API_COMMIT
# shellcheck disable=SC1090
source "$MANIFEST_FILE"

test "$LOCK_VERSION" = "0.5.0" || {
    echo "unexpected locked Agentic API version: $LOCK_VERSION" >&2
    exit 1
}
test "$LOCK_TAG" = "v0.5.0" || {
    echo "unexpected locked Agentic API tag: $LOCK_TAG" >&2
    exit 1
}
test "$LOCK_COMMIT" = "032935de73d92f116ac108f24cd63d6a158aad94" || {
    echo "unexpected locked Agentic API commit: $LOCK_COMMIT" >&2
    exit 1
}
test "$AGENTIC_API_VERSION" = "$LOCK_VERSION" || {
    echo "unexpected Agentic API version: $AGENTIC_API_VERSION" >&2
    exit 1
}
test "$AGENTIC_API_GIT_TAG" = "$LOCK_TAG" || {
    echo "unexpected Agentic API tag: $AGENTIC_API_GIT_TAG" >&2
    exit 1
}
test "$AGENTIC_API_COMMIT" = "$LOCK_COMMIT" || {
    echo "unexpected Agentic API commit: $AGENTIC_API_COMMIT" >&2
    exit 1
}

ACTUAL_CARGO_LOCK_SHA256=$(sha256sum "$SOURCE_DIR/Cargo.lock" | awk '{print $1}')
test "$ACTUAL_CARGO_LOCK_SHA256" = "$CARGO_LOCK_SHA256" || {
    echo "Cargo.lock checksum mismatch" >&2
    exit 1
}

grep -Fq 'version = "0.5.0"' "$SOURCE_DIR/Cargo.toml"
grep -Fq 'directory = "vendor"' "$SOURCE_DIR/.cargo/config.toml"
