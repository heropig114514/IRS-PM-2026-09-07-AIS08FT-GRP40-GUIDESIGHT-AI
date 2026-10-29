"""Responsibilities: Verify exact label-cache pixels/JPEG on complete existing moving videos.
Implementation: Original/cached/original rendering of identical decoded pixels and actual boxes
from one unchanged FP32 model pass; preserve every frame and source timestamp, never subsample.
Related Modules: core.detector; tools.label_cache_probe/benchmark_inference; realtime.detector.
Declaration Index: capture_render; capture_render.save; compare_render; run; run.paired; main.
Variable Index: None. Diagnostic repeated rendering is not browser throughput.
"""

import argparse
import hashlib
import io
import json
import logging
import platform
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.detector import annotate
from tools.benchmark_inference import ROOT, build_detector, distribution
from tools.label_cache_probe import LabelRasterCache


def capture_render(renderer, image, boxes):
    """Functionality: Capture pre-JPEG pixels while measuring the unchanged encoder call.
    Inputs: renderer, independent RGB image and identical boxes. Outputs: JPEG, RGB image, ms.
    Logic: temporarily wrap save to retain its image reference; hash/copy outside timed work.
    Constraints: single diagnostic worker only; patch installed outside timing and always restored;
    the wrapper/reference append adds a shared small cost, no second JPEG decode is an RGB oracle.
    """
    original_save, captured = Image.Image.save, []

    def save(frame, *args, **kwargs):
        """Functionality: Retain exactly the image passed to the real JPEG encoder.
        Inputs: original save arguments. Outputs: unchanged save result.
        Logic: append reference, call original once. Constraints: no image mutation or copies.
        """
        captured.append(frame)
        return original_save(frame, *args, **kwargs)

    with patch.object(Image.Image, "save", save):
        started = time.perf_counter()
        jpeg = renderer(image, boxes)
        elapsed = (time.perf_counter() - started) * 1000
    if len(captured) != 1:
        raise AssertionError("Expected one original JPEG encode")
    return jpeg, captured[0], elapsed


def compare_render(image, boxes, cache):
    """Functionality: Compare exact RGB and JPEG while preserving paired render timings.
    Inputs: same frame/boxes and persistent cache. Outputs: original JPEG and complete comparison.
    Logic: original/candidate/original, hash after timing and compare bytes with both controls.
    Constraints: no tolerance, retry, input mutation, model rerun or timing-based motion changes.
    """
    rows, results = {}, []
    for name, renderer in (("before", annotate), ("cached", cache.annotate), ("after", annotate)):
        jpeg, rendered, elapsed = capture_render(renderer, image, boxes)
        pixels = rendered.tobytes()
        rows[name] = {"render_ms": elapsed, "rgb_sha256": hashlib.sha256(pixels).hexdigest(),
                      "jpeg_sha256": hashlib.sha256(jpeg).hexdigest()}
        results.append((jpeg, pixels))
    rows.update(rgb_exact=results[0][1] == results[1][1] == results[2][1],
                jpeg_exact=results[0][0] == results[1][0] == results[2][0],
                cache=cache.snapshot())
    return results[0][0], rows


def run(video, output):
    """Functionality: Evaluate all frames of one existing raw video with real model/tracking data.
    Inputs: video path and new output directory. Outputs: report.json and every frame in JSONL.
    Logic: decode sequentially, resize/encode using existing camera-size/JPEG contract, pass source
    seconds to unchanged full inference; compare renders inside its annotation call on same boxes.
    Constraints: no ground truth, realtime pacing, depth Graph, dropped frames or sampling changes;
    processing times include extra comparisons and cannot establish end-to-end service performance.
    """
    if not video.is_file() or output.exists():
        raise ValueError("Provide an existing video and new output directory")
    output.mkdir(parents=True)
    report = {"status": "running", "completed_frames": 0,
              "scope": "Paired renderer diagnostic on every source frame; no accuracy/FPS claim",
              "video": str(video), "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
              "settings": {"detection_edge": 640, "confidence": 0.25, "jpeg_quality": 80,
                           "depth_edge": 768, "precision": "FP32", "depth_graph": False,
                           "timestamp": "decoded source presentation milliseconds / 1000"}}
    report["implementation_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("core/detector.py", "tools/label_cache_probe.py",
                     "tools/validate_label_cache.py",
                     "tools/benchmark_inference.py", "realtime/detector.py", "realtime/depth.py",
                     "realtime/tracking.py", "realtime/risk.py", "realtime/distance.py",
                     "realtime/distance_config.py", "realtime/performance.py",
                     "zerogpu/runtime.py")}
    cache, capture, timings = LabelRasterCache(), None, []
    comparisons = []

    def paired(image, boxes):
        """Functionality: Validate the actual pipeline's annotation without repeating inference.
        Inputs: decoded image and fully enriched boxes. Outputs: original JPEG.
        Logic: retain paired comparison for the current frame. Constraints: one call per prediction.
        """
        jpeg, row = compare_render(image, boxes, cache)
        comparisons.append(row)
        return jpeg

    try:
        import cv2
        import torch
        import ultralytics

        if not torch.cuda.is_available():
            raise RuntimeError("Native CUDA required")
        report["environment"] = {"python": platform.python_version(), "pillow": Image.__version__,
                                 "opencv": cv2.__version__, "torch": torch.__version__,
                                 "ultralytics": ultralytics.__version__, "cuda": torch.version.cuda,
                                 "gpu": torch.cuda.get_device_name()}
        report["assets_sha256"] = {
            name: hashlib.sha256((ROOT / "models" / name).read_bytes()).hexdigest()
            for name in ("yolo26n.pt", "yolo26n-depth.pt", "yolo26n.json")}
        detector = build_detector("full", ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n.json",
                                  ROOT / "models/yolo26n-depth.pt")
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise RuntimeError("Video cannot be opened")
        count, fps = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), capture.get(cv2.CAP_PROP_FPS)
        if count < 2 or fps <= 0:
            raise ValueError("Video must declare frame count and positive frame rate")
        report.update(declared_frames=count, source_fps=fps)
        previous_timestamp = -1.0
        with (patch("realtime.detector.annotate", paired),
              (output / "frames.jsonl").open("w", encoding="utf-8") as records):
            for index in range(count):
                ok, bgr = capture.read()
                if not ok:
                    raise RuntimeError("Video ended before its declared frame count")
                timestamp = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
                if not previous_timestamp < timestamp or abs(timestamp - index / fps) > 1e-6:
                    raise ValueError("Source timestamps differ from the declared frame cadence")
                previous_timestamp = timestamp
                image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                scale = min(1, 640 / max(image.size))
                image = image.resize((round(image.width * scale), round(image.height * scale)),
                                     Image.Resampling.BILINEAR)
                encoded = io.BytesIO()
                image.save(encoded, "JPEG", quality=80)
                payload = encoded.getvalue()
                metadata, _ = detector.predict(payload, timestamp=timestamp)
                if len(comparisons) != 1:
                    raise AssertionError("Expected one annotation per frame")
                row = comparisons.pop()
                records.write(json.dumps({"index": index, "timestamp_s": timestamp,
                    "input_sha256": hashlib.sha256(payload).hexdigest(), "comparison": row,
                    "metadata": metadata}, allow_nan=False) + "\n")
                records.flush()
                if not row["rgb_exact"] or not row["jpeg_exact"]:
                    raise AssertionError("Label raster pixels or JPEG differ")
                timings.append({name: row[name]["render_ms"]
                                for name in ("before", "cached", "after")})
                report["completed_frames"] += 1
            if capture.read()[0]:
                raise RuntimeError("Video contains more than its declared frame count")
        report.update(status="completed", all_rgb_exact=True, all_jpeg_exact=True,
                      first_render_ms=timings[0], steady_render_ms={
                          name: distribution([row[name] for row in timings[1:]])
                          for name in ("before", "cached", "after")})
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        if capture is not None:
            capture.release()
        report["cache"] = cache.snapshot()
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n",
                                           encoding="utf-8")
    return report


def main():
    """Functionality: Run an explicitly selected existing video diagnostic.
    Inputs: --video and --output. Outputs: status/count or nonzero failure.
    Logic: parse, log and execute once. Constraints: no downloads, retries or overwrites.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    report = run(args.video, args.output)
    print(json.dumps({"status": report["status"], "frames": report["completed_frames"]}))


if __name__ == "__main__":
    main()
