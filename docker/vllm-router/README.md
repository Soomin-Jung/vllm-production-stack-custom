# vLLM Router internal image build

This directory defines the deployable image path for the Cell-local
`vllm-project/router` used by the P/D Cell chart.

## Packaging decision

As of 2026-08-26, `vllm/vllm-router` on Docker Hub does not provide a
release-tagged image such as `v0.1.15`. The upstream release pipeline publishes
versioned Python artifacts to PyPI, while its Docker publishing section runs
only for `NIGHTLY=1` and creates `nightly` / dated-nightly images.

For production and closed-network use, do not pin the moving `nightly` tag.
Build an internal image from the official versioned PyPI wheel instead.

Current P/D Cell baseline:

```text
vllm-project/router tag: v0.1.15
PyPI package:            vllm-router==0.1.15
x86_64 wheel:            cp38-abi3-manylinux_2_28_x86_64
arm64 wheel:             cp38-abi3-manylinux_2_28_aarch64
```

Published v0.1.15 wheel SHA256 values:

| Architecture | SHA256 |
| --- | --- |
| x86_64 | `2f268b001a546d7921c2e87b510869134a212f0ab2faf138b78eb554c93a2241` |
| aarch64 | `c30070b2f8559fc33da4b114e58d28881775585dd6f6e1ac173ea494c8fbe20e` |

The release wheel is preferable to rebuilding Rust for every release because
it is a first-class upstream release artifact. The upstream Buildkite release
pipeline builds the wheels for x86_64/aarch64, smoke-tests them, and publishes
them to PyPI on version tags.

## Path A: fully offline wheelhouse build

This is the preferred path when the image build environment must not access any
package registry.

On an Internet-connected or package-staging host with the target architecture:

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

Transfer the tarball into the closed network, extract it back under this
directory, and build with an internally mirrored Python base image:

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

The Dockerfile uses all of the following controls:

- `--no-index`: package registries are never contacted.
- `--find-links`: packages come only from the copied wheelhouse.
- `--only-binary=:all:`: a missing wheel fails instead of silently compiling
  source code.
- optional `SHA256SUMS` verification before installation.
- package-version assertion and `vllm-router --help` smoke check during build.

The `BASE_IMAGE` itself must already be reachable from the internal registry.

## Path B: internal PyPI proxy build

If the closed network has a reliable PyPI/Artifactory proxy, use
`Dockerfile.proxy`.

Prepare local build inputs that are intentionally ignored by Git:

```text
docker/vllm-router/
├── Dockerfile.proxy
├── pip.conf
└── certs/
    └── corporate-root-ca.crt
```

Build:

```bash
cd docker/vllm-router

docker build \
  -f Dockerfile.proxy \
  --build-arg BASE_IMAGE=internal-registry/base/python:3.12-slim-bookworm \
  --build-arg VLLM_ROUTER_VERSION=0.1.15 \
  -t internal-registry/vllm/vllm-router:v0.1.15 \
  .
```

`Dockerfile.proxy` also uses `--only-binary=:all:`. If the internal PyPI proxy
has only an sdist or an incomplete mirror, the image build fails immediately
instead of unexpectedly requiring Rust/Cargo access.

The official Python slim image already includes `ca-certificates`; the proxy
Dockerfile adds the supplied `.crt` files to the system trust store before pip
access. No apt package installation is required for the router image itself.

## Why the upstream `Dockerfile.router` is not copied as-is

Upstream `v0.1.15/Dockerfile.router` is a valid upstream image recipe and is
used directly by the upstream Buildkite nightly Docker jobs. It builds the Rust
binary with `cargo build --release` and places it into a Python slim runtime.

It is therefore useful as a reference, but it is not the preferred production
recipe for this closed-network stack because it depends on mutable and online
build inputs:

- `rustlang/rust:nightly-bullseye` is a moving nightly compiler image.
- `apt-get` requires Debian package access.
- `cargo build` requires crates.io unless dependencies are mirrored or vendored.
- the runtime stage performs additional online pip operations that are not
  required when using the published release wheel.

The upstream release pipeline itself separates these concerns: version tags
produce and test release binaries/wheels, while DockerHub publishing is
nightly-only. The internal image should therefore package the release wheel,
not depend on a moving nightly image.

## Source-build fallback

Use a source build only if an upstream release wheel is unavailable for the
required architecture or a local source patch is necessary.

For a closed-network source build, the minimum safe procedure is:

1. Pin an upstream release tag and record its commit SHA.
2. Carry `Cargo.toml`, `Cargo.lock`, `src/`, and the rest of the release source
   into the staging environment.
3. On a connected staging host run `cargo vendor --locked vendor/`.
4. Configure Cargo to replace `crates-io` with the local `vendor/` directory.
5. Mirror and pin the Rust builder/runtime base images internally.
6. Build with `cargo build --release --locked --offline`.
7. Smoke-test `vllm-router --help` and the required P/D connector flags.
8. Push the resulting image to the internal registry and deploy by digest.

`v0.1.15/Cargo.toml` uses registry dependencies rather than Git dependencies,
so Cargo vendoring is feasible for this release. Do not use an unpinned online
`cargo build` as the normal production image path.

## Required runtime capability gate

Before using a newly packaged router version in P/D Cell, verify at least:

```bash
vllm-router --help | grep -- --vllm-pd-disaggregation
vllm-router --help | grep -- --kv-connector
```

For the current chart baseline, the accepted connector values must include
`nixl`, `mooncake`, and `moriio`, and Mooncake deployment must subsequently pass
the runtime bootstrap/transfer certification documented in
`helm/docs/PD_CELL_0.1.8_KO.md`.

## Helm image pin

After the image is pushed to the internal registry:

```yaml
pdCellSpec:
  router:
    repository: internal-registry/vllm/vllm-router
    tag: v0.1.15
```

For production, prefer a digest-pinned image policy in the deployment process
even if the values file keeps the human-readable release tag.
