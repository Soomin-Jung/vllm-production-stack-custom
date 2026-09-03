#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
DOCKERFILE="$REPO_ROOT/docker/Dockerfile.agentic-api"
KUSTOMIZATION_DIR="$REPO_ROOT/deploy/agentic-api"
ROUTING_CONTRACT="$KUSTOMIZATION_DIR/ROUTING_CONTRACT_KO.md"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

for script in "$SCRIPT_DIR"/*.sh; do
    bash -n "$script"
done

grep -Fq 'ARG DOCKER_REGISTRY=docker.io' "$DOCKERFILE" || fail "DOCKER_REGISTRY build arg missing"
grep -Fq 'ARG AGENTIC_API_BUILDER_IMAGE=library/rust:1.98.0-bookworm' "$DOCKERFILE" || fail "Rust builder must match rust-toolchain.toml 1.98.0"
grep -Fq 'RUSTUP_TOOLCHAIN=1.98.0' "$DOCKERFILE" || fail "installed Rust toolchain override missing"
grep -Fq "FROM \${DOCKER_REGISTRY}/\${AGENTIC_API_BUILDER_IMAGE}" "$DOCKERFILE" || fail "builder registry prefix missing"
grep -Fq "FROM \${DOCKER_REGISTRY}/\${AGENTIC_API_RUNTIME_IMAGE}" "$DOCKERFILE" || fail "runtime registry prefix missing"
grep -Fq 'COPY docker/agentic-api/agentic-api-src/' "$DOCKERFILE" || fail "source directory input missing"
grep -Fq 'COPY docker/agentic-api/cargo-config.toml' "$DOCKERFILE" || fail "Cargo proxy config missing"
grep -Fq 'cargo build --release --locked -p agentic-server' "$DOCKERFILE" || fail "locked Cargo build missing"
grep -Fq 'COPY certs/' "$DOCKERFILE" || fail "CA input missing"
grep -Fq 'COPY sources.list' "$DOCKERFILE" || fail "APT proxy input missing"
grep -Fq 'USER 10001:0' "$DOCKERFILE" || fail "non-root runtime user missing"
grep -Fq 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' "$DOCKERFILE" || fail "entrypoint missing"

if grep -Eiq 'SOURCE_LOCK|SHA256SUMS|sha256sum|cargo vendor|--frozen|agentic-api-offline' \
  "$DOCKERFILE" "$REPO_ROOT/docker/agentic-api/README_KO.md"; then
    fail "legacy hash/vendor build flow remains"
fi

if grep -Eiq '(^|[[:space:]])(git clone|curl|wget|pip install)([[:space:]]|$)' "$DOCKERFILE"; then
    fail "Dockerfile contains a direct external dependency fetch"
fi

grep -Fq 'replicas: 2' "$KUSTOMIZATION_DIR/deployment.yaml" || fail "two replicas missing"
grep -Fq 'key: DATABASE_URL' "$KUSTOMIZATION_DIR/deployment.yaml" || fail "PostgreSQL secret wiring missing"
grep -Fq 'GET /v1/responses' "$ROUTING_CONTRACT" || fail "Responses WebSocket route missing"
grep -Fq 'LMStack Router 0.1.9 normal round-robin' "$ROUTING_CONTRACT" || fail "LMStack route baseline missing"
grep -Fq 'blind replay' "$ROUTING_CONTRACT" || fail "Responses retry rule missing"

if grep -Fq 'secret.example.yaml' "$KUSTOMIZATION_DIR/kustomization.yaml"; then
    fail "example Secret must not be applied by kustomization"
fi

if command -v kubectl >/dev/null 2>&1; then
    kubectl kustomize "$KUSTOMIZATION_DIR" >/dev/null
else
    echo "WARN: kubectl not found; skipped kustomize render" >&2
fi

echo "Agentic API image and deployment contract is valid"
