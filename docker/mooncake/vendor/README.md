# Offline inputs

This directory intentionally contains no third-party binaries or source.

Run `../scripts/prepare-offline-inputs.sh` on an Internet-connected staging
host. Transfer the generated source archive, `wheelhouse/`, and
`SHA256SUMS` into this directory before starting the closed-network Kaniko
build. The generated files are ignored by Git but remain part of the Docker
build context.

