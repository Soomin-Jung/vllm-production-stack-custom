#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import pathlib
import re
import subprocess
import sys

PACKAGE_NAMES = (
    "mooncake-transfer-engine",
    "mooncake-transfer-engine-cuda13",
)
EXPECTED_TOKENS = (b"nvlink_intra", b"nvlink")
ALLOWED_BUILD_TIME_MISSING_LIBRARIES = {"libcuda.so.1", "libnvidia-ml.so.1"}
DEFAULT_LOCK_FILE = pathlib.Path("/opt/mooncake-build-info/SOURCE_LOCK.env")


def read_shell_env(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def cuda_contract() -> tuple[str, int, str]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("torch is required to identify the vLLM CUDA ABI") from exc

    cuda_version = torch.version.cuda
    if not cuda_version:
        raise RuntimeError("torch.version.cuda is empty")
    try:
        cuda_major = int(cuda_version.split(".", 1)[0])
    except ValueError as exc:
        raise RuntimeError(f"cannot parse torch CUDA version: {cuda_version}") from exc

    if cuda_major == 12:
        expected_package = "mooncake-transfer-engine"
    elif cuda_major == 13:
        expected_package = "mooncake-transfer-engine-cuda13"
    else:
        raise RuntimeError(f"unsupported CUDA major {cuda_major}")

    return cuda_version, cuda_major, expected_package


def installed_distribution() -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for package_name in PACKAGE_NAMES:
        try:
            matches.append((package_name, importlib.metadata.version(package_name)))
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
        raise RuntimeError(
            f"expected one Mooncake engine shared object, got {candidates}"
        )
    return candidates[0]


def verify_runtime_dependencies(lock: dict[str, str]) -> None:
    deps = lock.get("MOONCAKE_RUNTIME_DEPS", "").split()
    missing: list[str] = []
    for module_name in deps:
        if importlib.util.find_spec(module_name) is None:
            missing.append(module_name)
    if missing:
        raise RuntimeError(f"missing Mooncake runtime dependencies: {missing}")


def verify_linkage(path: pathlib.Path, cuda_major: int) -> tuple[list[str], str]:
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

    cudart = re.findall(r"libcudart\.so\.(\d+)", result.stdout)
    if not cudart:
        raise RuntimeError(f"libcudart dependency not found in ldd output for {path}")
    linked_majors = sorted(set(int(value) for value in cudart))
    if linked_majors != [cuda_major]:
        raise RuntimeError(
            f"CUDA ABI mismatch: vLLM/torch CUDA major={cuda_major}, "
            f"Mooncake libcudart majors={linked_majors}"
        )
    return sorted(set(missing)), f"libcudart.so.{cuda_major}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--load-extension",
        action="store_true",
        help="Import mooncake.engine; use this in a GPU-enabled runtime pod.",
    )
    parser.add_argument(
        "--lock-file",
        type=pathlib.Path,
        default=DEFAULT_LOCK_FILE,
        help="Source profile copied into the image at build time.",
    )
    args = parser.parse_args()

    if not args.lock_file.is_file():
        raise RuntimeError(f"Mooncake source lock not found: {args.lock_file}")
    lock = read_shell_env(args.lock_file)
    expected_version = lock.get("MOONCAKE_VERSION")
    if not expected_version:
        raise RuntimeError(f"MOONCAKE_VERSION missing from {args.lock_file}")

    cuda_version, cuda_major, expected_package = cuda_contract()
    package_name, version = installed_distribution()
    if package_name != expected_package:
        raise RuntimeError(
            f"expected distribution {expected_package} for CUDA {cuda_version}, "
            f"got {package_name}"
        )
    if version != expected_version:
        raise RuntimeError(f"expected Mooncake {expected_version}, got {version}")

    verify_runtime_dependencies(lock)

    path = engine_path()
    payload = path.read_bytes()
    missing_tokens = [
        token.decode() for token in EXPECTED_TOKENS if token not in payload
    ]
    if missing_tokens:
        raise RuntimeError(f"transport markers missing from {path}: {missing_tokens}")

    missing_libraries, cudart = verify_linkage(path, cuda_major)

    if args.load_extension:
        importlib.import_module("mooncake.engine")

    print(f"distribution={package_name}")
    print(f"version={version}")
    print(f"torch_cuda={cuda_version}")
    print(f"linked_cudart={cudart}")
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
