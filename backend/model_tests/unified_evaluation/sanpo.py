"""Responsibilities: Compare unchanged A/B/C and class-agnostic E on SANPO Test.
Implementation: Existing adapters/scoring/timing, continuous frames, manual-only scoring.
Related Modules: unified_evaluation.adapters/scoring/performance; SANPO manifests.
Declaration Index: canonical; main.
Variable Index: MAP is an explicit COCO-to-SANPO comparison taxonomy.
"""

import argparse
import json
from pathlib import Path

from PIL import Image

from object_distance.config import digest, write_json

from .adapters import create_adapter
from .performance import latency_summary
from .scoring import score_frame, summarize

MAP = {
    "person": "pedestrian",
    "car": "vehicle",
    "truck": "vehicle",
    "bus": "vehicle",
    "motorcycle": "vehicle",
    "bicycle": "vehicle",
    "traffic light": "traffic light",
    "stop sign": "traffic sign",
    **{
        c: "animal"
        for c in (
            "bird",
            "cat",
            "dog",
            "horse",
            "sheep",
            "cow",
            "elephant",
            "bear",
            "zebra",
            "giraffe",
        )
    },
}


def canonical(objects):
    """Functionality: Map predicted labels for evaluation only. Inputs: YOLO objects.
    Outputs: Copied objects. Logic: Explicit broad taxonomic correspondence.
    Constraints: Rider is not mapped to person; unknown classes remain unmatched.
    """
    return [
        {**obj, "class_name": MAP.get(obj["class_name"], "unmapped:" + obj["class_name"])}
        for obj in objects
    ]


def main():
    """Functionality: Evaluate one method in its own process. Inputs: CLI method/checkpoint.
    Outputs: Predictions, all-GT observations, grouped errors and latency report.
    Logic: Full 15FPS sequence, reset after warmup, score only manual annotated frames.
    Constraints: Same surface-median reference for all; B remains a size-based proxy.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=list("abce"), required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path("model_tests/datasets/sanpo")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    frames = [
        json.loads(line) for line in (root / "manifests/test-frames.jsonl").read_text().splitlines()
    ]
    targets = {
        r["image_id"]: r
        for r in (
            json.loads(line)
            for line in (root / "processed-agnostic-manual/test.jsonl").read_text().splitlines()
        )
    }
    options = {
        "method": args.method,
        "device": "cuda:0",
        "depth_interval": 3,
        "detection_weights": "models/yolo26n.pt",
        "detection_manifest": "models/yolo26n.json",
        "depth_weights": "models/yolo26n-depth.pt",
        "e_checkpoint": args.checkpoint,
    }
    adapter = create_adapter(options)
    observations, timings, flags = [], [], []
    scene = None
    with (output / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for index, source in enumerate(frames):
            key = f"{source['session_id']}/{source['frame_index']:06d}"
            camera = source["calibration"]
            frame = {
                "frame_id": key,
                "objects": [],
                "camera_intrinsic": [
                    [camera["fx"], 0, camera["cx"]],
                    [0, camera["fy"], camera["cy"]],
                    [0, 0, 1],
                ],
            }
            if key in targets:
                frame["objects"] = [
                    {**obj, "distance_gt_m": obj["distance_m"], "annotation_id": str(i)}
                    for i, obj in enumerate(targets[key]["objects"])
                ]
            with Image.open(root / source["rgb"]) as raw:
                image = raw.convert("RGB")
            if scene != source["session_id"]:
                if scene is None:
                    for _ in range(3):
                        adapter.predict(image, frame, bbox_mode="predicted", timestamp=0)
                adapter.reset()
                scene = source["session_id"]
            objects, timing, depth = adapter.predict(
                image, frame, bbox_mode="predicted", timestamp=source["timestamp_nominal_s"]
            )
            timings.append(timing)
            flags.append(depth)
            if key in targets:
                observed, _ = score_frame(
                    frame,
                    canonical(objects),
                    bbox_mode="predicted",
                    match_iou=0.5,
                    depth_frame=depth,
                )
                observations.extend(observed)
            handle.write(
                json.dumps(
                    {"frame_id": key, "objects": objects, "timings": timing, "depth_frame": depth}
                )
                + "\n"
            )
            if index % 50 == 0:
                print(args.method, index + 1, len(frames), flush=True)
    (output / "observations.jsonl").write_text("".join(json.dumps(r) + "\n" for r in observations))
    report = {
        "method": args.method,
        "distance_target": "sanpo_visible_surface_median_m",
        "metrics": summarize(observations),
        "latency": latency_summary(timings),
        "depth_calls": sum(flags),
        "frames": len(frames),
        "manual_scored_frames": len(targets),
        "identity": adapter.identity,
        "class_mapping": MAP,
        "test_manifest_sha256": digest(root / "processed-agnostic-manual/test.jsonl"),
        "limitations": [
            "MAE is conditional on matched valid distances; all GT remain in coverage.",
            "A/C use bbox-depth medians; B uses assumed height; "
            "all scored against mask-surface medians.",
            "Unmapped detector categories cannot match GT; no threshold tuning on test.",
        ],
    }
    if args.method == "c":
        report["depth_frame_latency"] = latency_summary(
            [t for t, f in zip(timings, flags, strict=True) if f]
        )
        report["non_depth_frame_latency"] = latency_summary(
            [t for t, f in zip(timings, flags, strict=True) if not f]
        )
    write_json(output / "report.json", report)
    print(json.dumps(report["metrics"]["overall"]), flush=True)


if __name__ == "__main__":
    main()
