# vLLM 이미지용 Mooncake 0.3.10.post2 사내 프록시 빌드 가이드

## 1. 목적과 적용 범위

이 디렉터리는 폐쇄망 Kaniko에서 사내 APT/pip 프록시를 사용하여 기존 vLLM 이미지에
`mooncake-transfer-engine==0.3.10.post2`를 소스 빌드하여 추가한다.

생성되는 Transfer Engine은 다음 두 transport를 모두 포함한다.

| vLLM `mooncake_protocol` | Mooncake 빌드 옵션 | 의도한 경로 |
|---|---|---|
| `nvlink` | `USE_MNNVL=ON` | Mooncake MNNVL/NVLink transport |
| `nvlink_intra` | `USE_INTRA_NVLINK=ON` | 동일 노드 CUDA P2P/NVLink transport |

이 변경은 Helm P/D Cell PR과 독립된 이미지 공급 경로다. 따라서 `main`에서
별도 PR로 관리하며, PR #2와 그 후속 PR #4가 어느 순서로 merge되더라도
이미지 빌드 변경과 Helm diff가 섞이지 않는다.

대상은 현재 P/D 시험에 사용하는 Ubuntu 계열 vLLM CUDA 이미지다. 기본값은
Python 3.12와 CUDA 12.9 개발 패키지이며, 다른 조합은 build argument로
명시적으로 바꾼다.

## 2. 공식 wheel에 transport가 없는 이유

Mooncake `v0.3.10.post2`의 공식 CUDA 12 release workflow는 CMake에
`USE_CUDA=ON`을 전달하지만 다음 두 옵션은 전달하지 않는다.

```text
USE_MNNVL=OFF          # default
USE_INTRA_NVLINK=OFF   # default
```

Mooncake 소스의 조건부 빌드는 다음과 같이 연결된다.

```text
-DUSE_MNNVL=ON
  -> nvlink_transport object 포함
  -> protocol 이름 "nvlink" 활성화

-DUSE_INTRA_NVLINK=ON
  -> intranode_nvlink_transport object 포함
  -> protocol 이름 "nvlink_intra" 활성화
```

공식 workflow가 별도로 `nvlink_allocator.so`를 생성하는 것과 transport 구현이
wheel에 포함되는 것은 다른 문제다. allocator 파일이 보이더라도 위 transport
object가 컴파일되지 않았다면 `nvlink` 또는 `nvlink_intra`는 사용할 수 없다.

`USE_CUDA=ON`만으로는 CUDA 메모리 인식과 GPU-aware TCP/RDMA 기반을 켤 뿐,
두 NVLink transport를 자동으로 포함하지 않는다.

## 3. 이번 빌드 프로파일

`docker/Dockerfile.vllm-mooncake`는 다음 기능만 빌드한다.

| 항목 | 값 | 이유 |
|---|---:|---|
| Transfer Engine | ON | vLLM MooncakeConnector가 요구 |
| CUDA | ON | GPU VRAM 직접 전송에 필요 |
| MNNVL `nvlink` | ON | 요청한 `nvlink` transport 포함 |
| Intra-node `nvlink_intra` | ON | 동일 노드 CUDA P2P/NVLink 포함 |
| TCP / HTTP metadata | ON | fallback 및 vLLM bootstrap 통합 유지 |
| Mooncake Store | OFF | P/D KV 직접 전송에 불필요 |
| Store Rust / Go binding | OFF | 불필요한 툴체인과 의존성 제거 |
| etcd / Redis | OFF | vLLM P/D bootstrap 경로에서 불필요 |
| EP / TENT | OFF | 이번 이미지의 목적 밖 |

### Go proxy가 필요 없는 이유

upstream `dependencies.sh`는 Store와 etcd wrapper까지 포괄하므로 Go를
설치한다. 이 Dockerfile은 그 스크립트를 실행하지 않고 아래를 모두 끈다.

```text
WITH_STORE=OFF
WITH_STORE_RUST=OFF
WITH_STORE_GO=OFF
USE_ETCD=OFF
```

따라서 이 빌드에는 Go 실행 파일, Go module, `GOPROXY`가 전혀 필요하지 않다.
Dockerfile 정적 검증도 GitHub 접근과 Go 관련 명령이 들어오면 실패한다.

## 4. 고정된 소스와 빌드 context

### 4.1 소스 잠금

모든 값은 `SOURCE_LOCK.env`에 저장되어 있다.

| 구성요소 | 고정 값 |
|---|---|
| Mooncake version/tag | `0.3.10.post2` / `v0.3.10.post2` |
| Mooncake commit | `e1d6d6f6f49fbbd77b7ee6e5d0c77349f341b3e3` |
| pybind11 submodule | `58c382a8e3d7081364d2f5c62e7f429f0412743b` |
| yalantinglibs submodule | `73dea196d23ad8fcd4914c6ef1238f390b9a1c48` |

GitHub에서 제공하는 일반 source tarball만 가져오면 submodule 본문이 비어
있다. 반드시 위 두 submodule까지 실제 파일로 채운 bundle을 반입해야 한다.

### 4.2 폐쇄망에 수동 반입할 외부 소스

인터넷 연결 구간에서 준비 스크립트를 실행하면 다음 두 파일이 생성된다.

```text
docker/mooncake/vendor/
├── mooncake-v0.3.10.post2-src.tar.gz
└── SHA256SUMS
```

source archive 안에는 Mooncake 본체와 고정된 pybind11/yalantinglibs submodule
본문이 모두 들어 있다. 따라서 별도 Python wheelhouse는 수동 반입하지 않는다.

다음 의존성은 기존 내부 저장소를 사용한다.

| 의존성 | 공급 경로 |
|---|---|
| vLLM base image | 내부 Docker registry/Artifactory proxy |
| Ubuntu build packages | `/etc/apt/sources.list`에 복사한 APT Artifactory 설정 |
| CUDA compiler/dev package | 기존 NVIDIA APT mirror 또는 devel base |
| Python build packages | `/etc/pip.conf`에 복사한 pip Artifactory 설정 |

이 Dockerfile은 Ubuntu vLLM base를 전제로 하므로 패키지 관리자는 `apt`다.
YUM/APK proxy만 있고 APT proxy가 없다면 현재 이미지 계열과 맞는 APT mirror를
먼저 Artifactory에 노출해야 한다.

### 4.3 Kaniko context에 추가할 사내 설정 파일

Dockerfile의 `COPY`는 저장소 root 기준이다. Kaniko를 실행하기 전에 build context를
다음처럼 구성한다.

```text
<repository-root>/
├── certs/
│   └── <internal-root-or-intermediate-ca>.crt
├── pip.conf
├── sources.list
└── docker/
    ├── Dockerfile.vllm-mooncake
    └── mooncake/vendor/
        ├── mooncake-v0.3.10.post2-src.tar.gz
        └── SHA256SUMS
```

- CA 파일은 PEM 형식이고 확장자가 `.crt`여야 한다.
- `pip.conf`에는 사내 index URL과 필요한 trusted-host/인증 설정을 둔다.
- `sources.list`에는 폐쇄망에서 접근 가능한 Ubuntu/NVIDIA mirror만 둔다.
- 인증정보가 포함된 설정 파일과 사내 CA는 공개 Git 저장소에 commit하지 않는다.

Dockerfile은 이 설정으로 `configured-base` stage를 먼저 만든다. 이후 builder와
runtime이 모두 이를 상속하므로 최종 vLLM 이미지에도 사내 CA와 저장소 설정이
일관되게 적용된다.

### 4.4 인터넷 연결 구간에서 source bundle 생성

저장소 root에서 실행한다.

```bash
bash docker/mooncake/scripts/prepare-offline-inputs.sh
```

스크립트는 다음을 자동 검증한다.

1. Mooncake를 고정 commit으로 checkout한다.
2. pybind11과 yalantinglibs를 고정 submodule commit으로 checkout한다.
3. commit 정보를 `SOURCE_MANIFEST.env`로 bundle 안에 기록한다.
4. source archive의 `SHA256SUMS`를 만든 후 즉시 재검증한다.

생성 파일은 Git에 commit되지 않도록 ignore되어 있지만 Docker/Kaniko build
context에는 포함된다.

### 4.5 폐쇄망 반입 후 무결성 확인

```bash
cd docker/mooncake/vendor
sha256sum --check SHA256SUMS
```

두 파일을 함께 옮겨야 한다.

- Mooncake와 submodule이 들어 있는 source archive
- `SHA256SUMS`

## 5. ABI 일치 전략

이전 CUDA runtime ABI 문제를 피하기 위해 builder와 final stage가 동일한
`VLLM_BASE_IMAGE`를 사용한다.

```text
same vLLM base image
├── same Python interpreter / CPython ABI
├── same glibc floor
├── same CUDA runtime
└── builder에만 compiler/header 추가
```

builder stage에만 CUDA compiler와 C/C++ 개발 패키지를 설치하고, 완성된 wheel만
원본 vLLM base를 다시 사용하는 final stage에 복사한다. 따라서 최종 이미지에는
compiler와 소스가 남지 않는다.

반드시 내부 base image를 tag보다 digest로 고정하는 편이 좋다.

```text
registry.example/vllm-openai@sha256:<digest>
```

같은 `v0.27.1-cu129` tag가 재게시되면 Python, CUDA, glibc가 바뀌어 빌드
재현성이 깨질 수 있기 때문이다.

## 6. Kaniko 빌드

빌드 context는 `certs/`, `pip.conf`, `sources.list`가 있는 저장소 root여야 한다.

```bash
/kaniko/executor \
  --context dir:///workspace/vllm-production-stack-custom \
  --dockerfile docker/Dockerfile.vllm-mooncake \
  --destination registry.example/vllm-openai:v0.27.1-cu129-mooncake-0.3.10.post2 \
  --build-arg VLLM_BASE_IMAGE=registry.example/vllm-openai@sha256:<digest> \
  --build-arg PYTHON_VERSION=3.12 \
  --build-arg MOONCAKE_BUILD_JOBS=8 \
  --build-arg 'CUDA_DEVEL_PACKAGES=cuda-nvcc-12-9 cuda-cudart-dev-12-9 cuda-driver-dev-12-9'
```

Dockerfile에는 BuildKit 전용 `RUN --mount`가 없으므로 Kaniko에서 별도 문법
변환이 필요 없다. APT와 pip는 각각 복사된 `sources.list`와 `pip.conf`를 사용하며,
GitHub/PyPI/Ubuntu public repository에 직접 접근하지 않는다.

### CUDA 13 base를 사용할 때

CUDA 13 base라면 두 값을 같이 바꾼다.

```text
MOONCAKE_CU13_BUILD=1
CUDA_DEVEL_PACKAGES="cuda-nvcc-13-0 cuda-cudart-dev-13-0 cuda-driver-dev-13-0"
```

`MOONCAKE_CU13_BUILD=1`은 upstream release 방식과 같이 distribution 이름을
`mooncake-transfer-engine-cuda13`으로 만든다. CUDA 12.9 base에서는 기본값
`0`을 유지해 `mooncake-transfer-engine`으로 설치한다.

### base에 CUDA devel 도구가 이미 있을 때

`nvcc`와 `/usr/local/cuda/include/cuda_runtime.h`가 모두 있으면
`CUDA_DEVEL_PACKAGES`는 설치되지 않는다. 이때도 build argument는 그대로
두어도 된다.

## 7. Docker build 중 수행되는 검증

### 소스 검증

- package version이 `0.3.10.post2`인지 확인
- Mooncake/pybind11/yalantinglibs commit 잠금 확인
- 두 submodule의 필수 source가 실제로 존재하는지 확인

### CMake 검증

빌드 직후 `CMakeCache.txt`에서 아래 값을 exact match한다.

```text
USE_CUDA:BOOL=ON
USE_MNNVL:BOOL=ON
USE_INTRA_NVLINK:BOOL=ON
WITH_STORE:BOOL=OFF
```

결과는 final image의 다음 경로에도 남는다.

```text
/opt/mooncake-build-info/SOURCE_LOCK.env
/opt/mooncake-build-info/CMAKE_FEATURES.txt
```

### wheel/final image 검증

- 기존 CUDA 12/13 Mooncake distribution을 제거하고 정확히 하나만 설치
- version `0.3.10.post2` 확인
- `engine.so` 안의 `nvlink`와 `nvlink_intra` transport marker 확인
- `ldd`에서 예상하지 못한 unresolved library 확인
- vLLM base에 Mooncake runtime dependency인 `aiohttp`, `requests`가 있는지 확인

이미지 build 시 NVIDIA driver가 mount되지 않으므로 `libcuda.so.1`과
`libnvidia-ml.so.1`만 build-time unresolved 항목으로 허용한다. GPU Pod에서는
다음 절차로 실제 extension load까지 확인한다.

```bash
python3 /usr/local/bin/verify-mooncake-install --load-extension
cat /opt/mooncake-build-info/CMAKE_FEATURES.txt
```

## 8. PR #4 P/D Cell에서 transport 선택

PR #4의 model-local `kvTransfer`와 model 공통 `env` 계약을 기준으로 한다.
Prefill과 Decode가 같은 model block의 env를 상속하므로 양쪽 process에 동일한
transport 선택 환경변수가 들어간다.

### 8.1 권장: 동일 노드 P/D에서 `nvlink_intra`

Network B의 H200 NVSwitch 동일 노드 P/D Cell은 먼저 이 경로를 검증한다.

```yaml
pdCellSpec:
  enabled: true
  models:
    - name: qwen-pd-p1d1
      repository: registry.example/vllm-openai
      tag: v0.27.1-cu129-mooncake-0.3.10.post2

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
```

`MC_INTRANODE_NVLINK`는 값이 `1`인지 검사하는 변수가 아니라 **환경변수의 존재
여부**를 검사한다. 따라서 비활성화할 때 `"0"`으로 두면 안 되고 env 항목 자체를
삭제해야 한다.

### 8.2 `nvlink` 비교 시험

```yaml
pdCellSpec:
  enabled: true
  models:
    - name: qwen-pd-p1d1
      repository: registry.example/vllm-openai
      tag: v0.27.1-cu129-mooncake-0.3.10.post2

      env:
        - name: MC_FORCE_MNNVL
          value: "1"

      kvTransfer:
        connector: MooncakeConnector
        config:
          kv_buffer_device: cuda
          kv_load_failure_policy: fail
          kv_connector_extra_config:
            mooncake_protocol: nvlink
```

이 시험에서는 `MC_INTRANODE_NVLINK`를 완전히 제거한다. 두 변수가 동시에 있으면
Mooncake 0.3.10.post2 코드상 `MC_INTRANODE_NVLINK`가 우선하여
`nvlink_intra` transport가 설치된다.

Network B처럼 HCA가 없는 노드에서는 Mooncake가 `nvlink`를 자동 선택할 수도
있지만, 비교 시험은 `MC_FORCE_MNNVL=1`로 경로를 명시하여 실행 조건을 고정한다.

## 9. GPU runtime 검증 순서

### 9.1 이미지 자체

```bash
kubectl exec <pd-cell-pod> -c prefill-0 -- \
  python3 /usr/local/bin/verify-mooncake-install --load-extension

kubectl exec <pd-cell-pod> -c decode-0 -- \
  python3 /usr/local/bin/verify-mooncake-install --load-extension
```

두 container 모두 아래를 출력해야 한다.

```text
version=0.3.10.post2
transports=nvlink,nvlink_intra
extension_loaded=True
```

### 9.2 실제 선택된 transport

`nvlink_intra` 시험에서는 Prefill과 Decode log 양쪽에서 다음 계열을 확인한다.

```text
Selected Intra-NVLink memory allocator
Using Intra-Node NVLink transport (MC_INTRANODE_NVLINK set)
```

`nvlink` 시험에서는 다음 계열을 확인한다.

```text
Selected MNNVL (NVLink) memory allocator
Using cross-node NVLink transport
```

transport marker가 wheel에 있다는 사실만으로 실제 KV가 그 경로를 탔다고
판정하면 안 된다. 최종 판정은 engine log, P/D 성공 요청, 전송 시간, TCP
fallback 부재를 함께 확인해야 한다.

### 9.3 P/D 기능 시험

최소 순서는 다음과 같다.

1. `/health`, `/v1/models` 확인
2. 짧은 non-streaming 요청
3. streaming 요청과 cancellation
4. 50K 이상 prompt의 Prefill → Decode 전환
5. 동시 요청에서 `kv_load_failure_policy=fail` 기준 오류율 확인
6. `nvlink_intra`와 `nvlink`의 TTFT, transfer latency, CPU 사용률 비교
7. Pod restart 후 Cell recovery 확인

## 10. 흔한 실패와 판별 기준

| 증상 | 가장 먼저 볼 항목 | 의미 |
|---|---|---|
| source archive가 없다고 즉시 실패 | `vendor/` 반입 상태 | GitHub 접근을 시도하지 않고 정상적으로 fail-fast한 것 |
| pybind11 header가 없음 | source bundle 생성 방식 | 일반 GitHub tarball만 반입하여 submodule이 비어 있음 |
| `nvcc: not found` | CUDA APT mirror와 `CUDA_DEVEL_PACKAGES` | runtime base에 devel package를 추가하지 못함 |
| `Python.h` 없음 | `PYTHON_VERSION`, base image | builder Python과 vLLM Python ABI가 불일치할 위험 |
| yalantinglibs CMake package 없음 | submodule/선행 install log | pinned yalantinglibs build 또는 install 실패 |
| pip가 외부 index를 조회 | `/etc/pip.conf`의 index 설정 | 사내 Artifactory URL만 사용해야 함 |
| APT TLS 인증 실패 | `certs/*.crt`, `sources.list` | CA가 PEM `.crt`인지와 mirror URL을 확인 |
| transport marker 누락 | `CMAKE_FEATURES.txt` | CMake flag가 실제 build에 반영되지 않음 |
| GPU Pod에서 `libcuda.so.1` 누락 | NVIDIA runtime/device plugin | build 문제가 아니라 container runtime driver mount 문제 |
| `nvlink_intra` 설정인데 `nvlink` log | `MC_INTRANODE_NVLINK` 양쪽 주입 | protocol 문자열과 transport 자동 설치 조건이 어긋남 |
| `nvlink` 설정인데 intra log | `MC_INTRANODE_NVLINK` 잔존 | 값 `0`도 존재로 판정되므로 env 자체를 제거해야 함 |

## 11. 이번 PR에서 의도적으로 하지 않는 것

- Mooncake third-party source bundle을 Git 저장소에 commit하지 않는다.
- `pip.conf`, `sources.list`, 사내 CA의 실제 값이나 credential을 공개 저장소에 기록하지 않는다.
- Artifactory 주소나 credential을 Dockerfile에 기록하지 않는다.
- Go proxy를 새로 구성하지 않는다.
- PR #2/#4 Helm template을 이 PR에서 수정하지 않는다.
- Kaniko build 성공만으로 GPU NVLink runtime 성공을 선언하지 않는다.

실제 폐쇄망 Kaniko build와 H200 P/D runtime 결과가 확인되기 전까지 PR은 Draft로
유지한다.
