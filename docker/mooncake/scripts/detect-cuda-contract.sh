#!/usr/bin/env bash
set -euo pipefail

EXPECTED_CUDA_VERSION=${1:-auto}
OUTPUT_FILE=${2:-/opt/mooncake/CUDA_CONTRACT.env}

for command_name in python3 dirname mkdir; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done

base_cuda=$(
  python3 - <<'PY'
import torch

value = torch.version.cuda
if not value:
    raise SystemExit("torch.version.cuda is empty; a CUDA vLLM base image is required")
print(value)
PY
)

cuda_major=${base_cuda%%.*}
cuda_rest=${base_cuda#*.}
cuda_minor=${cuda_rest%%.*}

if [[ -z "${cuda_major}" || -z "${cuda_minor}" ]]; then
  echo "Unable to parse CUDA version from torch.version.cuda=${base_cuda}" >&2
  exit 1
fi

case "${cuda_major}" in
  12)
    mooncake_cu13_build=0
    mooncake_package=mooncake-transfer-engine
    ;;
  13)
    mooncake_cu13_build=1
    mooncake_package=mooncake-transfer-engine-cuda13
    ;;
  *)
    echo "Unsupported CUDA major ${cuda_major}; this build contract supports CUDA 12/13 only" >&2
    exit 1
    ;;
esac

normalized="${cuda_major}.${cuda_minor}"
if [[ "${EXPECTED_CUDA_VERSION}" != "auto" && "${EXPECTED_CUDA_VERSION}" != "${normalized}" ]]; then
  echo "CUDA contract mismatch: base image reports ${normalized}, expected ${EXPECTED_CUDA_VERSION}" >&2
  exit 1
fi

cuda_apt_suffix="${cuda_major}-${cuda_minor}"

mkdir -p "$(dirname "${OUTPUT_FILE}")"
cat > "${OUTPUT_FILE}" <<EOF
BASE_CUDA_VERSION=${base_cuda}
CUDA_MAJOR=${cuda_major}
CUDA_MINOR=${cuda_minor}
CUDA_APT_SUFFIX=${cuda_apt_suffix}
MOONCAKE_CU13_BUILD=${mooncake_cu13_build}
MOONCAKE_PACKAGE_NAME=${mooncake_package}
EOF

echo "Detected CUDA contract from vLLM base:"
cat "${OUTPUT_FILE}"
