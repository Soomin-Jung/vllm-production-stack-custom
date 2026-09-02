# vLLM 이미지용 Mooncake NVLink 소스 빌드 가이드

## 1. 목적

이 경로는 폐쇄망 Kaniko에서 **임의의 Ubuntu/APT 계열 vLLM CUDA 이미지**를 base로 받아
Mooncake Transfer Engine을 소스 빌드하고 다음 transport를 포함한 이미지를 만든다.

| vLLM mooncake_protocol | Mooncake CMake | 용도 |
|---|---|---|
| nvlink_intra | USE_INTRA_NVLINK=ON | 동일 노드 GPU P2P/NVLink |
| nvlink | USE_MNNVL=ON | MNNVL/NVLink |
| rdma | Transfer Engine | RDMA 비교/대체 |
| tcp | USE_TCP=ON | 명시적 TCP |

공식 Mooncake x86_64 wheel은 USE_INTRA_NVLINK=ON을 보장하지 않으므로,
nvlink_intra를 운영 계약으로 사용할 때는 이 source-build 경로를 사용한다.

## 2. 설계 원칙

vLLM version, CUDA ABI, Mooncake source version을 서로 독립된 축으로 관리한다.

~~~text
VLLM_BASE_IMAGE
  ├─ vLLM version
  ├─ Python/glibc
  └─ CUDA runtime ABI
       └─ torch.version.cuda에서 자동 감지
            ├─ CUDA 12.x -> mooncake-transfer-engine
            └─ CUDA 13.x -> mooncake-transfer-engine-cuda13

MOONCAKE_PROFILE
  ├─ Mooncake exact version/commit
  ├─ pybind11 commit
  ├─ yalantinglibs commit
  ├─ source archive
  └─ runtime Python dependencies
~~~

호스트 NVIDIA driver 제약 때문에 CUDA 13 이미지를 사용할 수 없다면,
동일 vLLM version의 cu129 image를 준비해 VLLM_BASE_IMAGE로 넘긴다.
Dockerfile은 host driver를 추측하지 않고 **선택된 base image의 CUDA ABI를 그대로 따른다.**

운영에서는 base image tag보다 digest pin을 권장한다.

~~~text
registry.example/vllm-openai@sha256:<digest>
~~~

## 3. Dockerfile build args

Dockerfile 최상단:

~~~dockerfile
ARG VLLM_BASE_IMAGE=vllm/vllm-openai:v0.28.0
ARG MOONCAKE_PROFILE=0.3.12.post1
ARG TARGET_CUDA_VERSION=auto
ARG CUDA_DEVEL_PACKAGES=auto
ARG MOONCAKE_BUILD_JOBS=8
~~~

### VLLM_BASE_IMAGE

실제 사용할 vLLM image를 그대로 지정한다.

예:

~~~text
v0.26.0-cu129
v0.28.0-cu129
v0.28.0 CUDA 13 계열
~~~

### MOONCAKE_PROFILE

docker/mooncake/locks/<profile>.env를 선택한다.

현재 제공:

| profile | commit | 목적 |
|---|---|---|
| 0.3.10.post2 | e1d6d6f6... | 기존 0.26/cu129 검증 재현 |
| 0.3.12.post1 | 6041a609... | 신규 0.28 계열 기준 |

profile은 vLLM version과 강제로 묶지 않는다.
다만 조합별 runtime 검증은 필요하다.

### TARGET_CUDA_VERSION

기본값은 auto다.

auto이면 base에서 torch.version.cuda를 읽는다.

운영 build에서 expected ABI를 강제하고 싶으면:

~~~text
--build-arg TARGET_CUDA_VERSION=12.9
--build-arg TARGET_CUDA_VERSION=13.0
~~~

를 사용한다. base와 다르면 build가 즉시 실패한다.

### CUDA_DEVEL_PACKAGES

기본 auto.

runtime base에 nvcc/header가 없으면 감지된 CUDA major/minor로 package를 만든다.

~~~text
CUDA 12.9
  -> cuda-nvcc-12-9
     cuda-cudart-dev-12-9
     cuda-driver-dev-12-9

CUDA 13.0
  -> cuda-nvcc-13-0
     cuda-cudart-dev-13-0
     cuda-driver-dev-13-0
~~~

사내 NVIDIA APT mirror package naming이 다르면 override한다.

~~~text
--build-arg 'CUDA_DEVEL_PACKAGES=<custom packages>'
~~~

## 4. CUDA 자동 계약

detect-cuda-contract.sh가 base image에서 /opt/mooncake/CUDA_CONTRACT.env를 만든다.

CUDA 12.9 예:

~~~text
BASE_CUDA_VERSION=12.9
CUDA_MAJOR=12
CUDA_MINOR=9
CUDA_APT_SUFFIX=12-9
MOONCAKE_CU13_BUILD=0
MOONCAKE_PACKAGE_NAME=mooncake-transfer-engine
~~~

CUDA 13.0 예:

~~~text
BASE_CUDA_VERSION=13.0
CUDA_MAJOR=13
CUDA_MINOR=0
CUDA_APT_SUFFIX=13-0
MOONCAKE_CU13_BUILD=1
MOONCAKE_PACKAGE_NAME=mooncake-transfer-engine-cuda13
~~~

따라서 operator가 MOONCAKE_CU13_BUILD와 package name을 별도로 맞출 필요가 없다.
현재 contract는 CUDA 12/13만 허용한다.

## 5. Source profile

0.3.12.post1 profile:

~~~bash
MOONCAKE_VERSION=0.3.12.post1
MOONCAKE_GIT_TAG=v0.3.12.post1
MOONCAKE_COMMIT=6041a609a8c3af35e778f70db344f145c2914980
PYBIND11_COMMIT=58c382a8e3d7081364d2f5c62e7f429f0412743b
YALANTINGLIBS_COMMIT=6a0e067d9a43492cf8e4e280b531924fbd724dbd
MOONCAKE_SOURCE_ARCHIVE=mooncake-offline_0.3.12.post1.tar.gz
MOONCAKE_RUNTIME_DEPS="aiohttp requests msgpack"
~~~

0.3.12 계열은 msgpack runtime dependency가 추가되므로
0.3.10 profile과 dependency contract도 분리한다.

## 6. Offline source 준비

인터넷 연결 구간에서:

~~~bash
bash docker/mooncake/scripts/prepare-offline-inputs.sh   --profile 0.3.12.post1
~~~

기존 0.3.10도 같이 준비 가능:

~~~bash
bash docker/mooncake/scripts/prepare-offline-inputs.sh   --profile 0.3.10.post2
~~~

vendor에는 여러 version을 동시에 둘 수 있다.

~~~text
docker/mooncake/vendor/
├── mooncake-offline_0.3.10.post2.tar.gz
├── mooncake-offline_0.3.10.post2.tar.gz.sha256
├── mooncake-offline_0.3.12.post1.tar.gz
└── mooncake-offline_0.3.12.post1.tar.gz.sha256
~~~

Dockerfile은 wildcard로 source를 고르지 않는다.
선택한 profile의 MOONCAKE_SOURCE_ARCHIVE만 exact match한다.

source archive에는 Mooncake와 populated pybind11/yalantinglibs submodule,
SOURCE_MANIFEST.env가 포함된다.

일반 GitHub source tarball은 submodule 본문이 비어 있으므로 사용하지 않는다.

## 7. 폐쇄망 build context

~~~text
<repository-root>/
├── certs/
├── pip.conf
├── sources.list
└── docker/
    ├── Dockerfile.vllm-mooncake
    └── mooncake/
        ├── locks/
        ├── scripts/
        └── vendor/
~~~

- certs/: 사내 CA
- pip.conf: 사내 Python package proxy
- sources.list: Ubuntu/NVIDIA APT mirror
- source archive, credential, 사내 URL/CA 본문은 공개 Git에 commit하지 않는다.
- Docker build 중 GitHub/Go proxy를 호출하지 않는다.

## 8. Kaniko build matrix

### vLLM 0.26 + CUDA 12.9 + Mooncake 0.3.10

~~~bash
/kaniko/executor   --context dir:///workspace/vllm-production-stack-custom   --dockerfile docker/Dockerfile.vllm-mooncake   --destination registry.example/vllm-openai:v0.26.0-cu129-mc0310-nvlink   --build-arg VLLM_BASE_IMAGE=registry.example/vllm-openai:v0.26.0-cu129   --build-arg MOONCAKE_PROFILE=0.3.10.post2   --build-arg TARGET_CUDA_VERSION=12.9
~~~

### vLLM 0.28 + CUDA 12.9 custom image + Mooncake 0.3.12

~~~bash
/kaniko/executor   --context dir:///workspace/vllm-production-stack-custom   --dockerfile docker/Dockerfile.vllm-mooncake   --destination registry.example/vllm-openai:v0.28.0-cu129-mc0312-nvlink   --build-arg VLLM_BASE_IMAGE=registry.example/vllm-openai:v0.28.0-cu129   --build-arg MOONCAKE_PROFILE=0.3.12.post1   --build-arg TARGET_CUDA_VERSION=12.9
~~~

이 경우 Mooncake distribution은 자동으로 mooncake-transfer-engine이다.

### vLLM 0.28 + CUDA 13 + Mooncake 0.3.12

~~~bash
/kaniko/executor   --context dir:///workspace/vllm-production-stack-custom   --dockerfile docker/Dockerfile.vllm-mooncake   --destination registry.example/vllm-openai:v0.28.0-cu13-mc0312-nvlink   --build-arg VLLM_BASE_IMAGE=registry.example/vllm-openai:v0.28.0   --build-arg MOONCAKE_PROFILE=0.3.12.post1   --build-arg TARGET_CUDA_VERSION=13.0
~~~

CUDA 13이면 CU13_BUILD=1이 자동 적용되고 mooncake-transfer-engine-cuda13 wheel을 만든다.
base가 확실하면 TARGET_CUDA_VERSION은 생략 가능하다.

## 9. CMake contract

모든 profile에서 P/D Transfer Engine에 다음을 강제한다.

~~~text
WITH_TE=ON
WITH_STORE=OFF
WITH_STORE_RUST=OFF
WITH_STORE_GO=OFF
WITH_P2P_STORE=OFF
WITH_EP=OFF

USE_CUDA=ON
USE_MNNVL=ON
USE_INTRA_NVLINK=ON
USE_TCP=ON
USE_HTTP=ON

USE_ETCD=OFF
USE_REDIS=OFF
USE_TENT=OFF
~~~

build 후 CMakeCache.txt에서 USE_CUDA, USE_MNNVL, USE_INTRA_NVLINK가 ON이고
WITH_STORE가 OFF인지 exact match한다.

provenance:

~~~text
/opt/mooncake-build-info/SOURCE_LOCK.env
/opt/mooncake-build-info/CUDA_CONTRACT.env
/opt/mooncake-build-info/CMAKE_FEATURES.txt
~~~

## 10. CUDA stub

builder link-time에는:

~~~text
LIBRARY_PATH=/usr/local/cuda/lib64/stubs:${LIBRARY_PATH}
~~~

를 유지한다.

stub path를 runtime LD_LIBRARY_PATH에 넣지 않는다.
실제 libcuda.so.1은 NVIDIA container runtime이 host driver에서 제공해야 한다.

## 11. final image 검증

verify-mooncake-install은 source profile과 실제 CUDA ABI를 동적으로 검증한다.

- Mooncake distribution 정확히 하나
- exact Mooncake version
- CUDA 12 -> mooncake-transfer-engine
- CUDA 13 -> mooncake-transfer-engine-cuda13
- engine.so의 nvlink/nvlink_intra marker
- ldd의 libcudart.so.<major>와 torch.version.cuda major 일치
- 예상하지 못한 unresolved library 없음
- profile별 runtime dependency 존재

GPU Pod:

~~~bash
python3 /usr/local/bin/verify-mooncake-install --load-extension
cat /opt/mooncake-build-info/SOURCE_LOCK.env
cat /opt/mooncake-build-info/CUDA_CONTRACT.env
cat /opt/mooncake-build-info/CMAKE_FEATURES.txt
~~~

CUDA 13 예:

~~~text
distribution=mooncake-transfer-engine-cuda13
version=0.3.12.post1
torch_cuda=13.0
linked_cudart=libcudart.so.13
transports=nvlink,nvlink_intra
extension_loaded=True
~~~

## 12. nvlink_intra runtime

~~~yaml
env:
  - name: MC_INTRANODE_NVLINK
    value: "1"

kvTransfer:
  connector: MooncakeConnector
  config:
    kv_buffer_device: cuda
    kv_load_failure_policy: fail
    kv_connector_extra_config:
      mooncake_protocol: nvlink_intra
~~~

MC_INTRANODE_NVLINK는 값이 아니라 환경변수 존재 여부로 판정한다.
비활성화할 때 value: "0"을 두지 말고 env 항목 자체를 제거한다.

## 13. Mooncake 0.3.10 vs 0.3.12 transport 우선순위

### 0.3.10.post2

~~~text
MC_INTRANODE_NVLINK 존재
  -> nvlink_intra
else MC_FORCE_MNNVL 존재 또는 HCA 없음
  -> nvlink
else
  -> rdma
~~~

### 0.3.12.post1

0.3.12에서는 MC_FORCE_TCP가 상위 단계에서 먼저 처리된다.

~~~text
MC_FORCE_TCP 존재
  -> tcp only
  -> return

else MC_INTRANODE_NVLINK 존재
  -> nvlink_intra

else MC_FORCE_MNNVL 존재 또는 HCA 없음
  -> nvlink

else
  -> rdma
~~~

MC_FORCE_TCP도 value "0"이 disable이 아니다. 사용하지 않을 때 env를 제거한다.

## 14. 0.3.12 nvlink_intra 데이터 경로 변화

0.3.12에서는 CUDA 12.8 이상에서 batch copy 경로가 들어간다.

~~~text
CUDA >= 13.0
  -> cudaMemcpyBatchAsync (CUDA 13 API)

CUDA >= 12.8
  -> cudaMemcpyBatchAsync (CUDA 12.8 API)

CUDA < 12.8
  -> per-slice cudaMemcpyAsync fallback
~~~

P2P source visibility를 위한 cudaMemcpySrcAccessOrderStream과
CUDA event/stream synchronization도 사용한다.

따라서 0.3.10의 throughput/latency baseline을 0.3.12에 그대로 적용하지 않는다.

재검증:

- KV transfer throughput/latency
- TTFT
- CPU utilization
- CUDA stream contention
- long-context transfer
- concurrent transfer scaling
- streaming/cancellation
- restart/recovery
- NVLink Tx/Rx

0.3.12.post1에서는 overlapped memory region 오류도 함께 grep한다.

## 15. 운영 전 점검

container:

~~~bash
python3 - <<'PY'
import torch
print(torch.version.cuda)
PY

python3 /usr/local/bin/verify-mooncake-install --load-extension
~~~

host/node:

~~~bash
nvidia-smi
~~~

선택한 vLLM CUDA runtime을 host driver가 지원하는지는 별도로 확인한다.

실제 intra transport:

~~~text
Using Intra-Node NVLink transport (MC_INTRANODE_NVLINK set)
~~~

0.3.12 + CUDA 12.8 이상에서는 cudaMemcpyBatchAsync 사용 로그도 확인한다.

## 16. 흔한 실패

| 증상 | 우선 확인 |
|---|---|
| CUDA contract mismatch | TARGET_CUDA_VERSION vs torch.version.cuda |
| cuda-nvcc-XX-X 없음 | 사내 NVIDIA mirror / CUDA_DEVEL_PACKAGES override |
| libcudart.so.12/13 mismatch | 다른 CUDA base에서 만든 Mooncake wheel |
| source archive 없음 | MOONCAKE_PROFILE과 vendor archive |
| checksum 실패 | tar.gz와 tar.gz.sha256 조합 |
| submodule source 없음 | 일반 GitHub archive 사용 여부 |
| libcuda.so.1 runtime 누락 | NVIDIA runtime/device plugin/driver |
| nvlink_intra가 아닌 transport 선택 | MC_INTRANODE_NVLINK/MC_FORCE_TCP/MC_FORCE_MNNVL 잔존 |
| Mooncake import 실패 | CUDA package variant/runtime dependency |
| overlapped memory region | 0.3.12 memory registration/KV region layout |

## 17. 운영 원칙

- vLLM base와 Mooncake wheel은 같은 CUDA/Python/glibc ABI에서 빌드한다.
- CUDA는 image tag 문자열이 아니라 실제 torch.version.cuda로 검증한다.
- host driver에 맞는 base image 선택은 배포 결정으로 유지한다.
- Mooncake source/submodule은 exact commit lock을 유지한다.
- 여러 Mooncake archive를 폐쇄망에 같이 둘 수 있다.
- NVLink compile flag와 runtime transport 선택을 별도로 검증한다.
- build 성공만으로 NVLink data path 성공을 선언하지 않는다.
