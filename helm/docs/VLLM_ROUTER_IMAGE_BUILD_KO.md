# vLLM Router 폐쇄망 이미지 빌드 기준

## 결론

P/D Cell의 Cell-local Router는 `vllm-project/router`를 사용하지만,
`vllm/vllm-router` Docker Hub 저장소에는 현재 `v0.1.15` 같은 정식 release
Docker tag가 제공되지 않는다.

Upstream `v0.1.15` release pipeline을 확인하면 역할이 명확히 분리되어 있다.

- version tag release: x86_64/aarch64 wheel 및 source distribution 빌드, 테스트,
  PyPI publish
- Docker publish: `NIGHTLY=1`일 때만 실행
- Docker tag: `nightly`, `nightly-YYYYMMDD-<sha>` 계열

따라서 운영 환경에서는 `nightly` 이미지를 고정해서 사용하지 않고,
**정식 PyPI release wheel을 사내 Docker image로 재패키징**하는 것을 기본
정책으로 한다.

현재 기준:

```text
upstream tag       v0.1.15
PyPI package       vllm-router==0.1.15
P/D connector      nixl / mooncake / moriio
Python wheel ABI   cp38-abi3
Linux baseline     manylinux_2_28
```

## 왜 PyPI wheel 경로가 기본인가

`v0.1.15` release pipeline은 다음을 수행한다.

1. x86_64/aarch64 release artifact build
2. Rust binary smoke test
3. Python wheel install/import test
4. version tag일 때 PyPI publish

즉 PyPI wheel은 임시 산출물이 아니라 upstream의 정식 release artifact다.

반면 upstream `Dockerfile.router`는 실제 nightly Docker build에서 사용되는
유효한 Dockerfile이지만 다음 특성이 있다.

```text
rustlang/rust:nightly-bullseye
        ↓
apt-get
        ↓
cargo build --release
        ↓
python:3.12-slim-bullseye
        ↓
pip install ...
```

폐쇄망 관점에서는 그대로 사용하기 어렵다.

- Rust nightly base가 mutable tag다.
- Cargo가 crates.io에 접근한다.
- apt repository 접근이 필요하다.
- runtime stage에도 추가 pip network access가 존재한다.
- release wheel이 이미 존재하므로 같은 Rust binary를 매번 다시 compile할
  운영상 이점이 작다.

따라서 upstream Dockerfile은 **reference/fallback**으로 취급하고, 정상적인
release version은 wheel 기반으로 사내 이미지를 만든다.

## 권장 경로 A: 완전 offline wheelhouse

가장 확실한 폐쇄망 방식이다.

외부 또는 staging 환경에서:

```bash
cd docker/vllm-router
rm -rf wheelhouse
mkdir -p wheelhouse

python3.12 -m pip download \
  --dest wheelhouse \
  --only-binary=:all: \
  "vllm-router==0.1.15"

(
  cd wheelhouse
  sha256sum ./*.whl > SHA256SUMS
)

tar -czf vllm-router-0.1.15-wheelhouse.tar.gz wheelhouse
```

반입 후:

```bash
cd docker/vllm-router

tar -xzf vllm-router-0.1.15-wheelhouse.tar.gz

docker build \
  -f Dockerfile \
  --build-arg BASE_IMAGE=internal-registry/base/python:3.12-slim-bookworm \
  --build-arg VLLM_ROUTER_VERSION=0.1.15 \
  -t internal-registry/vllm/vllm-router:v0.1.15 \
  .
```

이 Dockerfile은 build 중 package network access를 사용하지 않는다.

```text
--no-index
--find-links=/opt/vllm-router-wheelhouse
--only-binary=:all:
```

으로 install한다.

wheel이 없거나 dependency가 누락되면 source compile로 우회하지 않고 build가
즉시 실패한다.

## 권장 경로 B: 사내 PyPI proxy

사내 PyPI/Artifactory proxy가 안정적으로 동작하면
`docker/vllm-router/Dockerfile.proxy`를 사용한다.

빌드 컨텍스트:

```text
docker/vllm-router/
├── Dockerfile.proxy
├── pip.conf
└── certs/
    └── corporate-root-ca.crt
```

예:

```bash
docker build \
  -f Dockerfile.proxy \
  --build-arg BASE_IMAGE=internal-registry/base/python:3.12-slim-bookworm \
  --build-arg VLLM_ROUTER_VERSION=0.1.15 \
  -t internal-registry/vllm/vllm-router:v0.1.15 \
  .
```

이 경로도 `--only-binary=:all:`을 사용한다. 따라서 proxy에 wheel이 없고
sdist만 있는 상태에서 갑자기 Rust compile을 시작하지 않는다.

## v0.1.15 release wheel hash

현재 upstream PyPI 기준:

| Architecture | SHA256 |
| --- | --- |
| x86_64 | `2f268b001a546d7921c2e87b510869134a212f0ab2faf138b78eb554c93a2241` |
| aarch64 | `c30070b2f8559fc33da4b114e58d28881775585dd6f6e1ac173ea494c8fbe20e` |

실제 wheelhouse에는 router wheel뿐 아니라 resolved Python dependency wheel도
포함되므로, 반입 시 전체 파일에 대해 별도의 `SHA256SUMS`를 생성하고 Docker
build 단계에서 검증한다.

## Source build가 필요한 경우

다음 경우에만 source build를 사용한다.

- 필요한 architecture용 release wheel이 없음
- upstream release 전 commit을 반드시 사용해야 함
- 사내 patch를 Rust source에 적용해야 함

이 경우 raw upstream `Dockerfile.router`를 그대로 사용하지 말고 최소한 다음
조건을 적용한다.

1. release tag와 commit SHA 고정
2. builder/runtime base image를 사내 registry에 mirror하고 digest pin
3. `Cargo.lock` 유지
4. 외부 staging 환경에서 `cargo vendor --locked vendor/`
5. crates.io를 local vendor directory로 replace
6. `cargo build --release --locked --offline`
7. build artifact에서 `vllm-router --help` smoke test
8. P/D connector option 확인 후 사내 registry push

`v0.1.15`의 `Cargo.toml`에는 Git dependency가 없으므로 crates.io dependency를
vendor하는 방식이 가능하다.

## 신규 version 도입 절차

새로운 vLLM Router release가 나오면 다음 순서로 갱신한다.

1. Git tag와 PyPI version 일치 확인
2. release wheel architecture 확인
3. Mooncake/NIXL/MoriIO CLI 지원 확인
4. release wheel hash 기록
5. 사내 image build
6. image digest 기록
7. P1D1 smoke test
8. Mooncake/NIXL 실제 KV transfer certification
9. 검증 완료 후 Helm values의 router tag/digest 변경

Docker Hub nightly 존재 여부는 release artifact 선택 기준으로 사용하지 않는다.

## Helm 연결

```yaml
pdCellSpec:
  router:
    repository: internal-registry/vllm/vllm-router
    tag: v0.1.15
```

P/D Cell chart는 image 내부의 `vllm-router` command를 명시적으로 실행한다.
따라서 사내 이미지 build 이후 최소한 다음이 통과해야 한다.

```bash
vllm-router --help | grep -- --vllm-pd-disaggregation
vllm-router --help | grep -- --kv-connector
```

그 다음 Mooncake를 사용하는 경우 bootstrap `/query`, `transfer_id`, 실제 KV
send/receive까지 별도의 runtime certification을 수행한다.
