ARG DOCKER_REGISTRY=docker.io

# Closed-network image for the Cell-local vLLM Router.
#
# Keep this file on baseline Dockerfile syntax only. In particular, do not add a
# '# syntax=docker/dockerfile:...' directive unless its frontend image is also
# mirrored internally; BuildKit can otherwise try to resolve it externally.
#
# This image intentionally installs the official vllm-router release wheel from
# a pre-populated local wheelhouse. No network access is required during the
# image build after BASE_IMAGE is available from the internal registry.
ARG BASE_IMAGE=library/python:3.12-slim-bookworm
FROM ${DOCKER_REGISTRY}/${BASE_IMAGE}

ARG VLLM_ROUTER_VERSION=0.1.15

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY wheelhouse/ /opt/vllm-router-wheelhouse/

WORKDIR /opt/vllm-router-wheelhouse

RUN set -eux; \
    if [ -f SHA256SUMS ]; then \
        sha256sum -c SHA256SUMS; \
    fi; \
    python -m pip install \
        --no-cache-dir \
        --no-index \
        --find-links=/opt/vllm-router-wheelhouse \
        --only-binary=:all: \
        "vllm-router==${VLLM_ROUTER_VERSION}"; \
    python -c "import importlib.metadata as m; assert m.version('vllm-router') == '${VLLM_ROUTER_VERSION}'"; \
    vllm-router --help >/dev/null; \
    rm -rf /opt/vllm-router-wheelhouse

WORKDIR /app

EXPOSE 8000
EXPOSE 29000

# Keep the upstream-style CMD contract. The Helm chart explicitly sets
# command: [vllm-router] and supplies the P/D orchestration arguments.
CMD ["vllm-router", "--host", "0.0.0.0", "--port", "8000"]
