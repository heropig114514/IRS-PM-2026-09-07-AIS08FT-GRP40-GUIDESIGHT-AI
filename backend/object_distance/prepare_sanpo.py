"""Responsibilities: Convert verified SANPO audit artifacts into E training manifests.
Implementation: Manual pedestrian instances, original session splits, visible-surface medians.
Related Modules: object_distance.data/train; SANPO validation reports.
Declaration Index: convert; main.
Variable Index: TARGET_NAME defines the explicit SANPO distance semantics.
"""

import argparse
import json
from pathlib import Path

from object_distance.config import digest, write_json

TARGET_NAME = "sanpo_visible_surface_median_m"


def convert(root, output):
    """Functionality: Prepare training without changing source data or session assignment.
    Inputs: Verified dataset root and new sibling processed directory. Outputs: manifests.
    Logic: Require completed audit; retain manual frames including empty pedestrian frames.
    Constraints: Generic vehicle is not car; machine masks are excluded explicitly.
    """
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or output.parent != root:
        raise ValueError("Choose a new processed directory directly under SANPO root")
    status = json.loads((root / "reports/validation-status.json").read_text())
    if status.get("stage") != "verified":
        raise ValueError("Wait for complete SANPO download and validation (stage=verified)")
    observations = root / "reports/focus-object-observations.jsonl"
    objects = {}
    for line in observations.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (
            row["annotation_type"] == "HUMAN_ANNOTATED"
            and row["class_id"] == 12
            and row["distance_available"]
        ):
            key = (row["session_id"], row["frame_index"])
            objects.setdefault(key, []).append(
                {
                    "class_name": "person",
                    "bbox": row["bbox_xyxy_rgb"],
                    "distance_m": row["median_surface_depth_m"],
                    "distance_target": TARGET_NAME,
                    "instance_id": row["instance_id"],
                }
            )
    prepared, sessions = {}, set()
    for split, source in (("train", "train"), ("val", "validation"), ("test", "test")):
        records, current = [], set()
        for line in (root / f"manifests/{source}-frames.jsonl").read_text().splitlines():
            row = json.loads(line)
            current.add(row["session_id"])
            if row["annotation_type"] != "HUMAN_ANNOTATED":
                continue
            key = (row["session_id"], row["frame_index"])
            records.append(
                {
                    "image_id": f"{key[0]}/{key[1]:06d}",
                    "image_path": row["rgb"],
                    "image_size": list(reversed(row["shapes"]["rgb_shape"][:2])),
                    "objects": objects.get(key, []),
                    "camera": row["calibration"],
                }
            )
        if sessions & current or not records:
            raise ValueError("Session overlap or empty split")
        sessions.update(current)
        prepared[split] = records
    output.mkdir()
    manifest = {
        "distance_target": TARGET_NAME,
        "supervised_classes": ["person"],
        "annotation_policy": "human_only",
        "source_observations_sha256": digest(observations),
        "splits": {},
    }
    for split, records in prepared.items():
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        manifest["splits"][split] = {
            "manifest_sha256": digest(path),
            "images": len(records),
            "objects": sum(len(r["objects"]) for r in records),
        }
    write_json(output / "manifest.json", manifest)
    return manifest


def main():
    """Functionality: Run explicit SANPO conversion. Inputs: CLI paths. Outputs: counts.
    Logic: Fail until full validation passes. Constraints: Never starts training or downloads.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="model_tests/datasets/sanpo")
    parser.add_argument("--output", default="model_tests/datasets/sanpo/processed-person-manual")
    args = parser.parse_args()
    print(json.dumps(convert(args.root, args.output), indent=2))


if __name__ == "__main__":
    main()
