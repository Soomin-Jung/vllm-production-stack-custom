#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
DOCKERFILE=${ROOT_DIR}/docker/Dockerfile.vllm-mooncake
MOONCAKE_DIR=${ROOT_DIR}/docker/mooncake

bash -n "${MOONCAKE_DIR}/scripts/prepare-offline-inputs.sh"
bash -n "${MOONCAKE_DIR}/scripts/verify-source-lock.sh"
python3 -c \
  'from pathlib import Path; path=Path(__import__("sys").argv[1]); compile(path.read_text(), str(path), "exec")' \
  "${MOONCAKE_DIR}/scripts/verify-install.py"

# shellcheck disable=SC1091
. "${MOONCAKE_DIR}/SOURCE_LOCK.env"

grep -Fq -- '-DUSE_CUDA=ON' "${DOCKERFILE}"
grep -Fq -- '-DUSE_MNNVL=ON' "${DOCKERFILE}"
grep -Fq -- '-DUSE_INTRA_NVLINK=ON' "${DOCKERFILE}"
grep -Fq -- '-DWITH_STORE=OFF' "${DOCKERFILE}"
grep -Fq -- '-DUSE_ETCD=OFF' "${DOCKERFILE}"
grep -Fq -- 'ai.mooncake.transport.nvlink="true"' "${DOCKERFILE}"
grep -Fq -- 'ai.mooncake.transport.nvlink_intra="true"' "${DOCKERFILE}"
grep -Fq -- "${MOONCAKE_VERSION}" "${DOCKERFILE}"
grep -Fq -- "${MOONCAKE_COMMIT}" "${DOCKERFILE}"
grep -Fq -- 'COPY certs/ /opt/certs/' "${DOCKERFILE}"
grep -Fq -- 'COPY pip.conf /etc/pip.conf' "${DOCKERFILE}"
grep -Fq -- 'COPY sources.list /etc/apt/sources.list' "${DOCKERFILE}"
grep -Fq -- 'update-ca-certificates' "${DOCKERFILE}"
grep -Fq -- 'ARG VLLM_BASE_IMAGE=vllm/vllm-openai:v0.26.0-cu129' "${DOCKERFILE}"
grep -Fq -- 'rm -fv /etc/apt/sources.list.d/*.list' "${DOCKERFILE}"
# The Dockerfile expression must remain literal here; expansion would inspect
# this validation shell's environment instead of the file contents.
# shellcheck disable=SC2016
grep -Fq -- 'LIBRARY_PATH=/usr/local/cuda/lib64/stubs:${LIBRARY_PATH}' "${DOCKERFILE}"
grep -Fq -- 'mooncake-offline_*.tar.gz' "${DOCKERFILE}"
grep -Fq -- 'sha256sum --check SHA256SUMS' "${DOCKERFILE}"

if grep -Fq -- 'libc6-bin' "${DOCKERFILE}"; then
  echo "Use the internally validated libc6 package instead of libc6-bin" >&2
  exit 1
fi

if grep -Eq -- 'wheelhouse|pip download' \
  "${DOCKERFILE}" "${MOONCAKE_DIR}/scripts/prepare-offline-inputs.sh"; then
  echo "Python packages must come from pip.conf, not a manually imported wheelhouse" >&2
  exit 1
fi

if grep -Eq -- 'git clone|go install|GOPROXY|https://github.com' "${DOCKERFILE}"; then
  echo "Dockerfile must not access GitHub or Go tooling during the closed-network build" >&2
  exit 1
fi

echo "Mooncake internal-proxy image build contract is valid"
