#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
DOCKERFILE="$REPO_ROOT/docker/Dockerfile.agentic-api"
LOCK_FILE="$REPO_ROOT/docker/agentic-api/SOURCE_LOCK.env"
KUSTOMIZATION_DIR="$REPO_ROOT/deploy/agentic-api"
ROUTING_CONTRACT="$KUSTOMIZATION_DIR/ROUTING_CONTRACT_KO.md"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

for script in "$SCRIPT_DIR"/*.sh; do
    bash -n "$script"
done

# SOURCE_LOCK.env is generated and resolved from the repository root at runtime.
# shellcheck disable=SC1090,SC1091
source "$LOCK_FILE"

test "$AGENTIC_API_VERSION" = "0.5.0" || fail "Agentic API version is not pinned to 0.5.0"
test "$AGENTIC_API_GIT_TAG" = "v0.5.0" || fail "Agentic API tag is not v0.5.0"
test "$AGENTIC_API_COMMIT" = "032935de73d92f116ac108f24cd63d6a158aad94" || fail "unexpected source commit"

grep -Fq 'cargo build --locked --frozen --offline --release -p agentic-server' "$DOCKERFILE" || fail "offline frozen Cargo build missing"
grep -Fq 'CARGO_NET_OFFLINE=true' "$DOCKERFILE" || fail "Cargo offline mode missing"
grep -Fq 'COPY certs/' "$DOCKERFILE" || fail "operator CA input missing"
grep -Fq 'COPY sources.list' "$DOCKERFILE" || fail "internal apt source input missing"
grep -Fq 'USER 10001:0' "$DOCKERFILE" || fail "non-root runtime user missing"
grep -Fq 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' "$DOCKERFILE" || fail "entrypoint missing"

if grep -Eiq '(^|[[:space:]])(git clone|curl|wget|pip install)([[:space:]]|$)' "$DOCKERFILE"; then
    fail "Dockerfile contains a network dependency fetch"
fi
if grep -Eiq '(^|[[:space:]])(python3?|maturin|pip)([[:space:]]|$)' "$DOCKERFILE"; then
    fail "standalone runtime must not install Python tooling"
fi

grep -Fq 'replicas: 2' "$KUSTOMIZATION_DIR/deployment.yaml" || fail "two-replica stateful deployment missing"
grep -Fq 'key: DATABASE_URL' "$KUSTOMIZATION_DIR/deployment.yaml" || fail "PostgreSQL secret wiring missing"
grep -Fq 'path: /health' "$KUSTOMIZATION_DIR/deployment.yaml" || fail "liveness/startup probe missing"
grep -Fq 'path: /ready' "$KUSTOMIZATION_DIR/deployment.yaml" || fail "readiness probe missing"
grep -Fq 'type: ClusterIP' "$KUSTOMIZATION_DIR/service.yaml" || fail "internal-only Service missing"
grep -Fq 'GET /v1/responses' "$ROUTING_CONTRACT" || fail "Responses WebSocket route contract missing"
grep -Fq 'LMStack Router 0.1.9 normal round-robin' "$ROUTING_CONTRACT" || fail "LMStack Responses routing baseline missing"
grep -Fq 'blind replay' "$ROUTING_CONTRACT" || fail "Responses retry safety contract missing"

if grep -Fq 'secret.example.yaml' "$KUSTOMIZATION_DIR/kustomization.yaml"; then
    fail "example Secret must not be applied by kustomization"
fi

if command -v kubectl >/dev/null 2>&1; then
    kubectl kustomize "$KUSTOMIZATION_DIR" >/dev/null
else
    echo "WARN: kubectl not found; skipped kustomize render" >&2
fi

echo "Agentic API v0.5.0 static validation passed"
