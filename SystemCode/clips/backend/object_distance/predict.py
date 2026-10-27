"""Responsibilities: Provide explicit offline E inference with truthful synchronized stage timings.
Implementation: One shared YOLO forward, canonical detection decoding, ROI regression, null
boundaries.
Related Modules: object_distance.model/data/evaluate; no production tracker, risk or depth import.
Declaration Index: synchronize; decode_detections; run_frame; predict_image; latency_summary; main.
Variable Index: None. distance_target is camera-reference z, not safety-certified obstacle range.
"""

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from object_distance.config import digest, seed_runtime, write_json
from object_distance.data import prepare_image
from object_distance.model import load_checkpoint


def synchronize(device):
    """Functionality: Close asynchronous CUDA timing boundaries only on the selected device.
    Inputs: Torch device/text. Outputs: None. Logic: synchronize CUDA; CPU executes synchronously.
    Constraints: never switches devices or enables a fallback.
    """
    if torch.device(device).type == "cuda":
        torch.cuda.synchronize(device)


def decode_detections(rows, transform, names, confidence):
    """Functionality: Preserve original YOLO e2e detections in original image coordinates.
    Inputs:300x6 predictions, exact transform, class names, confidence. Outputs: canonical boxes.
    Logic: validate numeric rows/classes, filter threshold, invert letterbox and clip.
    Constraints: no extra NMS or relabeling; unsupported distance categories remain detections.
    """
    if rows.shape != (300, 6) or not torch.isfinite(rows).all():
        raise ValueError("Invalid YOLO26 detection rows")
    rows = rows.detach().float().cpu()
    if (
        (rows[:, 4] < 0)
        | (rows[:, 4] > 1)
        | (rows[:, 5] < 0)
        | (rows[:, 5] >= len(names))
        | (rows[:, 5] != rows[:, 5].trunc())
    ).any():
        raise ValueError("Invalid YOLO confidence/class")
    rows = rows[rows[:, 4] >= confidence]
    boxes = transform.to_original(rows[:, :4])
    results = []
    for row, bbox in zip(rows.tolist(), boxes.tolist(), strict=True):
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        identity = int(row[5])
        results.append(
            {
                "class_id": identity,
                "label": names[identity],
                "score": row[4],
                "xyxy": bbox,
                "class_name": names[identity],
                "confidence": row[4],
                "bbox": bbox,
            }
        )
    return results


@torch.no_grad()
def run_frame(model, tensor, transform, *, gt_boxes=None, gt_classes=None, started=None):
    """Functionality: Infer E detections and GT-or-predicted-ROI distances from one forward.
    Inputs: prepared CHW tensor/transform, optional original GT boxes/head class IDs and start
    clock.
    Outputs: canonical boxes and synchronized timing fields. Logic: one extract, one regress call;
    absent classes/invalid predictions become null; AMP follows checkpoint's explicit setting.
    Constraints: GT mode is labeled separately by evaluation; never routes distances into risk.
    """
    config = model.config
    started = time.perf_counter() if started is None else started
    images = tensor.unsqueeze(0).to(config.device)
    synchronize(config.device)
    detect_started = time.perf_counter()
    with torch.autocast(
        torch.device(config.device).type,
        enabled=config.amp and torch.device(config.device).type == "cuda",
    ):
        prediction, features = model.extract(images)
    synchronize(config.device)
    detected_at = time.perf_counter()
    if gt_boxes is None:
        boxes = decode_detections(prediction[0], transform, model.names, config.confidence)
        original = torch.tensor([box["bbox"] for box in boxes], dtype=torch.float32).reshape(-1, 4)
        classes = torch.tensor(
            [
                config.supported_classes.index(box["class_name"])
                if box["class_name"] in config.supported_classes
                and (
                    config.distance_target != "sanpo_visible_surface_median_m"
                    or box["class_name"] == "person"
                )
                else -1
                for box in boxes
            ],
            dtype=torch.long,
        )
    else:
        original, classes = gt_boxes, gt_classes
        boxes = [
            {"class_name": config.supported_classes[int(category)], "confidence": 1.0, "bbox": bbox}
            for bbox, category in zip(original.tolist(), classes, strict=True)
        ]
    rois = torch.cat((torch.zeros((len(original), 1)), transform.to_input(original)), 1)
    rois, classes = rois.to(config.device), classes.to(config.device)
    synchronize(config.device)
    head_started = time.perf_counter()
    with torch.autocast(
        torch.device(config.device).type,
        enabled=config.amp and torch.device(config.device).type == "cuda",
    ):
        distance_tensor = model.regress(features, rois, classes, images.shape[-2:])
    synchronize(config.device)
    head_finished = time.perf_counter()
    distances = distance_tensor.detach().float().cpu().tolist()
    for box, distance in zip(boxes, distances, strict=True):
        box.update(
            distance_m=distance if math.isfinite(distance) and distance > 0 else None,
            distance_source="object_distance_head",
        )
    synchronize(config.device)
    finished = time.perf_counter()
    latency = {
        "detection_ms": (detected_at - detect_started) * 1000,
        "distance_ms": (finished - detected_at) * 1000,
        "distance_head_ms": (head_finished - head_started) * 1000,
        "end_to_end_ms": (finished - started) * 1000,
        "fps": 1 / (finished - started),
    }
    return boxes, latency


def predict_image(model, image):
    """Functionality: Predict canonical E objects from one RGB image.
    Inputs: loaded E model and PIL RGB. Outputs: objects and timing. Logic: preprocess then one
    call.
    Constraints: supports arbitrary original image size; no model initialization or fallback here.
    """
    synchronize(model.config.device)
    started = time.perf_counter()
    tensor, transform = prepare_image(image, model.config.image_size)
    return run_frame(model, tensor, transform, started=started)


def latency_summary(samples):
    """Functionality: Summarize explicitly warm steady-frame processing measurements.
    Inputs: list of stage timing dictionaries. Outputs: mean/P50/P95 per stage and aggregate FPS.
    Logic: NumPy percentiles, frames/total processing time. Constraints: excludes separate cold
    probe.
    """
    result = {"frames": len(samples)}
    for key in ("detection_ms", "distance_ms", "distance_head_ms", "end_to_end_ms"):
        values = np.asarray([sample[key] for sample in samples], dtype=float)
        result[key] = (
            {
                "mean": float(values.mean()),
                "p50": float(np.percentile(values, 50)),
                "p95": float(np.percentile(values, 95)),
            }
            if len(values)
            else None
        )
    result["fps"] = 1000 / result["end_to_end_ms"]["mean"] if samples else None
    return result


def main(argv=None):
    """Functionality: Run explicit offline E image prediction with cold and warm measurements.
    Inputs: checkpoint, RGB path, optional detector relocation/device and new JSON output.
    Outputs: canonical objects, target definition, parameters, timing and memory JSON.
    Logic: first-call probe then explicit warmups/repeats. Constraints: never changes A/B/C
    settings.
    """
    parser = argparse.ArgumentParser(description="Offline scheme E object camera-z inference")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--detection-weights", type=Path)
    parser.add_argument("--device")
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeat", type=int, default=10)
    args = parser.parse_args(argv)
    if args.output.exists() or args.warmup < 0 or args.repeat < 1:
        parser.error("Use a new output, nonnegative warmup and positive repeat count")
    loaded_at = time.perf_counter()
    model, _ = load_checkpoint(
        args.checkpoint, detection_weights=args.detection_weights, device=args.device
    )
    model.eval()
    seed_runtime(model.config)
    load_ms = (time.perf_counter() - loaded_at) * 1000
    with Image.open(args.input) as source:
        image = source.convert("RGB")
    _, cold = predict_image(model, image)
    for _ in range(args.warmup):
        predict_image(model, image)
    if torch.device(model.config.device).type == "cuda":
        torch.cuda.reset_peak_memory_stats(model.config.device)
    measurements = []
    for _ in range(args.repeat):
        objects, latency = predict_image(model, image)
        measurements.append(latency)
    peak = (
        torch.cuda.max_memory_allocated(model.config.device)
        if torch.device(model.config.device).type == "cuda"
        else None
    )
    report = {
        "distance_mode": "object_distance",
        "distance_target": model.config.distance_target,
        "objects": objects,
        "parameters": model.parameter_counts(),
        "config": asdict(model.config),
        "inference_batch_size": 1,
        "input_sha256": digest(args.input),
        "preprocessing": "original_RGB_bilinear_letterbox114_FP32",
        "torch": str(torch.__version__),
        "load_ms": load_ms,
        "cold_first_call": cold,
        "warmup_calls": args.warmup,
        "steady_latency": latency_summary(measurements),
        "peak_gpu_memory_bytes": peak,
        "detector_sha256": model.weight_sha256,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    main()
