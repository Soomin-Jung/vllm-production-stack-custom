# Agentic API v0.5.0 폐쇄망 이미지 빌드

이 빌드는 `vllm-project/agentic-api`의 `v0.5.0` tag가 가리키는 commit
`032935de73d92f116ac108f24cd63d6a158aad94`를 고정한다. 빌드 중 GitHub, crates.io, PyPI에 접근하지 않는다.

## 이미지 범위

Agentic API 저장소에는 Rust와 Python 코드가 모두 있지만 standalone 운영 컨테이너에 필요한 것은 Rust
`agentic-server` binary뿐이다. Python/maturin wheel과 vLLM Python dependency는 `serve <model>` 통합 개발 모드용이다.
Kubernetes에서는 별도 vLLM Production Stack에 연결하므로 이미지에 Python, CUDA, vLLM, 모델 weight를 포함하지
않는다. 결과적으로 GPU도 필요하지 않다.

## 공급망 고정값

고정값은 `SOURCE_LOCK.env`가 관리한다.

- Agentic API: `v0.5.0` / exact commit
- Rust builder: upstream v0.5.0 Dockerfile과 같은 `rust:1.96.0-bookworm` digest
- Runtime: upstream v0.5.0 Dockerfile과 같은 `debian:bookworm-slim` digest
- Rust dependency: upstream `Cargo.lock` + `cargo vendor --locked --versioned-dirs`

실제 폐쇄망에서는 두 base image를 내부 registry로 mirror하고, build arg에는 tag가 아니라 내부 digest reference를
전달한다. 소스 bundle과 `SHA256SUMS`는 용량과 provenance 때문에 Git에 commit하지 않는다.

## 1. 연결 구간에서 offline bundle 생성

Rust 1.96.0 toolchain과 Git, GNU tar, gzip이 있는 staging host에서 실행한다.

```bash
docker/agentic-api/scripts/prepare-offline-inputs.sh
docker/agentic-api/scripts/validate-static.sh
```

생성물:

```text
docker/agentic-api/vendor/
├── agentic-api-offline_0.5.0.tar.gz
└── SHA256SUMS
```

bundle에는 exact Git tree, `Cargo.lock`, vendored Cargo source, `.cargo/config.toml`, source manifest가 들어간다.
반입 매체에 두 파일을 함께 복사하고 반입 전후 SHA-256을 비교한다.

## 2. 폐쇄망 build context 준비

repository root에 다음 운영자 제공 파일을 둔다. `.gitignore`가 이 경로를 차단하므로 내부 주소나 인증서를 public
commit에 넣지 않는다.

```text
certs/
└── organization-root-ca.crt
sources.list
```

- `certs/*.crt`: 내부 apt mirror, PostgreSQL, upstream TLS 검증에 필요한 조직 CA. 최소 한 개가 필요하다.
- `sources.list`: Debian bookworm package mirror. Runtime의 `ca-certificates` 한 패키지만 이 mirror에서 설치한다.
- `pip.conf`: 필요 없음. 이 이미지에는 Python/pip 단계가 없다.

builder는 vendored dependency만 사용해 `cargo build --locked --frozen --offline`을 실행한다. Dockerfile에는
BuildKit cache mount가 없어 Kaniko에서도 동일한 dependency closure를 사용한다.

## 3. Kaniko로 build/push

아래 reference는 예시이므로 내부 registry의 digest로 교체한다. Kaniko context는 repository root여야 한다.

```bash
/kaniko/executor \
  --context dir:///workspace/vllm-production-stack-custom \
  --dockerfile /workspace/vllm-production-stack-custom/docker/Dockerfile.agentic-api \
  --destination registry.example.invalid/llm/agentic-api:0.5.0 \
  --build-arg AGENTIC_API_BUILDER_IMAGE=registry.example.invalid/base/rust:1.96.0-bookworm@sha256:REPLACE_ME \
  --build-arg AGENTIC_API_RUNTIME_IMAGE=registry.example.invalid/base/debian:bookworm-slim@sha256:REPLACE_ME \
  --build-arg CARGO_BUILD_JOBS=8 \
  --digest-file /workspace/agentic-api-image.digest
```

registry 인증은 Kaniko의 Docker config/credential helper로 주입하고 build arg나 image layer에 넣지 않는다.
배포 manifest에는 tag 대신 `/workspace/agentic-api-image.digest`의 immutable digest를 사용한다.

Docker BuildKit을 사용하는 검증 build도 repository root에서 가능하다.

```bash
docker build \
  --file docker/Dockerfile.agentic-api \
  --tag agentic-api:0.5.0 \
  --build-arg AGENTIC_API_BUILDER_IMAGE=registry.example.invalid/base/rust@sha256:REPLACE_ME \
  --build-arg AGENTIC_API_RUNTIME_IMAGE=registry.example.invalid/base/debian@sha256:REPLACE_ME \
  .
```

## 4. 산출물 검증

```bash
docker run --rm --entrypoint agentic-server agentic-api:0.5.0 --help
docker inspect agentic-api:0.5.0 \
  --format '{{ index .Config.Labels "org.opencontainers.image.version" }} {{ index .Config.Labels "org.opencontainers.image.revision" }}'
docker run --rm --entrypoint /bin/sh agentic-api:0.5.0 -c \
  'test ! -e /usr/local/bin/python && test ! -e /usr/local/cargo/bin/cargo && cat /usr/share/agentic-api/SOURCE_LOCK.env'
```

예상 version/revision은 `0.5.0`과 `032935de73d92f116ac108f24cd63d6a158aad94`다. 최종 image에는 compiler,
Cargo registry, vendored source, Python/vLLM이 없어야 한다. SBOM과 image vulnerability scan 결과도 digest와 함께
승인/보관한다.

Kubernetes 배포와 runtime 옵션은 [`../../deploy/agentic-api/README_KO.md`](../../deploy/agentic-api/README_KO.md)를
참조한다.
