"""Responsibilities: Measure explicit CPU acceleration candidates without changing production.
Implementation: One warm-up, ten serial public-image frames; stage/CPU/RSS and output fingerprints.
Related Modules: core.detector; ACCELERATION.md; runs under the same systemd resource limits.
Declaration Index: prepare; make_session; run; main.
Variable Index: PRESETS — explicit allocator/binding/buffer combinations, not automatic fallback.
"""

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

PRESETS = ("baseline", "arena", "binding", "fused", "bounded", "production", "openvino")


def prepare(image, output):
    """Functionality: Reuse input storage with identical FP32 preprocessing.
    Inputs: RGB image, contiguous output [1,3,640,640]; Outputs: tensor/scale/left/top.
    Logic: same Pillow resize/padding; divide uint8 directly into FP32 NCHW destination.
    Constraints: normalization explicitly FP32, unchanged 640/interpolation/padding.
    """
    scale = 640 / max(image.size)
    size = (round(image.width * scale), round(image.height * scale))
    left, top = (640 - size[0]) // 2, (640 - size[1]) // 2
    canvas = Image.new("RGB", (640, 640), (114, 114, 114))
    canvas.paste(image.resize(size, Image.Resampling.BILINEAR), (left, top))
    np.divide(
        np.asarray(canvas).transpose(2, 0, 1)[None], np.float32(255), out=output, dtype=np.float32
    )
    return output, scale, left, top


def make_session(path, preset):
    """Functionality: Build one explicitly selected backend. Inputs: model path and preset.
    Outputs: session/compiled model; Logic: CPU only, one thread and static model contract.
    Constraints: OpenVINO enforces FP32; no backend fallback, retries or production configuration.
    """
    if preset == "openvino":
        import openvino as ov

        return ov.Core().compile_model(
            str(path),
            "CPU",
            {
                "INFERENCE_NUM_THREADS": 1,
                "NUM_STREAMS": 1,
                "PERFORMANCE_HINT": "LATENCY",
                "INFERENCE_PRECISION_HINT": "f32",
            },
        )
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.enable_cpu_mem_arena = preset in ("arena", "fused", "bounded")
    if preset == "bounded":
        memory = ort.OrtMemoryInfo(
            "Cpu", ort.OrtAllocatorType.ORT_ARENA_ALLOCATOR, 0, ort.OrtMemType.DEFAULT
        )
        ort.create_and_register_allocator(
            memory, ort.OrtArenaCfg(128 * 1024 * 1024, 1, 1024 * 1024, 1024 * 1024)
        )
        options.add_session_config_entry("session.use_env_allocators", "1")
    if preset != "baseline":
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def run(arguments):
    """Functionality: Record one experiment, including full decode/render stages.
    Inputs: explicit CLI model/image/preset/output/evaluation paths.
    Outputs: JSON plus public results.
    Logic: warm once, time ten frames; evaluate separate public fixtures and fingerprint raw output.
    Constraints: Linux RSS, synthetic diagnostic sample; not COCO mAP or a concurrency benchmark.
    """
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import django

    django.setup()
    from core.detector import Detector, annotate, decode_boxes, decode_frame, letterbox

    names = json.loads((Path(__file__).parents[1] / "models/yolo26n.json").read_text())["names"]
    model = Detector(str(arguments.model.resolve())) if arguments.preset == "production" else None
    session = model.session if model else make_session(arguments.model, arguments.preset)
    payload = arguments.image.read_bytes()
    tensor = model.input_tensor if model else np.empty((1, 3, 640, 640), dtype=np.float32)
    prediction = model.predictions if model else np.empty((1, 300, 6), dtype=np.float32)
    binding = model.binding if model else None
    if arguments.preset in ("binding", "fused", "bounded"):
        binding = session.io_binding()
        binding.bind_cpu_input(session.get_inputs()[0].name, tensor)
        binding.bind_output(
            session.get_outputs()[0].name,
            "cpu",
            0,
            np.float32,
            prediction.shape,
            prediction.ctypes.data,
        )
    frames = []
    for index in range(11):
        cpu_started, started = time.process_time(), time.perf_counter()
        image = decode_frame(payload)
        decoded = time.perf_counter()
        if model:
            value, scale, left, top = letterbox(image, tensor)
        elif binding is not None or arguments.preset == "openvino":
            value, scale, left, top = prepare(image, tensor)
        else:
            value, scale, left, top = letterbox(image)
        prepared = time.perf_counter()
        if arguments.preset == "openvino":
            raw = session({0: value})[session.output(0)].copy()
        elif binding is not None:
            session.run_with_iobinding(binding)
            raw = prediction
        else:
            raw = session.run(None, {session.get_inputs()[0].name: value})[0]
        inferred = time.perf_counter()
        boxes = decode_boxes(raw, image.size, (scale, left, top), names)
        postprocessed = time.perf_counter()
        encoded = annotate(image, boxes)
        finished = time.perf_counter()
        if index:
            frames.append(
                [
                    1000 * (decoded - started),
                    1000 * (prepared - decoded),
                    1000 * (inferred - prepared),
                    1000 * (postprocessed - inferred),
                    1000 * (finished - postprocessed),
                    1000 * (finished - started),
                    1000 * (time.process_time() - cpu_started),
                ]
            )
    fixture_count = len(boxes)
    fixture_prediction_hash = hashlib.sha256(raw.tobytes()).hexdigest()
    evaluation = {}
    paths = sorted(arguments.evaluation.glob("*.jpg")) if arguments.evaluation else []
    for path in paths:
        image = decode_frame(path.read_bytes())
        value, scale, left, top = letterbox(image)
        if arguments.preset == "openvino":
            raw = session({0: value})[session.output(0)].copy()
        else:
            raw = session.run(None, {session.get_inputs()[0].name: value})[0]
        boxes = decode_boxes(raw, image.size, (scale, left, top), names)
        evaluation[path.name] = {
            "raw_sha256": hashlib.sha256(raw.tobytes()).hexdigest(),
            "boxes": boxes,
        }
    array = np.array(frames)
    result = {
        "preset": arguments.preset,
        "model_sha256": hashlib.sha256(arguments.model.read_bytes()).hexdigest(),
        "model_bytes": arguments.model.stat().st_size,
        "frames": len(frames),
        "stages_mean_ms": dict(
            zip(
                ("decode", "prepare", "infer", "postprocess", "render", "total", "cpu"),
                array.mean(axis=0).round(3).tolist(),
                strict=True,
            )
        ),
        "total_median_ms": float(np.median(array[:, 5])),
        "total_p95_ms": float(np.percentile(array[:, 5], 95)),
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "prediction_sha256": fixture_prediction_hash,
        "jpeg_sha256": hashlib.sha256(encoded).hexdigest(),
        "detections": fixture_count,
        "evaluation": evaluation,
    }
    arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "evaluation"}))


def main():
    """Functionality: Parse an explicit isolated experiment. Inputs: CLI arguments.
    Outputs: report via run, errors terminate; Logic: preset/model/image selection.
    Constraints: no downloading or automatic choice; caller owns resource and dataset boundaries.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--preset", required=True, choices=PRESETS)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evaluation", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
