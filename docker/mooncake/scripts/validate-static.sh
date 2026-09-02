#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
DOCKERFILE=${ROOT_DIR}/docker/Dockerfile.vllm-mooncake
MOONCAKE_DIR=${ROOT_DIR}/docker/mooncake
LOCK_DIR=${MOONCAKE_DIR}/locks

for script in \
  "${MOONCAKE_DIR}/scripts/prepare-offline-inputs.sh" \
  "${MOONCAKE_DIR}/scripts/detect-cuda-contract.sh" \
  "${MOONCAKE_DIR}/scripts/verify-source-lock.sh" \
  "${MOONCAKE_DIR}/scripts/validate-static.sh"; do
  bash -n "${script}"
done

python3 -c \
  'from pathlib import Path; path=Path(__import__("sys").argv[1]); compile(path.read_text(), str(path), "exec")' \
  "${MOONCAKE_DIR}/scripts/verify-install.py"

tmp_dir=$(mktemp -d)
trap 'rm -rf -- "${tmp_dir}"' EXIT
mkdir -p "${tmp_dir}/torch"

cat > "${tmp_dir}/torch/__init__.py" <<'PY'
class Version:
    cuda = "12.9"

version = Version()
PY

PYTHONPATH="${tmp_dir}" \
  "${MOONCAKE_DIR}/scripts/detect-cuda-contract.sh" auto "${tmp_dir}/cuda12.env" >/dev/null
grep -Fxq 'CUDA_APT_SUFFIX=12-9' "${tmp_dir}/cuda12.env"
grep -Fxq 'MOONCAKE_CU13_BUILD=0' "${tmp_dir}/cuda12.env"
grep -Fxq 'MOONCAKE_PACKAGE_NAME=mooncake-transfer-engine' "${tmp_dir}/cuda12.env"

cat > "${tmp_dir}/torch/__init__.py" <<'PY'
class Version:
    cuda = "13.0"

version = Version()
PY

PYTHONPATH="${tmp_dir}" \
  "${MOONCAKE_DIR}/scripts/detect-cuda-contract.sh" 13.0 "${tmp_dir}/cuda13.env" >/dev/null
grep -Fxq 'CUDA_APT_SUFFIX=13-0' "${tmp_dir}/cuda13.env"
grep -Fxq 'MOONCAKE_CU13_BUILD=1' "${tmp_dir}/cuda13.env"
grep -Fxq 'MOONCAKE_PACKAGE_NAME=mooncake-transfer-engine-cuda13' "${tmp_dir}/cuda13.env"

if PYTHONPATH="${tmp_dir}" \
  "${MOONCAKE_DIR}/scripts/detect-cuda-contract.sh" 12.9 "${tmp_dir}/bad.env" >/dev/null 2>&1; then
  echo "CUDA expected-version mismatch must fail" >&2
  exit 1
fi

for profile in 0.3.10.post2 0.3.12.post1; do
  lock="${LOCK_DIR}/${profile}.env"
  test -f "${lock}"
  (
    set -u
    # shellcheck disable=SC1090
    . "${lock}"
    test "${MOONCAKE_VERSION}" = "${profile}"
    test -n "${MOONCAKE_COMMIT}"
    test -n "${PYBIND11_COMMIT}"
    test -n "${YALANTINGLIBS_COMMIT}"
    test "${MOONCAKE_SOURCE_ARCHIVE}" = "mooncake-offline_${profile}.tar.gz"
    test -n "${MOONCAKE_RUNTIME_DEPS}"
  )
done

grep -Fq -- 'ARG VLLM_BASE_IMAGE=' "${DOCKERFILE}"
grep -Fq -- 'ARG MOONCAKE_PROFILE=0.3.12.post1' "${DOCKERFILE}"
grep -Fq -- 'ARG TARGET_CUDA_VERSION=auto' "${DOCKERFILE}"
grep -Fq -- 'ARG CUDA_DEVEL_PACKAGES=auto' "${DOCKERFILE}"
grep -Fq -- 'COPY docker/mooncake/locks/' "${DOCKERFILE}"
grep -Fq -- 'detect-cuda-contract.sh' "${DOCKERFILE}"
grep -Fq -- 'SOURCE_TAR="/opt/mooncake/vendor/${MOONCAKE_SOURCE_ARCHIVE}"' "${DOCKERFILE}"
grep -Fq -- 'CU13_BUILD="${MOONCAKE_CU13_BUILD}"' "${DOCKERFILE}"
grep -Fq -- 'cuda-nvcc-${CUDA_APT_SUFFIX}' "${DOCKERFILE}"
grep -Fq -- 'cuda-cudart-dev-${CUDA_APT_SUFFIX}' "${DOCKERFILE}"
grep -Fq -- 'cuda-driver-dev-${CUDA_APT_SUFFIX}' "${DOCKERFILE}"
grep -Fq -- '-DUSE_CUDA=ON' "${DOCKERFILE}"
grep -Fq -- '-DUSE_MNNVL=ON' "${DOCKERFILE}"
grep -Fq -- '-DUSE_INTRA_NVLINK=ON' "${DOCKERFILE}"
grep -Fq -- '-DWITH_STORE=OFF' "${DOCKERFILE}"
grep -Fq -- '-DUSE_ETCD=OFF' "${DOCKERFILE}"
grep -Fq -- 'ai.mooncake.transport.nvlink="true"' "${DOCKERFILE}"
grep -Fq -- 'ai.mooncake.transport.nvlink_intra="true"' "${DOCKERFILE}"
grep -Fq -- 'COPY certs/ /opt/certs/' "${DOCKERFILE}"
grep -Fq -- 'COPY pip.conf /etc/pip.conf' "${DOCKERFILE}"
grep -Fq -- 'COPY sources.list /etc/apt/sources.list' "${DOCKERFILE}"
grep -Fq -- 'update-ca-certificates' "${DOCKERFILE}"
grep -Fq -- 'rm -fv /etc/apt/sources.list.d/*.list' "${DOCKERFILE}"
# shellcheck disable=SC2016
grep -Fq -- 'LIBRARY_PATH=/usr/local/cuda/lib64/stubs:${LIBRARY_PATH}' "${DOCKERFILE}"
grep -Fq -- 'MOONCAKE_RUNTIME_DEPS' "${DOCKERFILE}"
grep -Fq -- 'linked_cudart=' "${MOONCAKE_DIR}/scripts/verify-install.py"
grep -Fq -- 'MC_FORCE_TCP' "${MOONCAKE_DIR}/README_KO.md"

if grep -Fq -- 'libc6-bin' "${DOCKERFILE}"; then
  echo "Use the internally validated libc6 package instead of libc6-bin" >&2
  exit 1
fi

if grep -Fq -- 'mooncake-offline_*.tar.gz' "${DOCKERFILE}"; then
  echo "Dockerfile must select the exact source archive from the profile lock" >&2
  exit 1
fi

if grep -Eq -- 'MOONCAKE_CU13_BUILD=|cuda-nvcc-12-9|cuda-nvcc-13-0' "${DOCKERFILE}"; then
  echo "CUDA variant must be derived from the vLLM base image, not hard-coded" >&2
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

echo "Mooncake multi-vLLM/multi-CUDA image build contract is valid"
