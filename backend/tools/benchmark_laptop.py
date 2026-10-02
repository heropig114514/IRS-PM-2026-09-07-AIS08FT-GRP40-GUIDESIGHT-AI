"""Responsibilities: Small public-image TensorRT smoke benchmark and FP32-output comparison.
Implementation: One warm-up/ten serial samples, four COCO8 val inputs, same camera preprocessing.
Related Modules: core.gpu_detector; tools.compare_acceleration; prior CPU baseline JSON.
Declaration Index: prepared_image; main.
Variable Index: None; explicit CLI paths select public fixtures and report location.
"""

import argparse
import hashlib
import io
import json
import statistics
import time
from pathlib import Path

from PIL import Image

from core.detector import decode_frame
from core.gpu_detector import GpuDetector
from tools.compare_acceleration import compare


def prepared_image(path):
    """Functionality: Reproduce public validation inputs. Inputs: path to public source JPEG.
    Outputs: RGB decoded JPEG80, max edge640; Logic: same Pillow bilinear capture simulation.
    Constraints: used only for offline fixtures, not a replacement camera preprocessing pipeline.
    """
    with Image.open(path) as original:
        image = original.convert("RGB")
        scale = min(1, 640 / max(image.size))
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.BILINEAR
        )
        output = io.BytesIO()
        image.save(output, "JPEG", quality=80)
    return decode_frame(output.getvalue())


def main():
    """Functionality: Compare GPU outputs.
    Inputs: engine/image/evaluation/reference/output CLI paths.
    Outputs: public JSON metrics/boxes/hashes/comparison; Logic: one warm-up, ten timed frames.
    Constraints: timing includes transfers; four images are not mAP, background GPU load noted.
    """
    parser = argparse.ArgumentParser()
    for name in ("engine", "image", "evaluation", "reference", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    model = GpuDetector(args.engine.resolve())
    payload = args.image.read_bytes()
    model.predict(decode_frame(payload))
    samples, inference = [], []
    for _ in range(10):
        started = time.perf_counter()
        result, boxes, elapsed = model.predict(decode_frame(payload))
        samples.append((time.perf_counter() - started) * 1000)
        inference.append(elapsed)
    evaluation = {}
    files = sorted(args.evaluation.glob("*.jpg"))
    if len(files) != 4:
        raise ValueError("Expected four COCO8 validation images")
    for path in files:
        _, candidate_boxes, _ = model.predict(prepared_image(path))
        evaluation[path.name] = {
            "boxes": candidate_boxes,
            "raw_sha256": hashlib.sha256(model.output.tobytes()).hexdigest(),
        }
    baseline = json.loads(args.reference.read_text(encoding="utf-8"))
    report = {
        "backend": "TensorRT",
        "precision": "FP32",
        "tf32": False,
        "warmup": 1,
        "frames": 10,
        "samples_total_ms": samples,
        "mean_total_ms": statistics.mean(samples),
        "median_total_ms": statistics.median(samples),
        "mean_gpu_with_transfer_ms": statistics.mean(inference),
        "jpeg_sha256": hashlib.sha256(result).hexdigest(),
        "bus_boxes": boxes,
        "evaluation": evaluation,
        "quality_comparison": compare(baseline["evaluation"], evaluation),
        "conditions": "Windows RTX4060 Laptop; other GPU applications active; not FPS/mAP",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in ("evaluation", "bus_boxes", "samples_total_ms")
            }
        )
    )


if __name__ == "__main__":
    main()
