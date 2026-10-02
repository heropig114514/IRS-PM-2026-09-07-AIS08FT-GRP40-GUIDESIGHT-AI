"""Responsibilities: Offline TensorRT FP32 engine compilation from the pinned YOLO ONNX.
Implementation: Select GPU, disable TF32/low precision, cap workspace, record hardware/source.
Related Modules: core.gpu_detector; tools.laptop_platform; models/yolo26n.json.
Declaration Index: main — parse/verify/build/write.
Variable Index: None; CLI paths and build state are local to main.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from tools.laptop_platform import engine_identity, runtime_profile, select_gpu


def main():
    """Functionality: Build a laptop-specific engine. Inputs: --onnx/--engine, optional --gpu-index.
    Outputs: engine and sidecar JSON; Logic: verify GPU/source, parse, FP32 build and hardware hash.
    Constraints: no download/model changes; 256 MiB tactic workspace, no FP16/INT8/TF32.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--gpu-index", type=int)
    args = parser.parse_args()
    configured = os.environ.get("LAPTOP_GPU_INDEX", "").strip()
    gpu = select_gpu(
        args.gpu_index if args.gpu_index is not None else (int(configured) if configured else None)
    )
    mask = os.environ.get("CUDA_VISIBLE_DEVICES")
    if mask is not None and mask != gpu["uuid"]:
        raise ValueError("CUDA GPU mask differs from selected physical GPU")
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu["uuid"]
    import tensorrt as trt

    if trt.__version__ != runtime_profile(gpu)["trt"]:
        raise ValueError("Installed TensorRT does not match the selected GPU profile")
    manifest = json.loads(
        (Path(__file__).parents[1] / "models/yolo26n.json").read_text(encoding="utf-8")
    )
    source = args.onnx.read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest["onnx_sha256"]:
        raise ValueError("ONNX hash differs from pinned model")
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    onnx_parser = trt.OnnxParser(network, logger)
    if not onnx_parser.parse(source):
        raise RuntimeError(
            "\n".join(str(onnx_parser.get_error(i)) for i in range(onnx_parser.num_errors))
        )
    if network.num_inputs != 1 or network.num_outputs != 1:
        raise ValueError("Unexpected ONNX I/O")
    config = builder.create_builder_config()
    config.clear_flag(trt.BuilderFlag.TF32)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 256 * 1024 * 1024)
    print("Building TensorRT FP32 engine; GPU timing may be affected by other GPU applications.")
    artifact = builder.build_serialized_network(network, config)
    if artifact is None:
        raise RuntimeError("TensorRT engine compilation failed")
    artifact = bytes(artifact)
    args.engine.parent.mkdir(parents=True, exist_ok=True)
    identity, _ = engine_identity(gpu, trt.__version__, manifest["onnx_sha256"])
    metadata = {
        "model": "yolo26n",
        "onnx_sha256": manifest["onnx_sha256"],
        "engine_sha256": hashlib.sha256(artifact).hexdigest(),
        "tensorrt": trt.__version__,
        "precision": "FP32",
        "tf32": False,
        "workspace_mib": 256,
        "input_name": network.get_input(0).name,
        "output_name": network.get_output(0).name,
        "input_shape": list(network.get_input(0).shape),
        "output_shape": list(network.get_output(0).shape),
        "hardware": identity,
    }
    # Stage both files; an interrupted promotion fails the cache-pair or engine-hash check.
    temporary_engine = args.engine.with_suffix(".engine.tmp")
    temporary_metadata = args.engine.with_suffix(".json.tmp")
    try:
        temporary_engine.write_bytes(artifact)
        temporary_metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        temporary_engine.replace(args.engine)
        temporary_metadata.replace(args.engine.with_suffix(".json"))
    finally:
        temporary_engine.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)
    print("TensorRT FP32 engine and hardware-specific metadata written successfully.")


if __name__ == "__main__":
    main()
