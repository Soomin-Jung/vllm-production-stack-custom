#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import pathlib
import subprocess
import sys


EXPECTED_VERSION = "0.3.10.post2"
PACKAGE_NAMES = (
    "mooncake-transfer-engine",
    "mooncake-transfer-engine-cuda13",
)
EXPECTED_TOKENS = (b"nvlink_intra", b"nvlink")
ALLOWED_BUILD_TIME_MISSING_LIBRARIES = {"libcuda.so.1", "libnvidia-ml.so.1"}


def installed_distribution() -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for package_name in PACKAGE_NAMES:
        try:
            matches.append(
                (package_name, importlib.metadata.version(package_name))
            )
        except importlib.metadata.PackageNotFoundError:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one Mooncake distribution, got {matches}")
    return matches[0]


def engine_path() -> pathlib.Path:
    spec = importlib.util.find_spec("mooncake")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("cannot locate the mooncake package")
    package_dir = pathlib.Path(next(iter(spec.submodule_search_locations)))
    candidates = sorted(package_dir.glob("engine*.so"))
    if len(candidates) != 1:
        raise RuntimeError(f"expected one Mooncake engine shared object, got {candidates}")
    return candidates[0]


def verify_linkage(path: pathlib.Path) -> list[str]:
    result = subprocess.run(
        ["ldd", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    missing = []
    for line in result.stdout.splitlines():
        if "=> not found" in line:
            missing.append(line.strip().split()[0])
    unexpected = sorted(set(missing) - ALLOWED_BUILD_TIME_MISSING_LIBRARIES)
    if unexpected:
        raise RuntimeError(f"unexpected unresolved libraries: {unexpected}")
    return sorted(set(missing))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--load-extension",
        action="store_true",
        help="Import mooncake.engine; use this in a GPU-enabled runtime pod.",
    )
    args = parser.parse_args()

    package_name, version = installed_distribution()
    if version != EXPECTED_VERSION:
        raise RuntimeError(f"expected {EXPECTED_VERSION}, got {version}")

    path = engine_path()
    payload = path.read_bytes()
    missing_tokens = [token.decode() for token in EXPECTED_TOKENS if token not in payload]
    if missing_tokens:
        raise RuntimeError(f"transport markers missing from {path}: {missing_tokens}")

    missing_libraries = verify_linkage(path)

    if args.load_extension:
        importlib.import_module("mooncake.engine")

    print(f"distribution={package_name}")
    print(f"version={version}")
    print(f"engine={path}")
    print("transports=nvlink,nvlink_intra")
    print(f"build_time_missing_libraries={','.join(missing_libraries) or 'none'}")
    print(f"extension_loaded={args.load_extension}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Mooncake verification failed: {exc}", file=sys.stderr)
        raise

