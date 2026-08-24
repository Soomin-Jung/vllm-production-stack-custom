#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=${1:-/opt/mooncake/Mooncake}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LOCK_FILE=${MOONCAKE_LOCK_FILE:-${SCRIPT_DIR}/../SOURCE_LOCK.env}

if [[ ! -f "${LOCK_FILE}" ]]; then
  LOCK_FILE=/opt/mooncake/SOURCE_LOCK.env
fi

# shellcheck disable=SC1090
. "${LOCK_FILE}"

manifest="${SOURCE_DIR}/SOURCE_MANIFEST.env"
if [[ ! -f "${manifest}" ]]; then
  echo "Missing ${manifest}; prepare the source with prepare-offline-inputs.sh" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "${manifest}"

assert_equal() {
  local name=$1
  local actual=$2
  local expected=$3
  if [[ "${actual}" != "${expected}" ]]; then
    echo "${name} mismatch: expected ${expected}, got ${actual}" >&2
    exit 1
  fi
}

assert_equal MOONCAKE_VERSION "${SOURCE_MOONCAKE_VERSION:-}" "${MOONCAKE_VERSION}"
assert_equal MOONCAKE_COMMIT "${SOURCE_MOONCAKE_COMMIT:-}" "${MOONCAKE_COMMIT}"
assert_equal PYBIND11_COMMIT "${SOURCE_PYBIND11_COMMIT:-}" "${PYBIND11_COMMIT}"
assert_equal YALANTINGLIBS_COMMIT "${SOURCE_YALANTINGLIBS_COMMIT:-}" "${YALANTINGLIBS_COMMIT}"

test -f "${SOURCE_DIR}/extern/pybind11/include/pybind11/pybind11.h"
test -f "${SOURCE_DIR}/extern/yalantinglibs/CMakeLists.txt"
grep -Fq "version = \"${MOONCAKE_VERSION}\"" \
  "${SOURCE_DIR}/mooncake-wheel/pyproject.toml"

echo "Verified Mooncake ${MOONCAKE_VERSION} source lock"

