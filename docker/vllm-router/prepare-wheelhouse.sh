#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.1.15}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEELHOUSE="${ROOT_DIR}/wheelhouse"
TARBALL="${ROOT_DIR}/vllm-router-${VERSION}-wheelhouse.tar.gz"

case "$(uname -m)" in
  x86_64)
    ARCH="x86_64"
    ;;
  aarch64|arm64)
    ARCH="aarch64"
    ;;
  *)
    ARCH="unknown"
    ;;
esac

rm -rf "${WHEELHOUSE}" "${TARBALL}"
mkdir -p "${WHEELHOUSE}"

"${PYTHON_BIN}" -m pip download \
  --dest "${WHEELHOUSE}" \
  --only-binary=:all: \
  "vllm-router==${VERSION}"

ROUTER_WHEEL="$(find "${WHEELHOUSE}" -maxdepth 1 -type f -name "vllm_router-${VERSION}-*.whl" -print -quit)"
if [[ -z "${ROUTER_WHEEL}" ]]; then
  echo "ERROR: vllm-router ${VERSION} release wheel was not downloaded" >&2
  exit 1
fi

EXPECTED_SHA256=""
case "${VERSION}:${ARCH}" in
  0.1.15:x86_64)
    EXPECTED_SHA256="2f268b001a546d7921c2e87b510869134a212f0ab2faf138b78eb554c93a2241"
    ;;
  0.1.15:aarch64)
    EXPECTED_SHA256="c30070b2f8559fc33da4b114e58d28881775585dd6f6e1ac173ea494c8fbe20e"
    ;;
esac

if [[ -n "${EXPECTED_SHA256}" ]]; then
  ACTUAL_SHA256="$(sha256sum "${ROUTER_WHEEL}" | awk '{print $1}')"
  if [[ "${ACTUAL_SHA256}" != "${EXPECTED_SHA256}" ]]; then
    echo "ERROR: vllm-router ${VERSION} wheel SHA256 mismatch" >&2
    echo "expected: ${EXPECTED_SHA256}" >&2
    echo "actual:   ${ACTUAL_SHA256}" >&2
    exit 1
  fi
else
  echo "WARNING: no pinned upstream wheel SHA256 is recorded for ${VERSION}/${ARCH}." >&2
  echo "         Verify the release artifact manually before closed-network import." >&2
fi

(
  cd "${WHEELHOUSE}"
  sha256sum ./*.whl > SHA256SUMS
)

tar -C "${ROOT_DIR}" -czf "${TARBALL}" wheelhouse

printf 'Prepared %s\n' "${TARBALL}"
printf 'Router wheel: %s\n' "$(basename "${ROUTER_WHEEL}")"
printf 'Architecture: %s\n' "${ARCH}"
