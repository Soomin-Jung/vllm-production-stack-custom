# vLLM Router internal image build

This directory defines the deployable image supply path for the Cell-local
`vllm-project/router` used by the P/D Cell chart.

## Production decision

As of 2026-08-26, Docker Hub `vllm/vllm-router` does not provide a stable
release-tagged image such as `v0.1.15`; the published Docker images are nightly
artifacts. Do not use a moving nightly image as the production P/D Cell baseline.

For the production baseline, follow the upstream execution model as closely as
possible:

```text
pinned vllm-project/router release source
        -> Cargo.lock
        -> internal Debian/Cargo proxies
        -> cargo build --release --locked
        -> standalone Rust vllm-router binary
        -> internal runtime image
        -> registry digest pin
```

The official PyPI wheel remains a supported fallback/recovery path. It also
contains the Rust Router core via PyO3, but the primary production image uses
the same standalone Rust binary path as upstream `Dockerfile.router`.

Current P/D Cell baseline:

```text
vllm-project/router tag: v0.1.15
Builder OS family:       Debian 11 Bullseye
Upstream builder image:  rustlang/rust:nightly-bullseye
Upstream runtime image:  python:3.12-slim-bullseye
Cell process:             standalone vllm-router Rust binary
```

In production, mirror both base images into the internal registry and pin them
by immutable internal tag or digest. `nightly-bullseye` is kept only as the
upstream reference default in the example Dockerfile.

## Required closed-network inputs

Prepare these local files before building. They are ignored by Git.

```text
docker/vllm-router/
├── Dockerfile.rust-proxy
├── cargo-config.toml.example       # committed template
├── cargo-config.toml               # internal Cargo proxy config
├── sources.list                    # Debian Bullseye apt proxy
├── certs/
│   └── corporate-root-ca.crt
└── router-src/                     # extracted exact release source tree
    ├── Cargo.toml
    ├── Cargo.lock
    └── src/
```

`router-src/` should come from the exact reviewed Router release, currently
`v0.1.15`. Record the corresponding upstream tag/commit SHA in the release
change record before building.

## Cargo proxy

The internal Cargo remote/proxy should use the official crates.io sparse index
as its upstream:

```text
index.crates.io
```

Use `cargo-config.toml.example` as the client-side template. The repository keeps
the same names used by the internal deployment convention:

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

Replace `<<DOMAIN>>` and `<<ENDPOINT>>` with the real internal endpoint. Use
`https://` instead of `http://` when the internal service is TLS-enabled; the
Docker builder installs the corporate CA before Cargo access. A sparse registry
URL should keep the trailing `/`.

Two Cargo settings have different jobs:

- `[registry].default = "cargo-proxy"` selects the default named registry for
  registry-oriented Cargo commands.
- `[source.crates-io].replace-with = "cargo-proxy"` redirects normal
  `Cargo.toml` dependencies away from crates.io, so the replacement source
  `[source.cargo-proxy]` is required for the actual build path.

TOML files must use ordinary ASCII quotes (`"`), not smart quotes (`“ ”`).

For v0.1.15, `Cargo.lock` contains crates.io registry dependencies and no
`git+...` package sources, so a Cargo remote repository that proxies crates.io
is sufficient for the Rust dependency graph.

Do not commit credentials. If the Cargo remote requires authentication, inject
credentials using Cargo credentials or the build system's secret mechanism. A
read-only internal Cargo remote or fully vendored dependency bundle is preferred
to putting a token in Docker build arguments or image layers.

## Production path: Rust source build through internal proxies

Copy `cargo-config.toml.example` to `cargo-config.toml`, replace its placeholders,
prepare the Bullseye `sources.list`, corporate CA, and release source tree, then:

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

Set `DOCKER_REGISTRY` to the registry host only. Keep each image argument as
the repository and tag below that registry. The default registry is `docker.io`.

`Dockerfile.rust-proxy` deliberately keeps the upstream v0.1.15 build/runtime
family while removing unnecessary external access:

- corporate CA is installed before apt/Cargo access,
- `sources.list` points apt to the internal Bullseye proxy,
- `/usr/local/cargo/config.toml` redirects dependency resolution to the internal
  Cargo proxy,
- `Cargo.lock` is enforced with `cargo build --release --locked`,
- no pip/PyPI operation occurs in the Rust production path,
- the final process is the standalone `/usr/local/bin/vllm-router`,
- final-image `ldd` rejects unresolved shared libraries,
- `vllm-router --help` is executed during both builder and runtime stages.

The runtime default remains `python:3.12-slim-bullseye` only for upstream image
parity; Python is not used by the Router process. After internal `ldd` and
runtime certification, a pinned `debian:bullseye-slim`-class runtime may be used
if it supplies every required shared library and the corporate trust bundle.

## Why raw upstream Dockerfile.router is not copied as-is

Upstream `v0.1.15/Dockerfile.router` is a valid and useful reference. It builds
the standalone Rust binary and uses Debian Bullseye for both stages. However it
assumes online/mutable inputs:

```text
rustlang/rust:nightly-bullseye
        -> apt-get from Debian
        -> cargo build from crates.io
        -> python:3.12-slim-bullseye
        -> additional pip operations
```

For the closed network we retain the standalone Rust execution path, but replace
those network dependencies with internal registry/APT/Cargo endpoints and remove
the unnecessary pip installation from the production build.

## Fully vendored Rust fallback

If the Cargo proxy is unavailable or policy requires a zero-package-network
build, stage dependencies outside the closed image build:

```bash
cargo vendor --locked vendor/
```

Then configure:

```toml
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
```

and build with:

```bash
cargo build --release --locked --offline
```

This is feasible for v0.1.15 because its Cargo lockfile does not contain Git
dependency sources.

## Wheel fallback

The existing `Dockerfile` / `Dockerfile.proxy` / `prepare-wheelhouse.sh` path is
kept as a fallback and CI reference for the upstream release wheel.

Published v0.1.15 wheel SHA256 values:

| Architecture | SHA256 |
| --- | --- |
| x86_64 | `2f268b001a546d7921c2e87b510869134a212f0ab2faf138b78eb554c93a2241` |
| aarch64 | `c30070b2f8559fc33da4b114e58d28881775585dd6f6e1ac173ea494c8fbe20e` |

The wheel route is useful for emergency recovery and release-artifact
cross-checks, but it is not the primary production image path for P/D Cell.

## Required runtime capability gate

For every newly built Router image:

```bash
vllm-router --help | grep -- --vllm-pd-disaggregation
vllm-router --help | grep -- --kv-connector
```

The current chart baseline requires the connector values `nixl`, `mooncake`,
and `moriio` to be present. Mooncake deployment must then pass bootstrap
`/query`, request `transfer_id`, producer send, and consumer receive/load runtime
certification before PR merge.

## Helm image pin

After pushing the image to the internal registry:

```yaml
pdCellSpec:
  router:
    repository: <internal-registry>/vllm/vllm-router
    tag: v0.1.15
```

For production deployment, record and prefer the immutable registry digest even
when Helm values retain the human-readable release tag.
