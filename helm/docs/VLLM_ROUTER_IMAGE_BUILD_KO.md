# vLLM Router 폐쇄망 이미지 빌드 기준

## 결론

P/D Cell의 Cell-local Router는 `vllm-project/router`를 사용한다.
2026-08-26 기준 Docker Hub `vllm/vllm-router`에는 `v0.1.15` 같은 안정적인
release Docker tag가 없고 nightly 계열만 제공된다.

따라서 운영 기준은 다음과 같이 고정한다.

```text
Production primary
  exact Router release source
    -> Cargo.lock
    -> 사내 APT/Cargo proxy
    -> cargo build --release --locked
    -> standalone Rust vllm-router
    -> 사내 image/digest

Fallback
  official PyPI release wheel
    -> wheel hash 검증
    -> 사내 image
```

PyPI wheel 역시 Rust core를 포함한 공식 release artifact지만, P/D Cell의
production primary는 upstream `Dockerfile.router` 및 Mooncake 예제와 실행
경로가 가장 가까운 **standalone Rust binary**로 한다.

## upstream v0.1.15 기본 환경

공식 `Dockerfile.router`의 OS family는 두 stage 모두 Debian 11 Bullseye다.

```text
Builder  rustlang/rust:nightly-bullseye
Runtime  python:3.12-slim-bullseye
```

upstream builder에서 설치하는 system package는 다음과 같다.

```text
build-essential
pkg-config
libssl-dev
```

폐쇄망에서는 public base tag를 직접 pull하지 않고 사내 Docker registry에
mirror한 image를 사용한다. `nightly-bullseye`는 mutable tag이므로 실제 운영
build record에는 내부 immutable tag 또는 digest를 남긴다.

Runtime은 우선 upstream parity를 위해 `python:3.12-slim-bullseye` family를
사용하지만 실제 process는 Python이 아니라 `/usr/local/bin/vllm-router` Rust
binary다. `ldd` 및 runtime certification 후 필요한 shared library가 모두
확인되면 `debian:bullseye-slim` 계열로 축소할 수 있다.

## 필요한 외부 저장소와 사내 대체점

| 용도 | upstream | 폐쇄망 입력 |
| --- | --- | --- |
| builder/runtime image | Docker Hub | 사내 Docker registry mirror |
| OS package | Debian Bullseye repository | `sources.list` + 사내 APT proxy |
| Rust dependency | crates.io | Cargo/Artifactory remote proxy |
| Router source | GitHub `vllm-project/router` | exact release source tarball 반입 |

v0.1.15 `Cargo.lock`에는 crates.io registry dependency가 존재하지만
`git+...` package source는 없다. 따라서 release source를 한번 반입한 뒤에는
Cargo remote가 crates.io만 정확히 proxy하면 별도 Git repository clone 없이
빌드할 수 있다.

## Cargo proxy 설정

사내 Cargo remote/proxy의 upstream은 crates.io sparse index를 사용한다.

```text
index.crates.io
```

PR에는 다음 template을 제공한다.

```text
docker/vllm-router/cargo-config.toml.example
```

사내 convention과 동일한 예시는 다음과 같다.

```toml
# ~/.cargo/config.toml
# Proxy 연결 URL: index.crates.io
# 설정 레퍼런스: https://doc.rust-lang.org/cargo/reference/config.html

[registry]
default = "cargo-proxy"

[registries.cargo-proxy]
index = "sparse+http://<<DOMAIN>>/<<ENDPOINT>>/"

[source.crates-io]
replace-with = "cargo-proxy"

[source.cargo-proxy]
registry = "sparse+http://<<DOMAIN>>/<<ENDPOINT>>/"
```

`<<DOMAIN>>` / `<<ENDPOINT>>`는 실제 사내 endpoint로 치환한다. 내부 endpoint가
TLS를 사용하면 `https://`를 사용하며 builder에는 사내 CA를 먼저 설치한다.
sparse registry URL은 마지막 `/`까지 포함한다.

중요한 점은 두 설정의 역할이 다르다는 것이다.

```text
[registry]
default = "cargo-proxy"
```

는 default named registry를 지정한다. 일반 `Cargo.toml` dependency를
crates.io 대신 proxy로 강제하려면 다음 source replacement가 별도로 필요하다.

```toml
[source.crates-io]
replace-with = "cargo-proxy"

[source.cargo-proxy]
registry = "sparse+http://<<DOMAIN>>/<<ENDPOINT>>/"
```

Cargo 공식 config reference도 `[source.<name>]`에 `replace-with`와 `registry`
source 정의를 두는 방식을 제공한다. 따라서 PR에서는 registry 이름과
replacement source 이름을 모두 `cargo-proxy`로 맞춰 혼동을 줄인다.

TOML 문자열에는 스마트 따옴표(`“ ”`)가 아니라 ASCII 큰따옴표(`"`)를 사용한다.

인증 token/password는 repository에 commit하지 않는다. Cargo remote가 인증을
요구한다면 Cargo credentials 또는 build system secret injection을 사용한다.
token을 Docker `ARG`나 source tree에 넣는 방식은 사용하지 않는다.

## Rust production build context

사내 build 전에 다음을 준비한다.

```text
docker/vllm-router/
├── Dockerfile.rust-proxy
├── cargo-config.toml.example
├── cargo-config.toml          # 실제 사내 endpoint, Git ignore
├── sources.list               # Debian Bullseye 사내 APT proxy, Git ignore
├── certs/                     # 사내 CA, Git ignore
└── router-src/                # exact v0.1.15 source, Git ignore
    ├── Cargo.toml
    ├── Cargo.lock
    └── src/
```

빌드 예:

```bash
cd docker/vllm-router

docker build \
  -f Dockerfile.rust-proxy \
  --build-arg DOCKER_REGISTRY=<internal-registry> \
  --build-arg RUST_BUILDER_IMAGE=rustlang/rust:nightly-bullseye \
  --build-arg RUNTIME_IMAGE=python:3.12-slim-bullseye \
  --build-arg VLLM_ROUTER_VERSION=0.1.15 \
  -t <internal-registry>/vllm/vllm-router:v0.1.15 \
  .
```

`DOCKER_REGISTRY`에는 registry host만 넣고 image 인자에는 그 아래 repository/tag만 넣는다.
인자를 생략하면 `docker.io`를 사용한다.

`Dockerfile.rust-proxy`는 다음을 보장한다.

1. 사내 CA를 먼저 trust store에 반영한다.
2. Debian package는 supplied `sources.list`만 사용한다.
3. Cargo는 supplied `/usr/local/cargo/config.toml`을 사용한다.
4. exact release의 `Cargo.lock`을 보존하고 `--locked`로 build한다.
5. final image에는 source/Cargo toolchain을 복사하지 않는다.
6. pip/PyPI를 production Rust path에서 사용하지 않는다.
7. builder와 runtime 모두 `vllm-router --help` smoke를 수행한다.
8. final runtime에서 `ldd` 결과의 `not found`를 build failure로 처리한다.

## upstream Dockerfile.router와의 차이

upstream `v0.1.15/Dockerfile.router` 자체는 공식/유효 recipe다. 문제는 폐쇄망
production에서 그대로 실행하면 다음 external access가 필요하다는 점이다.

```text
rustlang/rust:nightly-bullseye
  -> Debian apt
  -> crates.io
  -> python:3.12-slim-bullseye
  -> pip/PyPI
```

사내 production Dockerfile은 **Rust binary build 방식은 유지**하되:

```text
Docker Hub  -> internal registry
Debian      -> internal APT proxy
crates.io   -> internal Cargo proxy
GitHub      -> reviewed source tarball
pip         -> 제거
```

로 치환한다.

## Cargo proxy가 없는 완전 offline build

Cargo remote까지 사용할 수 없다면 외부/staging 환경에서:

```bash
cargo vendor --locked vendor/
```

를 수행하고:

```toml
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
```

로 설정한 뒤:

```bash
cargo build --release --locked --offline
```

을 사용한다. v0.1.15에는 Cargo Git dependency가 없으므로 이 경로를 사용할 수
있다.

## Wheel fallback

기존 파일은 fallback과 CI cross-check 목적으로 유지한다.

```text
docker/vllm-router/Dockerfile
  -> 완전 offline wheelhouse

docker/vllm-router/Dockerfile.proxy
  -> 사내 PyPI proxy

docker/vllm-router/prepare-wheelhouse.sh
  -> release wheel/dependency staging 및 SHA256 gate
```

v0.1.15 공식 Router wheel SHA256:

| Architecture | SHA256 |
| --- | --- |
| x86_64 | `2f268b001a546d7921c2e87b510869134a212f0ab2faf138b78eb554c93a2241` |
| aarch64 | `c30070b2f8559fc33da4b114e58d28881775585dd6f6e1ac173ea494c8fbe20e` |

Wheel은 emergency/recovery 경로로 유효하지만 production primary는 아니다.

## 신규 Router version 도입 절차

1. upstream release tag와 commit SHA 확인
2. `Cargo.toml` / `Cargo.lock` diff 및 신규 Git dependency 유무 확인
3. upstream `Dockerfile.router`/release pipeline 변경 확인
4. 사내 base image digest 고정
5. Cargo proxy에서 `cargo build --release --locked` 수행
6. `ldd` unresolved library 없음 확인
7. `vllm-router --help`에서 P/D 및 connector option 확인
8. image digest 기록 및 사내 registry push
9. P1D1부터 actual KV transfer runtime certification
10. 검증 완료 후 Helm Router tag/digest 변경

## Runtime capability gate

최소:

```bash
vllm-router --help | grep -- --vllm-pd-disaggregation
vllm-router --help | grep -- --kv-connector
```

현재 chart baseline에서는 `nixl`, `mooncake`, `moriio`가 보여야 한다.
Mooncake에서는 그 다음 bootstrap `/query`, 동일 request의 `transfer_id`,
Prefill KV send 및 Decode KV receive/load까지 실제 runtime으로 증명해야 한다.

## Helm 연결

```yaml
pdCellSpec:
  router:
    repository: <internal-registry>/vllm/vllm-router
    tag: v0.1.15
```

운영 배포 기록에는 가능하면 human-readable tag와 함께 immutable image digest도
남긴다.
