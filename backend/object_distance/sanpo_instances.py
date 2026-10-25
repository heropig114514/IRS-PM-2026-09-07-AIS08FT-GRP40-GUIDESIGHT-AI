"""Responsibilities: Prepare class-agnostic SANPO object supervision.
Implementation: Manual panoptic instances, native-depth-grid medians, fixed session splits.
Related Modules: object_distance.data/train; verified SANPO frame manifests.
Declaration Index: extract; main.
Variable Index: IDS are independent object classes; TARGET is surface median meters.
"""

import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from object_distance.config import digest, write_json

IDS = (10, 11, 12, 13, 14, 15, 20, 21, 22, 23, 24, 25, 26, 28)
TARGET = "sanpo_visible_surface_median_m"


def extract(root, frame, names):
    """Functionality: Extract boxes and independent surface-distance targets.
    Inputs: Root/frame/taxonomy. Outputs: objects and excluded-instance counts.
    Logic: Official R=semantic,G*256+B=instance; float16 H,W header then depth.
    Constraints: Native depth units; nearest mask resize; zero-ID and invalid depth excluded.
    """
    with Image.open(root / frame["mask"]) as image:
        mask = np.array(image.convert("RGB"))
    values = np.frombuffer(gzip.decompress((root / frame["depth"]).read_bytes()), dtype="<f2")
    h, w = map(int, values[:2])
    depth = values[2:].reshape(h, w).astype(np.float32)
    if abs(mask.shape[1] / mask.shape[0] - w / h) > 0.002:
        raise ValueError("Mask/depth aspect mismatch")
    sampled = np.array(Image.fromarray(mask).resize((w, h), Image.Resampling.NEAREST))
    keys = (
        mask[:, :, 0].astype(np.int64) * 65536
        + mask[:, :, 1].astype(np.int64) * 256
        + mask[:, :, 2]
    )
    small = (
        sampled[:, :, 0].astype(np.int64) * 65536
        + sampled[:, :, 1].astype(np.int64) * 256
        + sampled[:, :, 2]
    )
    valid = np.isfinite(depth) & (depth > 0)
    objects, rejected = [], Counter()
    for key in np.unique(keys):
        category, instance = divmod(int(key), 65536)
        if category not in IDS:
            continue
        if instance == 0:
            rejected["zero_instance_id"] += 1
            continue
        distances = depth[(small == key) & valid]
        if not len(distances):
            rejected["no_valid_depth"] += 1
            continue
        yy, xx = np.where(keys == key)
        height, width = frame["shapes"]["rgb_shape"][:2]
        sx, sy = width / mask.shape[1], height / mask.shape[0]
        objects.append(
            {
                "class_name": names[category],
                "bbox": [
                    float(xx.min() * sx),
                    float(yy.min() * sy),
                    float((xx.max() + 1) * sx),
                    float((yy.max() + 1) * sy),
                ],
                "distance_m": float(np.median(distances)),
                "distance_target": TARGET,
                "instance_id": instance,
                "valid_depth_pixels": len(distances),
            }
        )
    return objects, rejected


def main():
    """Functionality: Build new immutable training manifests. Inputs: Verified local SANPO.
    Outputs: processed-agnostic-manual and config. Logic: official session isolation.
    Constraints: Human masks only; no hidden distance cutoff, model training or downloads.
    """
    root = Path("model_tests/datasets/sanpo")
    if json.loads((root / "reports/validation-status.json").read_text())["stage"] != "verified":
        raise ValueError("Full validation required")
    output = root / "processed-agnostic-manual"
    if output.exists():
        raise ValueError("Output exists")
    output.mkdir()
    names = {v: k for k, v in json.loads((root / "raw/labelmap.json").read_text()).items()}
    report = {
        "distance_target": TARGET,
        "annotation_policy": "human_only",
        "splits": {},
        "included_classes": [names[i] for i in IDS],
        "excluded": {},
        "source_hashes": {},
    }
    seen = set()
    for split, source in (("train", "train"), ("val", "validation"), ("test", "test")):
        source_path = root / f"manifests/{source}-frames.jsonl"
        frames = [json.loads(line) for line in source_path.read_text().splitlines()]
        sessions = {f["session_id"] for f in frames}
        if seen & sessions:
            raise ValueError("Session leakage")
        seen |= sessions
        report["source_hashes"][source] = digest(source_path)
        records, counts, rejected = [], Counter(), Counter()
        for frame in frames:
            if frame["annotation_type"] != "HUMAN_ANNOTATED":
                continue
            objects, failures = extract(root, frame, names)
            rejected.update(failures)
            counts.update(o["class_name"] for o in objects)
            records.append(
                {
                    "image_id": f"{frame['session_id']}/{frame['frame_index']:06d}",
                    "image_path": frame["rgb"],
                    "image_size": list(reversed(frame["shapes"]["rgb_shape"][:2])),
                    "objects": objects,
                    "camera": frame["calibration"],
                }
            )
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        report["splits"][split] = {
            "manifest_sha256": digest(path),
            "images": len(records),
            "objects": sum(counts.values()),
            "classes": dict(counts),
            "sessions": sorted(sessions),
        }
        report["excluded"][split] = dict(rejected)
        print(split, report["splits"][split], flush=True)
    write_json(output / "manifest.json", report)
    config = json.loads(Path("object_distance/config.sanpo.json").read_text())
    config.update(
        class_agnostic=True,
        supported_classes=[names[i] for i in IDS],
        processed=str(output),
        freeze_backbone=True,
        unfreeze_last_layers=0,
    )
    write_json("object_distance/config.sanpo-agnostic.json", config)


if __name__ == "__main__":
    main()
