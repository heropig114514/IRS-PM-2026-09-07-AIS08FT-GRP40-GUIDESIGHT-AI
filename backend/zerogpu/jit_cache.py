"""Responsibilities: Reuse the T4-selected JIT configuration across bounded GPU allocations.
Implementation: Compile once on the real startup device; serialize compiler artifacts and retain
owned Inductor/Triton disk caches. A fresh worker validates identity, restores the artifacts,
resets allocation-local frontend state, requires an FX graph cache hit and prohibits code generation
before returning a compiled model; HF may reuse a worker PID across allocations.
Related Modules: runtime.load_model/GraphRunner; app startup; live_worker; aoti hardware/linker.
Declaration Index: configure_cache; compiler_state; cache_hits; validate_cache_contract;
initialize_compiled; prepare_cache; load_cache; load_cache.reject_codegen.
Variable Index: logger — phase/hash/cache diagnostics; MAX_CACHE_BYTES — bounded artifact read.
Config compile/default, FP16, fusion, layout and buffers are inherited unchanged from T4.
"""

import hashlib
import importlib
import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import patch

from zerogpu.aoti import compiler_link, hardware
from zerogpu.runtime import configure_runtime, load_model

logger = logging.getLogger(__name__)
MAX_CACHE_BYTES = 64 * 1024 * 1024


def configure_cache(directory):
    """Functionality: Select owned persistent compiler storage before importing Inductor.
    Inputs: owned root Path. Outputs: None; creates cache subdirectories and process env entries.
    Logic: keep Inductor and Triton files on the same application disk across worker forks;
    use one compilation thread and enable the FX cache explicitly.
    Constraints: these affect compilation/storage, not model numerics; no deletion or fallback.
    Call before Spaces/Torch imports in the parent and before compiler imports in fresh probes.
    """
    root = Path(directory).resolve()
    for variable, name in (("TORCHINDUCTOR_CACHE_DIR", "inductor"),
                           ("TRITON_CACHE_DIR", "triton")):
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(target)
    os.environ["TORCHINDUCTOR_COMPILE_THREADS"] = "1"
    os.environ["TORCHINDUCTOR_FX_GRAPH_CACHE"] = "1"


def compiler_state():
    """Functionality: Snapshot compiler counters without model tensors or artifact contents.
    Inputs: current process's Torch compiler state. Outputs: JSON-compatible nested counts.
    Logic: copy counters; Constraints: measurements are diagnostic, not a substitute for AP.
    """
    from torch._dynamo.utils import counters

    return {group: dict(values) for group, values in counters.items()}


def cache_hits(state):
    """Functionality: Require observed restoration rather than accepting silent compilation.
    Inputs: compiler counter snapshot. Outputs: None or RuntimeError.
    Logic: require an Inductor FX hit and reject miss/bypass/disabled counters.
    Constraints: a hit alone does not verify output quality; independent output probes do that.
    """
    counts = state.get("inductor", {})
    if (counts.get("fxgraph_cache_hit", 0) < 1 or
            any(counts.get(key, 0) for key in
                ("fxgraph_cache_miss", "fxgraph_cache_bypass", "fxgraph_cache_disabled"))):
        raise RuntimeError(f"JIT restore did not exclusively hit the compiled FX cache: {state}")


def validate_cache_contract(contract, manifest, config, actual, digest, size, triton):
    """Functionality: Reject a cache compiled for different model/runtime/device/configuration.
    Inputs: saved metadata, manifest/config, actual hardware, bytes digest/size, Triton version.
    Outputs: None or ValueError. Logic: exact comparisons before artifact deserialization.
    Constraints: cache files are locally generated trusted artifacts; no portability guesses,
    relaxed GPU matching, network fetch, automatic rebuilding or alternative backend.
    """
    if (contract.get("schema_version") != 1 or
            contract.get("source_sha256") != manifest["source_sha256"] or
            contract.get("config") != config or contract.get("hardware") != actual or
            contract.get("triton") != triton or contract.get("sha256") != digest or
            contract.get("bytes") != size):
        raise ValueError("JIT cache identity/configuration/hardware/content contract differs")
    if (config.get("compile") != "default" or config.get("dtype") != "float16" or
            not config.get("layout") or not config.get("fuse") or not config.get("graph") or
            config.get("aoti") or config.get("tf32")):
        raise ValueError("JIT cache requires the explicit T4-selected FP16 channels-last Graph")


def initialize_compiled(weights, manifest, config):
    """Functionality: Reconstruct exactly the selected fused JIT model before graph capture.
    Inputs: pinned weights, manifest and unchanged T4 compile configuration.
    Outputs: compiled module and one owned CUDA example tensor.
    Logic: shared verified loader; initialize fixed-shape anchors before Dynamo tracing, as the
    T4 runner did; normalize cached anchor strides exactly as that runner;
    fullgraph/static/default compilation; execute once under inference_mode.
    Constraints: only CUDA/FP16/channels-last; original weights/head and FP32 output contract;
    caller controls cold compilation versus cache-only restoration and synchronizes timing.
    """
    import torch

    configure_runtime(config)
    model = load_model(weights, manifest, "cuda", config)
    example = torch.zeros((1, 3, 640, 640), device="cuda", dtype=torch.float16)
    example = example.contiguous(memory_format=torch.channels_last)
    with torch.inference_mode():
        model(example)
        model.model[-1].anchors = model.model[-1].anchors.contiguous()
        compiled = torch.compile(model, fullgraph=True, dynamic=False, mode="default")
        output = compiled(example)
    if output.shape != (1, 300, 6) or output.device.type != "cuda":
        raise ValueError("Restored compiled model has an invalid prediction contract")
    torch.cuda.synchronize()
    return compiled, example


def prepare_cache(weights, manifest, config, directory):
    """Functionality: Compile/cache the selected model once on the actual startup GPU.
    Inputs: pinned assets/config and owned directory. Outputs: portable artifact descriptor.
    Logic: establish caches/linker, clear compiler counters, cold compile and serialize all
    supported artifacts; retain Triton disk files as well; record hashes/runtime/cost/counters.
    Constraints: called only in startup GPU allocation; no foreign T4 binary or public API;
    failure aborts readiness, without precision/backend substitution.
    """
    import torch
    import triton
    from torch._dynamo.utils import counters

    started = time.monotonic()
    directory = Path(directory)
    configure_cache(directory)
    actual = hardware()
    compiler_link(directory / "cuda-link")
    counters.clear()
    logger.info("Target JIT cold compilation start hardware=%s config=%s", actual, config)
    model, _ = initialize_compiled(weights, manifest, config)
    artifacts = torch.compiler.save_cache_artifacts()
    if artifacts is None:
        raise RuntimeError("JIT compilation returned no serializable compiler artifacts")
    data, info = artifacts
    if not 0 < len(data) <= MAX_CACHE_BYTES or not info.inductor_artifacts:
        raise RuntimeError("JIT compiler artifacts are empty, oversized or lack an FX graph")
    path = directory / "inference.cache"
    path.write_bytes(data)
    contract = {"schema_version": 1, "source_sha256": manifest["source_sha256"],
                "config": config, "hardware": actual, "triton": triton.__version__,
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "artifacts": dict(info.artifacts), "compiler_state": compiler_state(),
                "compile_ms": (time.monotonic() - started) * 1000}
    (directory / "cache-contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    logger.info("Target JIT cache ready contract=%s", contract)
    del model
    return {"path": str(path), "directory": str(directory.resolve()), "contract": contract}


def load_cache(descriptor, weights, manifest, config):
    """Functionality: Restore a compiled callable in a fresh allocated GPU process.
    Inputs: startup descriptor, pinned weights/manifest and selected config.
    Outputs: compiled module and restoration evidence including first-call cost.
    Logic: validate bytes/device/runtime; reset official Dynamo frontend state (a reused HF worker
    can otherwise skip FX lookup and its counters); retain disk artifacts and restore official APIs;
    temporarily reject the
    pinned Torch2.8 code-generation entry point so a cache miss raises before kernel generation;
    reconstruct/warm, audit FX hit counters, then forbid later Dynamo recompilation.
    Constraints: frontend tracing/loading are allowed; new backend code generation and eager
    fallback are not. Compiled callable/Graph stay allocation-local; disk caches persist.
    """
    import torch
    import triton
    from torch._dynamo.utils import counters

    started = time.monotonic()
    configure_cache(descriptor["directory"])
    path = Path(descriptor["path"])
    size = path.stat().st_size
    if not 0 < size <= MAX_CACHE_BYTES:
        raise ValueError("JIT cache artifact size is outside the owned asset boundary")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    actual = hardware()
    logger.info("Allocated JIT identity check pid=%d hardware=%s sha256=%s bytes=%d",
                os.getpid(), actual, digest, size)
    validate_cache_contract(descriptor["contract"], manifest, config, actual,
                            digest, size, triton.__version__)
    # ZeroGPU may reuse a process: an earlier compiled code object could bypass Inductor lookup
    # after counters.clear(), yielding neither a hit nor a miss. Reset frontend state deliberately
    # so every allocation verifies disk-backed restoration; model configuration/numerics and
    # the no-codegen guard remain unchanged. Re-enable tracing only during this guarded phase.
    torch.compiler.set_stance("default")
    torch.compiler.reset()
    logger.info("Allocated JIT frontend reset pid=%d; persistent compiler artifacts retained",
                os.getpid())
    info = torch.compiler.load_cache_artifacts(data)
    if info is None or not info.inductor_artifacts:
        raise RuntimeError("Official compiler cache restoration produced no Inductor artifacts")
    counters.clear()
    module = importlib.import_module("torch._inductor.compile_fx")

    def reject_codegen(*args, **kwargs):
        """Functionality: Reject unexpected code generation during the restoration call.
        Inputs: opaque pinned Torch2.8 code-generation arguments. Outputs: RuntimeError.
        Logic: log counters then fail; Constraints: never compile, retry or choose eager mode.
        """
        logger.error("JIT cache restore attempted new code generation state=%s", compiler_state())
        raise RuntimeError("JIT cache miss: live backend code generation is forbidden")

    logger.info("Allocated JIT cache restore start pid=%d sha256=%s hardware=%s",
                os.getpid(), digest, actual)
    with patch.object(module, "fx_codegen_and_compile", reject_codegen):
        model, _ = initialize_compiled(weights, manifest, config)
    state = compiler_state()
    cache_hits(state)
    torch.compiler.set_stance("fail_on_recompile")
    evidence = {"cache_sha256": digest, "cache_restore_ms": (time.monotonic()-started)*1000,
                "compiler_state": state, "new_backend_codegen_forbidden": True,
                "triton": triton.__version__, "pid": os.getpid()}
    logger.info("Allocated JIT cache restored evidence=%s", evidence)
    return model, evidence
