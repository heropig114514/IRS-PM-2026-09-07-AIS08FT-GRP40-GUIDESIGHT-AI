"""Responsibilities: Portable NVIDIA discovery, pinned dependency selection and engine identity.
Implementation: Parse nvidia-smi without CUDA imports, select compatible GPU, fingerprint hardware.
Related Modules: start_laptop.py, tools.build_tensorrt, core.gpu_detector.
Declaration Index: discover_gpus; runtime_profile; select_gpu; engine_identity; cuda_library.
Variable Index: PROFILES — pinned-version policy and supported SM sets, no fallback.
"""

import csv
import hashlib
import importlib.util
import json
import platform
import re
import subprocess
from pathlib import Path

PROFILES = (
    {
        "sm": {"7.5", "8.0", "8.6", "8.9", "9.0"},
        "trt": "10.7.0.post1",
        "cuda": "12.6.77",
        "minimum_cuda": (12, 6),
    },
    {"sm": {"10.0", "12.0"}, "trt": "10.8.0.43", "cuda": "12.8.90", "minimum_cuda": (12, 8)},
)


def discover_gpus():
    """Functionality: Read NVIDIA inventory and driver CUDA capability. Inputs: OS/nvidia-smi.
    Outputs: dictionaries with physical index/name/UUID/SM/driver/free MiB/cuda_capability.
    Logic: bounded subprocesses, CSV/banner parsing; replace localized non-UTF8 banner decoration.
    Constraints: Windows/Linux x64 only; never import CUDA, change drivers or log UUIDs.
    """
    if platform.system() not in ("Windows", "Linux") or platform.machine().lower() not in (
        "amd64",
        "x86_64",
    ):
        raise RuntimeError("Laptop TensorRT supports Windows/Linux x64 in this project")
    try:
        inventory = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,uuid,compute_cap,driver_version,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=15,
        ).stdout
        banner = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "NVIDIA discovery failed; install/check NVIDIA driver and nvidia-smi"
        ) from error
    match = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", banner)
    if not match:
        raise RuntimeError("Cannot determine NVIDIA driver's CUDA capability")
    cuda_capability = tuple(map(int, match.groups()))
    result = []
    for row in csv.reader(inventory.splitlines(), skipinitialspace=True):
        if len(row) != 6:
            raise RuntimeError("Unexpected NVIDIA inventory format")
        index, name, uuid, sm, driver, free = (value.strip() for value in row)
        result.append(
            {
                "index": int(index),
                "name": name,
                "uuid": uuid,
                "sm": sm,
                "driver": driver,
                "free_mib": int(free),
                "cuda_capability": cuda_capability,
            }
        )
    if not result:
        raise RuntimeError("No NVIDIA GPU found; laptop GPU mode cannot start")
    return result


def runtime_profile(gpu):
    """Functionality: Choose pinned TensorRT/CUDA packages. Inputs: GPU SM/driver capability.
    Outputs: profile or actionable error; Logic: exact supported SM, conservative CUDA capability.
    Constraints: no CPU/older-version fallback; driver capability is not installed nvcc version.
    """
    for profile in PROFILES:
        if gpu["sm"] in profile["sm"]:
            if tuple(gpu["cuda_capability"]) < profile["minimum_cuda"]:
                raise RuntimeError(
                    "NVIDIA driver too old for selected CUDA runtime; update it explicitly"
                )
            return profile
    raise RuntimeError(f"GPU compute capability {gpu['sm']} is outside the supported profiles")


def select_gpu(index=None):
    """Functionality: Select physical GPU or first compatible device. Inputs: optional index.
    Outputs: selected GPU dict; Logic: ascending physical indices, validate profile first.
    Constraints: skip unsupported devices only during discovery; selected-device failures stop.
    """
    devices = sorted(discover_gpus(), key=lambda gpu: gpu["index"])
    if index is not None:
        selected = next((gpu for gpu in devices if gpu["index"] == index), None)
        if selected is None:
            raise RuntimeError("Configured NVIDIA GPU index was not found")
        runtime_profile(selected)
        return selected
    for gpu in devices:
        try:
            runtime_profile(gpu)
        except RuntimeError:
            continue
        return gpu
    raise RuntimeError("No compatible NVIDIA GPU/driver pair; check TensorRT profiles and driver")


def engine_identity(gpu, trt_version, onnx_sha256):
    """Functionality: Identify hardware-specific FP32 conditions. Inputs: GPU, TRT, source hash.
    Outputs: identity dictionary and stable cache key; Logic: include UUID/driver/SM/OS/machine.
    Constraints: unchanged FP32/TF32-off/workspace256; only cached local metadata contains UUID.
    """
    identity = {
        "gpu_uuid": gpu["uuid"],
        "gpu_sm": gpu["sm"],
        "gpu_name": gpu["name"],
        "driver": gpu["driver"],
        "os": platform.system(),
        "machine": platform.machine(),
        "tensorrt": trt_version,
        "onnx_sha256": onnx_sha256,
        "precision": "FP32",
        "tf32": False,
        "workspace_mib": 256,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return identity, key


def cuda_library():
    """Functionality: Locate installed CUDA runtime library. Inputs: package installation and OS.
    Outputs: absolute DLL/SO Path; Logic: inspect nvidia.cuda_runtime package search location.
    Constraints: exact runtime filename; no global PATH change or alternative CUDA installation.
    """
    spec = importlib.util.find_spec("nvidia.cuda_runtime")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("CUDA runtime package missing; run start_laptop.py --setup")
    directory = Path(next(iter(spec.submodule_search_locations)))
    name = "bin/cudart64_12.dll" if platform.system() == "Windows" else "lib/libcudart.so.12"
    library = directory / name
    if not library.is_file():
        raise RuntimeError("Installed CUDA runtime library missing; check isolated dependencies")
    return library
