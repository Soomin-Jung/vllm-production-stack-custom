#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
AGENTIC_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_DIR=${1:-"$AGENTIC_DIR/agentic-api-src"}
AGENTIC_API_TAG=${AGENTIC_API_TAG:-v0.5.0}

if [ -e "$SOURCE_DIR" ]; then
    echo "source directory already exists: $SOURCE_DIR" >&2
    exit 1
fi

git clone \
    --branch "$AGENTIC_API_TAG" \
    --depth 1 \
    https://github.com/vllm-project/agentic-api.git \
    "$SOURCE_DIR"

rm -rf "$SOURCE_DIR/.git"
echo "prepared Agentic API source: $SOURCE_DIR ($AGENTIC_API_TAG)"
