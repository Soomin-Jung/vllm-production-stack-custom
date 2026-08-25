# Offline inputs

This directory intentionally contains no third-party binaries or source.

Run `../scripts/prepare-offline-inputs.sh` on an Internet-connected staging
host. Transfer the generated source archive and `SHA256SUMS` into this
directory before starting the closed-network Kaniko build. The archive
contains the pinned Mooncake source and populated pybind11/yalantinglibs
submodules. Python and Ubuntu packages are resolved through the internal
`pip.conf` and `sources.list` settings. Generated files are ignored by Git but
remain part of the Docker build context.
