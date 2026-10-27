"""Responsibilities: Compile/load the measured serial AOT path on its actual serving GPU.
Implementation: Startup compilation writes one self-contained package and a content/hardware
contract; each live allocation validates that contract and loads one serial callable.
The official pinned Spaces ELF helper clears executable-stack metadata before final packaging;
all other decoded archive bytes must remain identical and both package hashes are retained.
Related Modules: zerogpu.app supplies verified weights/config; runtime exports the fixed model;
live_worker creates an allocation-local GraphRunner. Kaggle binaries are never reused on HF.
Declaration Index: hardware; compiler_link; compatible_package; compile_package;
validate_contract; register_package_cleanup; load_package.
Variable Index: logger — phase/hash/hardware diagnostics; TEMP_ROOT — pinned Torch2.8 loader's
hardcoded /tmp extraction boundary. Package/config/contract are explicit
inputs. No retained GPU callable, implicit recompilation, retry or alternative backend exists.
"""

import hashlib
import json
import logging
import os
import struct
import time
import zipfile
from pathlib import Path

from zerogpu.runtime import configure_runtime, export_program

logger = logging.getLogger(__name__)
TEMP_ROOT = Path("/tmp")


def hardware():
    """Functionality: Identify the real allocated CUDA device and executable runtime.
    Inputs: initialized current Torch CUDA allocation. Outputs: comparable hardware dictionary.
    Logic: query versions, device capability/name and multiprocessor count.
    Constraints: called only inside an allocated GPU function; unavailable CUDA is an error,
    never evidence of an emulated/CPU execution path.
    """
    import torch

    if torch.__version__.split("+")[0] != "2.8.0" or not torch.cuda.is_available():
        raise RuntimeError("Serving AOT requires pinned Torch2.8.0 and a real CUDA allocation")
    properties = torch.cuda.get_device_properties(torch.cuda.current_device())
    return {"torch": torch.__version__, "cuda": torch.version.cuda,
            "gpu": properties.name, "capability": list(torch.cuda.get_device_capability()),
            "multiprocessors": properties.multi_processor_count}


def compiler_link(directory):
    """Functionality: Make the already loaded CUDA driver discoverable by the C++ linker.
    Inputs: owned package directory and Linux /proc/self/maps after CUDA initialization.
    Outputs: explicit LIBRARY_PATH mutation and the verified driver path.
    Logic: require one real loaded libcuda.so mapping, create/check an owned libcuda.so symlink;
    prepend that directory while retaining existing library search paths.
    Constraints: no stub library, download, deletion, privileged modification or guessed driver;
    existing conflicting links fail explicitly. The same boundary is recorded in Kaggle setup.
    """
    drivers = {Path(line.split()[-1]) for line in Path("/proc/self/maps").read_text().splitlines()
               if "/libcuda.so" in line and line.split()[-1].startswith("/")}
    drivers = {path.resolve(strict=True) for path in drivers}
    if len(drivers) != 1:
        raise RuntimeError(f"Expected one loaded CUDA driver, found {len(drivers)}")
    driver = drivers.pop()
    link_directory = directory / "cuda-link"
    link_directory.mkdir(parents=True, exist_ok=True)
    link = link_directory / "libcuda.so"
    if link.is_symlink() or link.exists():
        if not link.is_symlink() or link.resolve(strict=True) != driver:
            raise RuntimeError("Owned CUDA compiler link differs from the loaded driver")
    else:
        link.symlink_to(driver)
    prior = os.environ.get("LIBRARY_PATH", "")
    os.environ["LIBRARY_PATH"] = str(link_directory) + (os.pathsep + prior if prior else "")
    logger.info("AOT linker configured loaded_driver=%s link=%s", driver, link)
    return str(driver)


def compatible_package(source, destination, clear_stack):
    """Functionality: Apply the pinned official Spaces ELF compatibility change to a stock package.
    Inputs: original/destination package Paths and trusted spaces.zero.elf.clear_elf_execstack.
    Outputs: auditable member changes and both package hashes; destination archive written.
    Logic: copy decoded members; for each host shared library, check one GNU_STACK header,
    call the official helper on an owned temporary file, verify only the execute flag changed.
    Constraints: preserve weights/code/other metadata bytes; no extraction of archive paths,
    arbitrary native patch, loading or architecture conversion. Original package remains evidence.
    """
    if source.resolve() == destination.resolve():
        raise ValueError("Original and compatible package paths must differ")
    if not 0 < source.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError("Original AOT package exceeds the model asset boundary")
    libraries = []
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as compatible:
        for member in original.infolist():
            raw = original.read(member)
            updated = raw
            if member.filename.endswith(".so"):
                if raw[:6] != b"\x7fELF\x02\x01":
                    raise ValueError("AOT compatibility expects a little-endian ELF64 library")
                offset = struct.unpack_from("<Q", raw, 32)[0]
                entry_size, count = struct.unpack_from("<HH", raw, 54)
                stack = [offset + index * entry_size for index in range(count)
                         if struct.unpack_from("<I", raw, offset + index * entry_size)[0]
                         == 0x6474E551]
                if len(stack) != 1:
                    raise ValueError("AOT library must contain one explicit GNU_STACK header")
                flags_offset = stack[0] + 4
                before = struct.unpack_from("<I", raw, flags_offset)[0]
                temporary = destination.parent / f"elf-audit-{len(libraries)}.so"
                temporary.write_bytes(raw)
                changed = clear_stack(temporary)
                updated = temporary.read_bytes()
                temporary.unlink()
                expected = bytearray(raw)
                struct.pack_into("<I", expected, flags_offset, before & ~1)
                if updated != expected or changed != bool(before & 1):
                    raise RuntimeError("Spaces ELF helper changed bytes outside the execute flag")
                libraries.append({"member": member.filename, "flags_before": before,
                                  "flags_after": before & ~1, "changed": changed,
                                  "flags_offset": flags_offset,
                                  "original_sha256": hashlib.sha256(raw).hexdigest(),
                                  "compatible_sha256": hashlib.sha256(updated).hexdigest()})
            compatible.writestr(member, updated)
    if not libraries:
        raise ValueError("Stock AOT package contains no auditable shared library")
    result = {"source_package_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "package_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
              "libraries": libraries, "scope": "GNU_STACK execute flag only; other bytes unchanged"}
    logger.info("AOT ELF compatibility completed evidence=%s", result)
    return result


def compile_package(model, manifest, config, directory):
    """Functionality: Package the selected fixed-shape model during a startup GPU allocation.
    Inputs: already verified fused CUDA model, original manifest, explicit measured AOT config,
    owned output Path. Outputs: package descriptor containing path and auditable contract.
    Logic: real hardware/linker checks; same precision/layout export and stock Torch packaging
    as the measured candidate; apply pinned Spaces ELF compatibility with byte verification;
    save original/final package/config/weight/runtime hashes and setup time.
    Constraints: no JIT, Spaces helper mutation, target architecture guess or foreign binary;
    compilation and file errors propagate. Startup allocation is separate from live requests.
    """
    import torch

    started = time.monotonic()
    if (not config.get("aoti") or not config.get("aoti_single_threaded")
            or config.get("compile") or config.get("spaces_aoti")):
        raise ValueError("Explicit measured stock serial AOT configuration required")
    actual = hardware()
    from importlib.metadata import version

    from spaces.zero.elf import clear_elf_execstack

    if version("spaces") != "0.51.3":
        raise RuntimeError("AOT ELF compatibility requires pinned Spaces0.51.3")
    directory.mkdir(parents=True, exist_ok=True)
    driver = compiler_link(directory)
    inputs = torch.zeros((1, 3, 640, 640), device="cuda", dtype=getattr(torch, config["dtype"]))
    if config.get("layout"):
        inputs = inputs.contiguous(memory_format=torch.channels_last)
    logger.info("Target AOT export start hardware=%s config=%s stride=%s",
                actual, config, list(inputs.stride()))
    exported = export_program(model, inputs)
    tensor_contract = {name: {"dtype": str(value.dtype), "shape": list(value.shape),
                              "stride": list(value.stride()), "device": str(value.device)}
                       for name, value in {**exported.state_dict, **exported.constants}.items()
                       if isinstance(value, torch.Tensor)}
    (directory / "export-contract.json").write_text(
        json.dumps(tensor_contract, indent=2), encoding="utf-8"
    )
    package = directory / "inference.pt2"
    original_package = directory / "inference-original.pt2"
    logger.info("Target AOT export completed tensors=%d; stock package compile start",
                len(tensor_contract))
    torch._inductor.aoti_compile_and_package(exported, package_path=str(original_package))
    elf_contract = compatible_package(original_package, package, clear_elf_execstack)
    contract = {"schema_version": 1, "source_sha256": manifest["source_sha256"],
                "config": dict(config), "hardware": actual, "driver": driver,
                "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "package_bytes": package.stat().st_size,
                "elf_compatibility": {"spaces": version("spaces"), **elf_contract},
                "export_contract_sha256": hashlib.sha256(
                    (directory / "export-contract.json").read_bytes()).hexdigest(),
                "compile_ms": (time.monotonic() - started) * 1000}
    (directory / "package-contract.json").write_text(
        json.dumps(contract, indent=2, allow_nan=False), encoding="utf-8"
    )
    logger.info("Target AOT package completed contract=%s", contract)
    return {"path": str(package), "contract": contract}


def validate_contract(contract, manifest, config, actual, digest, size):
    """Functionality: Reject incompatible/corrupted packages before native loading.
    Inputs: recorded contract, pinned manifest/config, actual hardware, package digest/byte count.
    Outputs: None or ValueError. Logic: exact identity/config/runtime/hardware/hash equality.
    Constraints: metadata validation does not prove mAP; no relaxed architecture matching,
    package download, automatic rebuild or alternative inference path.
    """
    if (contract.get("schema_version") != 1
            or contract.get("source_sha256") != manifest["source_sha256"]
            or contract.get("config") != config or contract.get("hardware") != actual
            or contract.get("package_sha256") != digest or contract.get("package_bytes") != size):
        raise ValueError("AOT package identity/configuration/hardware/content contract differs")
    if not config.get("aoti") or not config.get("aoti_single_threaded"):
        raise ValueError("AOT package requires the measured serial loader contract")


def register_package_cleanup(package, maps_before):
    """Functionality: Register this loader's extracted libraries for ZeroGPU parent cleanup.
    Inputs: verified package Path, pre-load mapping names and pinned TEMP_ROOT.
    Outputs: registered directories.
    Logic: identify newly mapped wrapper names present in this exact archive; require the known
    Torch extraction structure under the resolved temporary directory; register each owned root
    through pinned Spaces' cleanup API. Constraints: no deletion here, unrelated mapping cleanup,
    broad glob, or reliance on worker atexit; absence of an attributable mapping fails explicitly.
    """
    from spaces.zero.utils import read_map_files, register_cleanup

    with zipfile.ZipFile(package) as archive:
        wrappers = {Path(name).name for name in archive.namelist()
                    if name.endswith(".wrapper.so")}
    temporary = TEMP_ROOT.resolve()
    roots = set()
    for name, mapped in read_map_files():
        if name in maps_before or mapped.name not in wrappers:
            continue
        resolved = mapped.resolve(strict=True)
        relative = resolved.relative_to(temporary)
        # Torch packages have tmp/root/data/aotinductor/model/wrapper.so (six components).
        if len(relative.parts) != 6 or relative.parts[2:5] != ("data", "aotinductor", "model"):
            raise ValueError("AOT extracted library has an unexpected cleanup boundary")
        root = temporary / relative.parts[0]
        if root.parent != temporary or not root.is_dir():
            raise ValueError("AOT cleanup directory is outside the verified temporary root")
        roots.add(root)
    if not roots:
        raise RuntimeError("AOT loader produced no attributable extracted wrapper mapping")
    for root in sorted(roots):
        register_cleanup(os.getpid(), root)
    logger.info("Allocated AOT cleanup registered pid=%d directories=%s",
                os.getpid(), [str(root) for root in sorted(roots)])
    return roots


def load_package(descriptor, manifest, config):
    """Functionality: Load one compiled callable inside one current live allocation.
    Inputs: startup package descriptor, pinned manifest and measured configuration.
    Outputs: serial AOT callable; logs load time, content hash and actual hardware.
    Logic: hash bounded local package, require exact compile/device contract, select the explicit
    run_single_threaded loader used by the successful capture control; register owned temporary
    archives for Spaces parent cleanup because its GPU workers do not exit gracefully. Establish
    measured floating flags in this allocation; startup child flags do not configure the parent.
    Constraints: never run concurrently or retain the callable across allocations; loading errors
    propagate. External CUDA Graph preparation remains the allocation-local runtime's job.
    """
    import torch
    from spaces.zero.utils import read_map_files

    started = time.monotonic()
    package = Path(descriptor["path"])
    size = package.stat().st_size
    if not 0 < size <= 32 * 1024 * 1024:
        raise ValueError("AOT package exceeds the fixed model asset boundary")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    actual = hardware()
    validate_contract(descriptor["contract"], manifest, config, actual, digest, size)
    configure_runtime(config)
    logger.info("Allocated AOT load start sha256=%s hardware=%s run_single_threaded=True",
                digest, actual)
    maps_before = {name for name, _ in read_map_files()}
    model = torch._inductor.aoti_load_package(str(package), run_single_threaded=True)
    register_package_cleanup(package, maps_before)
    logger.info("Allocated AOT load completed elapsed_ms=%.2f callable=%s",
                (time.monotonic() - started) * 1000, type(model).__name__)
    return model
