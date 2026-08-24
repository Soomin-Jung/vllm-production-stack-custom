#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MOONCAKE_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)

# shellcheck disable=SC1091
. "${MOONCAKE_DIR}/SOURCE_LOCK.env"

OUTPUT_DIR=${1:-${MOONCAKE_DIR}/vendor}
BUNDLE_PATH=${OUTPUT_DIR}/${MOONCAKE_SOURCE_ARCHIVE}
WHEELHOUSE=${OUTPUT_DIR}/wheelhouse
CHECKSUMS=${OUTPUT_DIR}/SHA256SUMS

for command_name in git python3 tar sha256sum; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done

if [[ -e "${BUNDLE_PATH}" || -e "${WHEELHOUSE}" || -e "${CHECKSUMS}" ]]; then
  echo "Offline inputs already exist under ${OUTPUT_DIR}; move them before regenerating" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"
work_dir=$(mktemp -d)
trap 'rm -rf -- "${work_dir}"' EXIT

git clone --filter=blob:none https://github.com/kvcache-ai/Mooncake.git \
  "${work_dir}/Mooncake"
git -C "${work_dir}/Mooncake" checkout --detach "${MOONCAKE_COMMIT}"
git -C "${work_dir}/Mooncake" submodule sync --recursive
git -C "${work_dir}/Mooncake" submodule update --init --recursive

actual_mooncake=$(git -C "${work_dir}/Mooncake" rev-parse HEAD)
actual_pybind11=$(git -C "${work_dir}/Mooncake/extern/pybind11" rev-parse HEAD)
actual_yalantinglibs=$(git -C "${work_dir}/Mooncake/extern/yalantinglibs" rev-parse HEAD)

[[ "${actual_mooncake}" == "${MOONCAKE_COMMIT}" ]]
[[ "${actual_pybind11}" == "${PYBIND11_COMMIT}" ]]
[[ "${actual_yalantinglibs}" == "${YALANTINGLIBS_COMMIT}" ]]

cat > "${work_dir}/Mooncake/SOURCE_MANIFEST.env" <<EOF
SOURCE_MOONCAKE_VERSION=${MOONCAKE_VERSION}
SOURCE_MOONCAKE_COMMIT=${actual_mooncake}
SOURCE_PYBIND11_COMMIT=${actual_pybind11}
SOURCE_YALANTINGLIBS_COMMIT=${actual_yalantinglibs}
EOF

tar \
  --sort=name \
  --mtime='UTC 1970-01-01' \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  --exclude-vcs \
  -C "${work_dir}" \
  -czf "${BUNDLE_PATH}" \
  Mooncake

mkdir -p "${WHEELHOUSE}"
python3 -m pip download \
  --only-binary=:all: \
  --destination "${WHEELHOUSE}" \
  --requirement "${MOONCAKE_DIR}/requirements-build.txt"

(
  cd "${OUTPUT_DIR}"
  sha256sum "${MOONCAKE_SOURCE_ARCHIVE}" wheelhouse/*.whl > SHA256SUMS
  sha256sum --check SHA256SUMS
)

echo "Prepared offline inputs in ${OUTPUT_DIR}"
echo "Transfer ${MOONCAKE_SOURCE_ARCHIVE}, wheelhouse/, and SHA256SUMS together"

