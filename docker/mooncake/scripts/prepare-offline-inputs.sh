#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MOONCAKE_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)

PROFILE=${MOONCAKE_PROFILE:-0.3.12.post1}
OUTPUT_DIR=${MOONCAKE_DIR}/vendor

usage() {
  cat <<'EOF'
Usage:
  prepare-offline-inputs.sh [--profile VERSION] [--output-dir DIR]
  prepare-offline-inputs.sh DIR

Examples:
  prepare-offline-inputs.sh --profile 0.3.12.post1
  prepare-offline-inputs.sh --profile 0.3.10.post2 --output-dir /tmp/vendor

The legacy single positional argument is still accepted as OUTPUT_DIR.
EOF
}

positional_seen=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
      PROFILE=$2
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || { echo "--output-dir requires a value" >&2; exit 2; }
      OUTPUT_DIR=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ "${positional_seen}" -ne 0 ]]; then
        echo "Only one positional OUTPUT_DIR is supported" >&2
        exit 2
      fi
      OUTPUT_DIR=$1
      positional_seen=1
      shift
      ;;
  esac
done

LOCK_FILE=${MOONCAKE_LOCK_FILE:-${MOONCAKE_DIR}/locks/${PROFILE}.env}
if [[ ! -f "${LOCK_FILE}" ]]; then
  echo "Unknown Mooncake profile ${PROFILE}: ${LOCK_FILE} not found" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "${LOCK_FILE}"

BUNDLE_PATH=${OUTPUT_DIR}/${MOONCAKE_SOURCE_ARCHIVE}
CHECKSUM_FILE=${BUNDLE_PATH}.sha256

for command_name in git tar sha256sum; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done

if [[ -e "${BUNDLE_PATH}" || -e "${CHECKSUM_FILE}" ]]; then
  echo "Offline inputs already exist for profile ${PROFILE} under ${OUTPUT_DIR}" >&2
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

(
  cd "${OUTPUT_DIR}"
  sha256sum "${MOONCAKE_SOURCE_ARCHIVE}" > "$(basename "${CHECKSUM_FILE}")"
  sha256sum --check "$(basename "${CHECKSUM_FILE}")"
)

echo "Prepared Mooncake profile ${PROFILE} in ${OUTPUT_DIR}"
echo "Transfer both files:"
echo "  ${MOONCAKE_SOURCE_ARCHIVE}"
echo "  ${MOONCAKE_SOURCE_ARCHIVE}.sha256"
