"""Responsibilities: Offline TensorRT FP32 engine compilation from the pinned YOLO ONNX.
Implementation: Disable TF32/low precision, cap workspace, record engine/source/version hashes.
Related Modules: core.gpu_detector; models/yolo26n.json; requirements-laptop.txt.
Declaration Index: main — parse/verify/build/write.
Variable Index: None; CLI paths and build state are local to main.
"""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    """Functionality: Build a laptop-specific engine. Inputs: --onnx/--engine CLI paths.
    Outputs: engine and public sidecar JSON; Logic: verify source hash, parse, FP32 build.
    Constraints: no download/model changes; 256 MiB tactic workspace, no FP16/INT8/TF32.
    """
    import tensorrt as trt

    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()
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
    args.engine.write_bytes(artifact)
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
    }
    args.engine.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
