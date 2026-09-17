# Offline Mooncake source inputs

This directory intentionally contains no committed third-party source or binaries.

Prepare one or more pinned source profiles on an Internet-connected staging host:

~~~bash
../scripts/prepare-offline-inputs.sh --profile 0.3.12.post1
../scripts/prepare-offline-inputs.sh --profile 0.3.10.post2
~~~

Each profile produces an exact source archive and a profile-specific checksum:

~~~text
mooncake-offline_<version>.tar.gz
mooncake-offline_<version>.tar.gz.sha256
~~~

Multiple versions may coexist here. The Docker build selects only the archive declared by
the requested MOONCAKE_PROFILE lock file, so there is no wildcard ambiguity.

The archive contains the pinned Mooncake source plus populated pybind11 and yalantinglibs
submodules. Python and Ubuntu/CUDA packages are resolved through the internal pip.conf and
sources.list settings. Generated source/checksum files are ignored by Git but remain part
of the Docker/Kaniko build context.
