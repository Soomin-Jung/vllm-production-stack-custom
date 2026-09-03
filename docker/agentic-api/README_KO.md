# Agentic API v0.5.0 이미지 빌드

Agentic API 이미지는 `docker/vllm-router/Dockerfile.rust-proxy`와 같은 방식으로 빌드한다.

```text
Agentic API v0.5.0 source
  + Cargo.lock
  + internal Cargo proxy
  -> cargo build --release --locked
  -> standalone agentic-server image
```

별도 source 검증 archive나 manifest 없이 준비한 source tree를 그대로 사용한다.

## 준비할 파일

```text
certs/
sources.list
docker/agentic-api/
├── agentic-api-src/
├── cargo-config.toml
└── cargo-config.toml.example
```

- `agentic-api-src/`: 검토한 Agentic API v0.5.0 source tree
- `cargo-config.toml`: 내부 Cargo proxy 설정
- `certs/`: 내부 Cargo/APT endpoint용 CA
- `sources.list`: Debian bookworm 내부 APT mirror

Agentic API source의 `rust-toolchain.toml`은 Rust `1.98.0`을 지정하므로 builder image에도 같은 toolchain이 설치되어 있어야 한다. 내부 Cargo proxy는 crate 의존성만 제공하며 rustup toolchain 배포 파일은 제공하지 않는다.

source는 연결 가능한 구간에서 간단히 준비할 수 있다.

```bash
docker/agentic-api/scripts/prepare-source.sh
cp docker/agentic-api/cargo-config.toml.example \
  docker/agentic-api/cargo-config.toml
```

`cargo-config.toml`의 proxy URL만 실제 내부 주소로 바꾼다. source와 실제 proxy 설정은 Git에 commit하지 않는다.

## 빌드

`DOCKER_REGISTRY`는 registry host만 받고, image build arg는 registry 아래 repository와 tag만 받는다.
기본 registry는 `docker.io`다.

```bash
docker build \
  --file docker/Dockerfile.agentic-api \
  --build-arg DOCKER_REGISTRY=registry.example.invalid \
  --build-arg AGENTIC_API_BUILDER_IMAGE=base/rust:1.98.0-bookworm \
  --build-arg AGENTIC_API_RUNTIME_IMAGE=base/debian:bookworm-slim \
  --build-arg CARGO_BUILD_JOBS=8 \
  --tag registry.example.invalid/llm/agentic-api:0.5.0 \
  .
```

Kaniko도 repository root를 context로 두고 같은 build arg를 사용한다.

## 확인

```bash
docker run --rm --entrypoint agentic-server \
  registry.example.invalid/llm/agentic-api:0.5.0 --help
```

최종 image에는 Rust compiler, Cargo cache, Agentic API source, Python, vLLM, CUDA를 포함하지 않는다.
Kubernetes 배포 방법은 [`../../deploy/agentic-api/README_KO.md`](../../deploy/agentic-api/README_KO.md)를 참조한다.
