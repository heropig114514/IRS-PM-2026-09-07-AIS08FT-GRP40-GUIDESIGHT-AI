"""Responsibilities: Produce reproducible stability evidence for two exports of the same source.
Implementation: Compare source/model/timestamp identity, adjacent-track statistics and near-box
retention; extract side-by-side frames from the completed MP4s.
Related Modules: VIDEO-EXPORT.md; video_export.services/refinement; core.detector.box_iou.
Declaration Index: read_run; unique_boxes; summarize; compare; screenshots; main.
Variable Index: None. These metrics measure output behavior, not ground-truth accuracy.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import av
from PIL import Image, ImageDraw

from core.detector import box_iou


def read_run(path):
    """Functionality: Read only completed export artifacts. Inputs: Directory. Outputs: Report/rows.
    Logic: Require completion and declared record count. Constraints: No partial-run comparisons.
    """
    report = json.loads((path / "run.json").read_text(encoding="utf-8"))
    if report["status"] != "completed":
        raise ValueError("Comparison requires two completed runs")
    with (path / "results.jsonl").open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    if len(rows) != report["frames"]:
        raise ValueError("Frame records do not match the manifest")
    return report, rows


def unique_boxes(boxes):
    """Functionality: Deduplicate a reference set consistently before retention accounting.
    Inputs: Boxes. Outputs: Highest-score representatives. Logic: Same class, IoU >= .85.
    Constraints: This is geometric matching, not an independently annotated ground truth.
    """
    result = []
    for box in sorted(boxes, key=lambda b: b["score"], reverse=True):
        if not any(other["label"] == box["label"] and box_iou(other["xyxy"], box["xyxy"])
                   >= .85 for other in result):
            result.append(box)
    return result


def summarize(rows):
    """Functionality: Measure label/candidate counts and per-track temporal changes.
    Inputs: Frame records. Outputs: JSON-safe metrics including their comparison denominators.
    Logic: Only consecutive frames with the same track ID/class form a temporal pair.
    Constraints: Track identity and filtering differ between runs; report rates as well as counts.
    """
    counts, classes, levels, previous = Counter(), Counter(), Counter(), {}
    for row in rows:
        boxes = row["metadata"]["boxes"]
        counts["frames_with_duplicate_boxes"] += len(unique_boxes(boxes)) != len(boxes)
        current = {}
        for box in boxes:
            counts["object_observations"] += 1
            classes[box["label"]] += 1
            levels[box["risk_level"]] += 1
            key = box.get("track_id"), box["label"]
            if key[0] is None:
                continue
            if key in previous:
                old = previous[key]
                counts["adjacent_track_pairs"] += 1
                counts["risk_changes"] += old["risk_level"] != box["risk_level"]
                if "unknown" not in (old["risk_level"], box["risk_level"]):
                    counts["known_risk_pairs"] += 1
                    counts["known_risk_changes"] += old["risk_level"] != box["risk_level"]
                a = old.get("display_distance_m", old.get("distance_m"))
                b = box.get("display_distance_m", box.get("distance_m"))
                if a is not None and b is not None:
                    counts["valid_distance_pairs"] += 1
                    counts["display_jumps_over_2m"] += abs(a - b) > 2
            current[key] = box
        previous = current
    for name, denominator in (("risk_changes", "adjacent_track_pairs"),
                              ("known_risk_changes", "known_risk_pairs"),
                              ("display_jumps_over_2m", "valid_distance_pairs")):
        counts[name + "_per_1000_pairs"] = (round(1000 * counts[name] / counts[denominator], 3)
                                            if counts[denominator] else None)
    return {**counts, "classes": dict(classes), "risk_levels": dict(levels)}


def compare(before, after):
    """Functionality: Check experiment identity and near-reference retention before interpretation.
    Inputs: Two directories. Outputs: Comparison report and row lists.
    Logic: Match input/model/compute settings and timestamps; count same-frame IoU matches for
    unique baseline boxes with raw depth <=3m. Compare raw detector hashes across all frames.
    Constraints: Retention is relative to baseline detections, not true-object recall or safety.
    """
    first, old = read_run(before)
    second, new = read_run(after)
    for key in ("sha256", "frame_rate", "time_base"):
        if first["source"][key] != second["source"][key]:
            raise ValueError(f"Source conditions differ: {key}")
    for key in ("model", "max_edge", "jpeg_quality", "distance", "depth", "risk", "tracking"):
        if first["settings"][key] != second["settings"][key]:
            raise ValueError(f"Base inference conditions differ: {key}")
    if first["models"] != second["models"] or len(old) != len(new):
        raise ValueError("Weights or frame counts differ")
    retention, hashes = Counter(), 0
    for a, b in zip(old, new, strict=True):
        if a["timestamp"] != b["timestamp"]:
            raise ValueError("Source timestamps differ")
        hashes += a["metadata"]["raw_sha256"] == b["metadata"]["raw_sha256"]
        for box in unique_boxes(a["metadata"]["boxes"]):
            distance = box.get("distance_m")
            if distance is None or distance > 3:
                continue
            retention["reference_observations"] += 1
            match = any(other["label"] == box["label"] and box_iou(box["xyxy"], other["xyxy"])
                        >= .5 for other in b["metadata"]["boxes"])
            retention["matched_observations"] += match
            if box["label"] == "person":
                retention["person_reference"] += 1
                retention["person_matched"] += match
    return {"before": str(before.resolve()), "after": str(after.resolve()),
            "frames": len(old), "same_raw_prediction_hash_frames": hashes,
            "source_sha256": first["source"]["sha256"], "models": first["models"],
            "baseline": summarize(old), "stable": summarize(new),
            "near_reference_retention": dict(retention),
            "audio_payloads_equal": first.get("audio_verification") == second.get(
                "audio_verification"),
            "limits": "Heuristic output stability and baseline retention, not accuracy or recall; "
                      "different filtering/tracking changes temporal-pair denominators."}, old, new


def screenshots(before, after, output, times):
    """Functionality: Extract actual encoded before/after frames for visual inspection.
    Inputs: Video directories, owned review directory and timestamp list.
    Outputs: Paired JPGs. Logic: Decode each video sequentially, select closest preceding frames.
    Constraints: Inspection artifacts only; input and result MP4s remain unchanged.
    """
    selected = []
    for directory in (before, after):
        frames, index, previous = [], 0, None
        with av.open(str(directory / "annotated.mp4")) as container:
            for frame in container.decode(video=0):
                timestamp = float(frame.pts * frame.time_base)
                if index < len(times) and timestamp >= times[index]:
                    frames.append(frame.to_image() if timestamp == times[index] or previous is None
                                  else previous.to_image())
                    index += 1
                previous = frame
                if index == len(times):
                    break
        selected.append(frames)
    for index, (old, new) in enumerate(zip(*selected, strict=True)):
        size = old.width + new.width, max(old.height, new.height) + 28
        board = Image.new("RGB", size, "#202020")
        board.paste(old, (0, 28))
        board.paste(new, (old.width, 28))
        draw = ImageDraw.Draw(board)
        draw.text((6, 7), f"Before / {times[index]:g}s", fill="white")
        draw.text((old.width + 6, 7), f"Stable / {times[index]:g}s", fill="white")
        board.save(output / f"compare-{times[index]:g}s.jpg", quality=95)


def main():
    """Functionality: Save a comparison without overwriting existing review artifacts.
    Inputs: CLI before/after/output paths. Outputs: comparison.json and paired frames.
    Logic: Validate completed runs, create an exclusive review directory, write metrics/screens.
    Constraints: Reported counts are observations, not distinct real people or labeled accuracy.
    """
    parser = argparse.ArgumentParser(description="Compare completed video exports")
    for name in ("before", "after", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    report, _, _ = compare(args.before, args.after)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n",
                                                encoding="utf-8")
    screenshots(args.before, args.after, args.output,
                [100 / 28, 458 / 28, 35, 50, 2281 / 28, 95, 140, 148])
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
