#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
AGENTIC_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
# SOURCE_LOCK.env is resolved relative to this script at runtime.
# shellcheck disable=SC1091
source "$AGENTIC_DIR/SOURCE_LOCK.env"

OUTPUT_DIR=${1:-"$AGENTIC_DIR/vendor"}
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd -- "$OUTPUT_DIR" && pwd)
ARCHIVE_PATH="$OUTPUT_DIR/$AGENTIC_API_SOURCE_ARCHIVE"
CHECKSUM_PATH="$OUTPUT_DIR/SHA256SUMS"

for command_name in cargo git gzip rustc sha256sum tar; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "required command is missing: $command_name" >&2
        exit 1
    }
done

case "$(rustc --version)" in
    "rustc $RUST_VERSION "*) ;;
    *)
        echo "rustc $RUST_VERSION is required; found: $(rustc --version)" >&2
        exit 1
        ;;
esac

TASK_TEMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TASK_TEMP_DIR"' EXIT

CHECKOUT_DIR="$TASK_TEMP_DIR/checkout"
BUNDLE_PARENT="$TASK_TEMP_DIR/bundle"
BUNDLE_ROOT="$BUNDLE_PARENT/agentic-api"
mkdir -p "$CHECKOUT_DIR" "$BUNDLE_PARENT"

git -C "$CHECKOUT_DIR" init --quiet
git -C "$CHECKOUT_DIR" remote add origin "$AGENTIC_API_REPOSITORY"
git -C "$CHECKOUT_DIR" fetch --quiet --depth=1 origin "$AGENTIC_API_COMMIT"
git -C "$CHECKOUT_DIR" checkout --quiet --detach FETCH_HEAD

ACTUAL_COMMIT=$(git -C "$CHECKOUT_DIR" rev-parse HEAD)
test "$ACTUAL_COMMIT" = "$AGENTIC_API_COMMIT" || {
    echo "source commit mismatch: expected $AGENTIC_API_COMMIT, got $ACTUAL_COMMIT" >&2
    exit 1
}
SOURCE_DATE_EPOCH=$(git -C "$CHECKOUT_DIR" show -s --format=%ct HEAD)

git -C "$CHECKOUT_DIR" archive --format=tar --prefix=agentic-api/ HEAD | tar -xf - -C "$BUNDLE_PARENT"
mkdir -p "$BUNDLE_ROOT/.cargo"

(
    cd "$BUNDLE_ROOT"
    cargo vendor --locked --versioned-dirs vendor > .cargo/config.toml
)

CARGO_LOCK_SHA256=$(sha256sum "$BUNDLE_ROOT/Cargo.lock" | awk '{print $1}')
printf '%s\n' \
    "AGENTIC_API_VERSION=$AGENTIC_API_VERSION" \
    "AGENTIC_API_GIT_TAG=$AGENTIC_API_GIT_TAG" \
    "AGENTIC_API_COMMIT=$AGENTIC_API_COMMIT" \
    "CARGO_LOCK_SHA256=$CARGO_LOCK_SHA256" \
    > "$BUNDLE_ROOT/SOURCE_MANIFEST.env"

rm -f -- "$ARCHIVE_PATH" "$CHECKSUM_PATH"
(
    cd "$BUNDLE_PARENT"
    tar \
        --sort=name \
        --mtime="@$SOURCE_DATE_EPOCH" \
        --owner=0 \
        --group=0 \
        --numeric-owner \
        -cf - agentic-api | gzip -n > "$ARCHIVE_PATH"
)
(
    cd "$OUTPUT_DIR"
    sha256sum "$AGENTIC_API_SOURCE_ARCHIVE" > SHA256SUMS
)

echo "prepared: $ARCHIVE_PATH"
echo "checksum: $CHECKSUM_PATH"
