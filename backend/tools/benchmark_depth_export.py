"""Responsibilities: Export and measure native-quarter-resolution YOLO26 depth forward candidates.
Implementation: Fixed-shape FP32 ONNX/TensorRT artifacts; unchanged SDK inputs and postprocessing.
Related Modules: tools.depth_export_runtime; tools.depth_graph_probe; realtime.depth;
tools.fixtures; benchmarks/*/depth-export; DEPTH-EXPORT.md.
Declaration Index: write_json; append_progress; identity; prepare; export_onnx; build_engine;
difference; distribution; measure; benchmark; main.
Variable Index: None. All model and artifact state is explicit in one diagnostic invocation.
"""

import argparse
import gc
import importlib.metadata
import json
import platform
import subprocess
import time
from pathlib import Path

import numpy as np

from realtime.depth_runtime import (
    TrtDepthForward,
    file_hash,
    validate_manifest,
)
from tools.depth_export_runtime import OrtDepthForward


def write_json(path, value):
    """Functionality: Persist progress without losing earlier successful or failed measurements.
    Inputs: report path and JSON-compatible value. Outputs: replaced UTF-8 JSON file.
    Logic: write a sibling temporary file then replace. Constraints: no NaN/Inf or binary payloads;
    inspect manifests only after their phase completes to avoid Windows reader/rename conflicts.
    """
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_progress(path, value):
    """Functionality: Preserve stages without replacing a report a reader may hold open.
    Inputs: final report path and one completed stage record. Outputs: appended JSONL sidecar row.
    Logic: append and close once per stage; publish the final JSON snapshot only at process exit.
    Constraints: no retry; readers must ignore an incomplete trailing line during writes.
    """
    with Path(path).with_suffix(".jsonl").open("a", encoding="utf-8") as target:
        target.write(json.dumps(value, allow_nan=False) + "\n")


def identity():
    """Functionality: Bind artifacts to the observed SDK, CUDA runtime and device family.
    Inputs: installed packages and active CUDA device. Outputs: serializable identity dictionary.
    Logic: record versions and hash the actual SDK depth head/predictor implementations.
    Constraints: engine reuse across different device families or package versions is rejected.
    """
    import torch
    import ultralytics

    sdk = Path(ultralytics.__file__).parent
    props = torch.cuda.get_device_properties(0)
    return {
        "python": platform.python_version(), "platform": platform.platform(),
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(), "ultralytics": ultralytics.__version__,
        "onnx": importlib.metadata.version("onnx"),
        "onnxruntime": importlib.metadata.version("onnxruntime-gpu"),
        "tensorrt": importlib.metadata.version("tensorrt-cu12"),
        "gpu": props.name, "compute_capability": [props.major, props.minor],
        "total_memory": props.total_memory,
        "depth_head_sha256": file_hash(sdk / "nn/modules/head.py"),
        "depth_predictor_sha256": file_hash(sdk / "models/yolo/depth/predict.py"),
    }


def prepare(weights, inputs):
    """Functionality: Load the current GPU-fused SDK model and unchanged benchmark tensors.
    Inputs: local checkpoint and explicit fixture paths. Outputs: estimator and prepared records.
    Logic: initialize official prediction once; use its own BGR letterbox/normalization per image.
    Constraints: depth768, batch1, FP32, TF32 disabled; preserve the max640 JPEG80 fixture path.
    """
    import torch

    from core.detector import decode_frame
    from realtime.depth import DepthEstimator
    from tools.fixtures import fixture_bytes

    if not torch.cuda.is_available():
        raise RuntimeError("A native CUDA device is required")
    torch.cuda.set_device(0)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    estimator = DepthEstimator(weights, "8.4.171")
    estimator.activate()
    records = []
    for index, path in enumerate(inputs):
        jpeg, size = fixture_bytes(path)
        image = decode_frame(jpeg)
        if index == 0:
            estimator.predict(image)
        bgr = np.ascontiguousarray(np.asarray(image)[..., ::-1])
        tensor = estimator.model.predictor.preprocess([bgr])
        import hashlib

        records.append({"path": str(path), "size": list(size), "bgr": bgr, "tensor": tensor,
                        "source_sha256": file_hash(path),
                        "jpeg_sha256": hashlib.sha256(jpeg).hexdigest(),
                        "tensor_sha256": hashlib.sha256(
                            tensor.cpu().numpy().tobytes()).hexdigest()})
    torch.cuda.synchronize()
    return estimator, records


def export_onnx(model, tensor, directory):
    """Functionality: Export exactly the eval forward used by the warmed native SDK.
    Inputs: GPU-fused module, native tensor and fresh artifact directory. Outputs: manifest entry.
    Logic: keep head.export=False and calibration buffers; trace static opset17 then check graph.
    Constraints: no export resize, simplification, dynamic batch, new fusion or reduced precision.
    """
    import onnx
    import torch

    shape = list(tensor.shape)
    head = model.model[-1]
    if head.export or head.training or model.training:
        raise ValueError("Expected native eval depth head with export=False")
    with torch.inference_mode():
        native = model(tensor)
    output_shape = [1, 1, shape[2] // 4, shape[3] // 4]
    if list(native.shape) != output_shape:
        raise ValueError("Native quarter-resolution depth contract changed")
    name = "depth-" + "x".join(map(str, shape)) + ".onnx"
    path = directory / name
    if path.exists():
        raise FileExistsError(path)
    started = time.perf_counter()
    torch.onnx.export(model, tensor, str(path), dynamo=False, opset_version=17,
                      input_names=["images"], output_names=["depth"], dynamic_axes=None,
                      do_constant_folding=True)
    graph = onnx.load(str(path))
    onnx.checker.check_model(graph, full_check=True)
    for value, expected in ((graph.graph.input[0], shape),
                             (graph.graph.output[0], output_shape)):
        actual = [dimension.dim_value for dimension in value.type.tensor_type.shape.dim]
        if actual != expected or value.type.tensor_type.elem_type != onnx.TensorProto.FLOAT:
            raise ValueError("Exported ONNX tensor contract changed")
    return {"input_shape": shape, "output_shape": output_shape, "onnx": name,
            "onnx_sha256": file_hash(path), "export_ms": (time.perf_counter() - started) * 1000,
            "opset": 17, "native_head_export": False,
            "cal_a": head.cal_a.detach().cpu().tolist(),
            "cal_b": head.cal_b.detach().cpu().tolist()}


def build_engine(directory, entry):
    """Functionality: Build one explicit TensorRT FP32 engine without TF32 or auxiliary streams.
    Inputs: checked ONNX artifact and manifest entry. Outputs: engine identity/timing fields.
    Logic: parse static graph, use strong typing, restrict workspace to 1 GiB, serialize once.
    Constraints: no FP16/INT8 flags, rebuild retries, shared tactic cache or automatic deployment.
    """
    import tensorrt as trt

    target = directory / Path(entry["onnx"]).with_suffix(".engine")
    if target.exists():
        raise FileExistsError(target)
    started = time.perf_counter()
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, logger)
    if not parser.parse((directory / entry["onnx"]).read_bytes()):
        raise RuntimeError("TensorRT ONNX parse failed: " + "; ".join(
            str(parser.get_error(index)) for index in range(parser.num_errors)))
    config = builder.create_builder_config()
    config.clear_flag(trt.BuilderFlag.TF32)
    config.max_aux_streams = 0
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 30)
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TensorRT engine build failed; see retained build log")
    target.write_bytes(bytes(serialized))
    return {"engine": target.name, "engine_sha256": file_hash(target),
            "build_ms": (time.perf_counter() - started) * 1000,
            "builder": {"strongly_typed": True, "tf32": False, "workspace_bytes": 1 << 30,
                        "max_aux_streams": 0, "fp16": False, "int8": False}}


def difference(reference, candidate):
    """Functionality: Describe observed numerical differences without inventing quality tolerance.
    Inputs: same-shape Torch tensors. Outputs: exactness, finite counts, absolute/relative errors.
    Logic: transfer outside timing and calculate errors in float64 over mutually finite samples.
    Constraints: nonfinite results remain visible; local agreement is not ground-truth accuracy.
    """
    left, right = reference.detach().cpu().numpy(), candidate.detach().cpu().numpy()
    if left.shape != right.shape or left.dtype != right.dtype:
        raise ValueError("Depth output shape or dtype differs")
    finite = np.isfinite(left) & np.isfinite(right)
    absolute = np.abs(left[finite].astype(np.float64) - right[finite].astype(np.float64))
    nonzero = finite & (left != 0)
    relative = np.abs((right[nonzero].astype(np.float64) - left[nonzero]) / left[nonzero])
    return {"shape": list(left.shape), "exact": bool(np.array_equal(left, right)),
            "reference_nonfinite": int((~np.isfinite(left)).sum()),
            "candidate_nonfinite": int((~np.isfinite(right)).sum()),
            "different_elements": int(np.count_nonzero(left != right)),
            "absolute_m": distribution(absolute), "relative": distribution(relative),
            "relative_excluded_zero_or_nonfinite": int(left.size - nonzero.sum()),
            "quality_accepted": False}


def distribution(values):
    """Functionality: Retain descriptive latency/error statistics over every supplied sample.
    Inputs: numeric sequence. Outputs: count, mean, p50, p95 and maximum or empty-count record.
    Logic: use NumPy percentiles without trimming.
    Constraints: callers exclude only the named first call.
    """
    values = np.asarray(values, dtype=np.float64)
    if not values.size:
        return {"count": 0}
    return {"count": int(values.size), "mean": float(values.mean()),
            "p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95)),
            "max": float(values.max())}


def measure(forward, tensor):
    """Functionality: Separate the first measured call from 30 complete steady forward samples.
    Inputs: prepared callable and identical resident CUDA tensor. Outputs: samples and output.
    Logic: record CUDA events and host submit-to-completion wall time, synchronize each call.
    Constraints: CUDA intervals include launch gaps; setup and SDK pre/postprocessing excluded.
    First call is after backend setup, not a process-cold model load. No samples are discarded.
    """
    import torch

    start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
    rows = []
    with torch.inference_mode():
        for index in range(31):
            torch.cuda.synchronize()
            began = time.perf_counter()
            start.record()
            output = forward(tensor)
            end.record()
            end.synchronize()
            rows.append({"index": index, "cuda_event_ms": start.elapsed_time(end),
                         "wall_ms": (time.perf_counter() - began) * 1000})
    return {"first_call": rows[0], "steady_samples": rows[1:],
            "steady_cuda_event_ms": distribution([row["cuda_event_ms"] for row in rows[1:]]),
            "steady_wall_ms": distribution([row["wall_ms"] for row in rows[1:]])}, output


def benchmark(estimator, records, manifest, directory, report, output):
    """Functionality: Compare each requested backend and verify buffers and complete aligned maps.
    Inputs: initialized SDK, fixtures, checked manifest and report paths. Outputs: report rows.
    Logic: eager before/Graph/ORT/TRT/eager after per fixture, plus mirrored-input ownership checks.
    Constraints: record independent backend failures and continue remaining requested candidates.
    This enumerates diagnostics, never fallback. No accuracy or deployment acceptance is inferred.
    """
    import torch

    from tools.depth_graph_probe import DepthGraphProbe

    predictor = estimator.model.predictor
    for record in records:
        tensor = record["tensor"]
        entry = next(item for item in manifest["artifacts"]
                     if item["input_shape"] == list(tensor.shape))
        row = {key: value for key, value in record.items() if key not in ("bgr", "tensor")}
        row.update(input_shape=list(tensor.shape), backends={})
        report["inputs"].append(row)
        with torch.inference_mode():
            baseline = predictor.inference(tensor).clone()
            aligned = predictor.postprocess(baseline, tensor, [record["bgr"]])[0].depth.data
        for name in ("eager_before", "torch_graph", "onnx_cuda", "tensorrt", "eager_after"):
            print(json.dumps({"phase": "benchmark", "input": record["path"], "backend": name}),
                  flush=True)
            runner = None
            try:
                started = time.perf_counter()
                if name == "torch_graph":
                    runner = DepthGraphProbe(predictor.inference, tensor)
                elif name == "onnx_cuda":
                    runner = OrtDepthForward(directory / entry["onnx"], entry, tensor)
                elif name == "tensorrt":
                    runner = TrtDepthForward(directory / entry["engine"], entry, tensor)
                else:
                    runner = predictor.inference
                torch.cuda.synchronize()
                setup_ms = (time.perf_counter() - started) * 1000
                result, raw = measure(runner, tensor)
                result.update(status="completed", setup_ms=setup_ms,
                              raw_comparison=difference(baseline, raw))
                with torch.inference_mode():
                    candidate = predictor.postprocess(raw, tensor, [record["bgr"]])[0].depth.data
                    saved = raw.clone()
                    changed = tensor.flip(-1).contiguous()
                    expected = predictor.inference(changed)
                    changed_output = runner(changed)
                    result.update(aligned_comparison=difference(aligned, candidate),
                                  mirrored_input_comparison=difference(expected, changed_output),
                                  previous_output_preserved=bool(torch.equal(saved, raw)))
                if not result["previous_output_preserved"]:
                    raise RuntimeError("A later forward overwrote the previous output")
                if name == "onnx_cuda":
                    result["provider_options"] = runner.providers
                    result["completion_contract"] = (
                        "ORT-owned stream; producer synchronized before run; "
                        "bound outputs synchronized before return. Caller CUDA event interval "
                        "includes the completion wait, not isolated kernel duration.")
                if name == "torch_graph":
                    result["graph_evidence"] = runner.evidence
                row["backends"][name] = result
            except Exception as error:
                row["backends"][name] = {"status": "failed", "error_type": type(error).__name__,
                                         "error": str(error)}
                report["failures"].append({"phase": "benchmark", "input": record["path"],
                                           "backend": name, "error": str(error)})
                print(json.dumps(report["failures"][-1]), flush=True)
            finally:
                torch.cuda.synchronize()
                append_progress(output, {"phase": "benchmark", "input": record["path"],
                                         "backend": name, "result": row["backends"][name]})
                if isinstance(runner, OrtDepthForward):
                    runner.close()
                runner = None
                gc.collect()
                torch.cuda.empty_cache()


def main():
    """Functionality: Run an explicitly selected export/build/forward diagnostic phase.
    Inputs: local weights, fixture paths, artifact directory, report path and phase CLI arguments.
    Outputs: checked binaries and structured report; nonzero exit if any requested stage fails.
    Logic: lock provenance, preserve native inputs, persist each stage and retain partial failures.
    Constraints: all requires a new artifact directory; later phases validate hashes before reuse.
    No installation, downloads, retries, truth thresholds or production backend changes occur here.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("all", "export", "build", "benchmark"), default="all")
    parser.add_argument("--stream", choices=("default", "dedicated"), default="default")
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(".jsonl").exists():
        raise FileExistsError("Use a new report path to preserve previous evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = args.artifacts / "manifest.json"
    report = {"status": "running", "phase": args.phase, "inputs": [], "failures": [],
              "scope": "Forward only; FP32; no ground-truth quality or deployment acceptance",
              "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                       text=True).strip(),
              "source_files": {path: file_hash(path) for path in (
                  "tools/benchmark_depth_export.py", "tools/depth_export_runtime.py",
                  "tools/depth_graph_probe.py", "realtime/depth.py", "tools/fixtures.py")},
              "protocol": {"imgsz": 768, "batch": 1, "precision": "FP32", "tf32": False,
                           "stream": args.stream,
                           "first_calls": 1, "steady_calls": 30, "fixture_max_edge": 640,
                           "output": "calibrated quarter-resolution depth; SDK alignment retained"}}
    try:
        estimator, records = prepare(args.weights, args.inputs)
        observed = identity()
        report["identity"] = observed
        if args.phase in ("all", "export"):
            args.artifacts.mkdir(parents=True, exist_ok=False)
            manifest = {"schema": 1, "weights_sha256": file_hash(args.weights),
                        "precision": "FP32", "tf32": False, "identity": observed,
                        "artifacts": []}
            for record in records:
                tensor = record["tensor"]
                if any(entry["input_shape"] == list(tensor.shape)
                       for entry in manifest["artifacts"]):
                    continue
                print(json.dumps({"phase": "export", "shape": list(tensor.shape)}), flush=True)
                entry = export_onnx(estimator.model.predictor.model.model, tensor, args.artifacts)
                manifest["artifacts"].append(entry)
                write_json(manifest_path, manifest)
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest, args.artifacts, args.weights, observed)
        if args.phase in ("all", "build"):
            for entry in manifest["artifacts"]:
                print(json.dumps({"phase": "build", "shape": entry["input_shape"]}), flush=True)
                try:
                    entry.update(build_engine(args.artifacts, entry))
                except Exception as error:
                    report["failures"].append({"phase": "build", "shape": entry["input_shape"],
                                               "error_type": type(error).__name__,
                                               "error": str(error)})
                    print(json.dumps(report["failures"][-1]), flush=True)
                write_json(manifest_path, manifest)
                append_progress(args.output, {"phase": "build", "entry": entry,
                                              "failures": list(report["failures"])})
        report["manifest"] = manifest
        if args.phase in ("all", "benchmark"):
            import torch

            validate_manifest(manifest, args.artifacts, args.weights, observed)
            stream = (torch.cuda.Stream() if args.stream == "dedicated"
                      else torch.cuda.default_stream())
            stream.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(stream):
                benchmark(estimator, records, manifest, args.artifacts, report, args.output)
        report["status"] = "failed" if report["failures"] else "completed"
    except Exception as error:
        report["status"] = "failed"
        report["failures"].append({"phase": args.phase, "error_type": type(error).__name__,
                                   "error": str(error)})
        raise
    finally:
        write_json(args.output, report)
    return int(report["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
