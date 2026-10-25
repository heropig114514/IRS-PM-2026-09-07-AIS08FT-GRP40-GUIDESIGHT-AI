"""Responsibilities: Convert local KITTI objects into explicit camera-z training manifests.
Implementation: Audit image/label/calibration pairing; parse fields; persist fixed disjoint splits.
Related Modules: object_distance.config/data; KITTI raw/processed directories remain Git-ignored.
Declaration Index: parse_label; read_calibration; inventory; split_ids; convert; main.
Variable Index: CLASS_MAP — only justified KITTI-to-COCO mappings; logger — audit progress.
"""

import argparse
import json
import logging
import math
import random
from collections import Counter
from pathlib import Path

from PIL import Image

from object_distance.config import ROOT, TARGET, ExperimentConfig, digest, write_json

CLASS_MAP = {"Car": "car", "Pedestrian": "person"}
logger = logging.getLogger(__name__)


def parse_label(line, image_size):
    """Functionality: Parse KITTI's 15-field ground truth with z at field13 (zero-based).
    Inputs: label line and (width,height). Outputs: (object or None, filter reason or None).
    Logic: map only Car/Pedestrian, require finite values/positive dimensions/z, clip visible bbox.
    Constraints: location xyz is a camera reference point; z is not nearest surface or Euclidean
    range. Truncation/occlusion remain metadata, not hidden difficulty filtering.
    """
    fields = line.split()
    if len(fields) != 15:
        return None, "malformed_fields"
    if fields[0] not in CLASS_MAP:
        return None, f"unsupported_class:{fields[0]}"
    try:
        values = [float(value) for value in fields[1:]]
    except ValueError:
        return None, "nonnumeric"
    if not all(math.isfinite(value) for value in values):
        return None, "nonfinite"
    x1, y1, x2, y2 = values[3:7]
    location = values[10:13]
    if location[2] <= 0:
        return None, "nonpositive_z"
    if min(values[7:10]) <= 0:
        return None, "invalid_3d_dimensions"
    if x2 <= x1 or y2 <= y1:
        return None, "invalid_bbox"
    width, height = image_size
    bbox = [
        max(0.0, min(float(width), x1)),
        max(0.0, min(float(height), y1)),
        max(0.0, min(float(width), x2)),
        max(0.0, min(float(height), y2)),
    ]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None, "bbox_outside_image"
    return {
        "kitti_class": fields[0],
        "class_name": CLASS_MAP[fields[0]],
        "bbox": bbox,
        "original_bbox": [x1, y1, x2, y2],
        "location_xyz_m": location,
        "distance_m": location[2],
        "distance_target": TARGET,
        "truncation": values[0],
        "occlusion": values[1],
    }, None


def read_calibration(path):
    """Functionality: Validate and retain KITTI left-color projection P2.
    Inputs: calibration text path. Outputs: finite12-element projection.
    Logic: exact field selection. Constraints: used for provenance/future camera comparisons,
    never to reinterpret supplied object z as a different distance definition.
    """
    entries = dict(
        line.split(":", 1) for line in Path(path).read_text().splitlines() if ":" in line
    )
    values = [float(v) for v in entries.get("P2", "").split()]
    if (
        len(values) != 12
        or not all(math.isfinite(v) for v in values)
        or min(values[0], values[5]) <= 0
    ):
        raise ValueError(f"Missing/invalid P2: {Path(path).name}")
    return values


def inventory(raw, verify_images=False):
    """Functionality: Check all local raw filenames, PNG readability and P2 calibrations.
    Inputs: raw directory, optional full PNG CRC verification. Outputs: image sizes and audit.
    Logic: exact counterpart ID sets; test labels are absent by design; inspect every image.
    Constraints: no dataset download, raw modification or silent exclusion of incomplete files.
    """
    sizes, report = {}, {}
    for split in ("training", "testing"):
        images = {p.stem: p for p in (raw / split / "image_2").glob("*.png")}
        calibrations = {p.stem for p in (raw / split / "calib").glob("*.txt")}
        if not images or set(images) != calibrations:
            raise ValueError(f"Incomplete {split} image/calibration pairing")
        labels = {p.stem for p in (raw / split / "label_2").glob("*.txt")}
        if split == "training" and set(images) != labels:
            raise ValueError("Incomplete training image/label pairing")
        if split == "testing" and labels:
            raise ValueError("Unexpected test labels; do not mix public and private test data")
        for index, (identity, path) in enumerate(sorted(images.items())):
            if not identity.isdigit() or len(identity) != 6:
                raise ValueError("KITTI image IDs must be six digits")
            with Image.open(path) as image:
                if image.format != "PNG" or image.mode != "RGB" or min(image.size) <= 0:
                    raise ValueError(f"Invalid RGB PNG: {path.name}")
                sizes[(split, identity)] = image.size
                if verify_images:
                    image.verify()
            read_calibration(raw / split / "calib" / f"{identity}.txt")
            if index % 1000 == 0:
                logger.info("Raw audit split=%s checked=%d total=%d", split, index, len(images))
        report[split] = {
            "images": len(images),
            "calibrations": len(calibrations),
            "labels": len(labels),
        }
    report["image_validation"] = "PNG CRC verification" if verify_images else "PNG headers"
    return sizes, report


def split_ids(identities, *, split_dir=None, seed=42, val_fraction=0.2):
    """Functionality: Select reproducible disjoint train/validation image IDs.
    Inputs: available IDs; explicit local common split files or seeded random fraction.
    Outputs: train IDs, val IDs, provenance. Logic: require full exact coverage and no duplicates.
    Constraints: seeded image splitting is the default policy, not a fallback; neither policy
    establishes scene independence without sequence metadata.
    """
    identities = sorted(identities)
    if split_dir is not None:
        paths = [Path(split_dir) / f"{name}.txt" for name in ("train", "val")]
        train, val = [path.read_text().splitlines() for path in paths]
        provenance = {
            "method": "provided_common_object_split",
            "files": {path.name: digest(path) for path in paths},
            "seed_used": False,
        }
        source = Path(split_dir) / "provenance.json"
        if source.is_file():
            provenance["source"] = json.loads(source.read_text())
    else:
        if not 0 < val_fraction < 1 or len(identities) < 2:
            raise ValueError("Random split requires >=2 images and 0<val_fraction<1")
        shuffled = identities.copy()
        random.Random(seed).shuffle(shuffled)
        count = max(1, min(len(shuffled) - 1, round(len(shuffled) * val_fraction)))
        val, train = sorted(shuffled[:count]), sorted(shuffled[count:])
        provenance = {
            "method": "seeded_image_split",
            "seed": seed,
            "val_fraction": val_fraction,
            "seed_used": True,
        }
    if (
        not train
        or not val
        or len(set(train)) != len(train)
        or len(set(val)) != len(val)
        or set(train) & set(val)
        or set(train) | set(val) != set(identities)
    ):
        raise ValueError("Splits must be nonempty, unique, disjoint and cover all training images")
    provenance["limitation"] = "Image-ID split; sequence-level independence is not established"
    return train, val, provenance


def convert(raw, output, *, split_dir=None, seed=42, val_fraction=0.2, verify_images=False):
    """Functionality: Convert all labeled images while retaining empty images and filter counts.
    Inputs: local raw/new output directories and explicit split policy. Outputs: audit manifest.
    Logic: audit before writing; store paths relative to dataset root; hash label/calibration
    inputs.
    Constraints: never overwrite raw/processed data; testing images are not used as validation.
    """
    raw, output = Path(raw).resolve(), Path(output).resolve()
    if output.exists() or output.parent != raw.parent or raw.name != "raw":
        raise ValueError("Choose a new processed directory beside the KITTI raw directory")
    sizes, audit = inventory(raw, verify_images)
    train, val, provenance = split_ids(
        [identity for split, identity in sizes if split == "training"],
        split_dir=split_dir,
        seed=seed,
        val_fraction=val_fraction,
    )
    output.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "distance_target": TARGET,
        "raw_audit": audit,
        "class_mapping": CLASS_MAP,
        "split": provenance,
        "splits": {},
    }
    for split, ids in (("train", train), ("val", val)):
        counts, classes = Counter(), Counter()
        with (output / f"{split}.jsonl").open("w", encoding="utf-8") as stream:
            for identity in ids:
                size = sizes[("training", identity)]
                label = raw / "training/label_2" / f"{identity}.txt"
                calib = raw / "training/calib" / f"{identity}.txt"
                objects = []
                for line in label.read_text().splitlines():
                    obj, reason = parse_label(line, size)
                    counts[reason or "accepted"] += 1
                    if obj is not None:
                        objects.append(obj)
                        classes[obj["class_name"]] += 1
                record = {
                    "image_id": identity,
                    "image_path": f"raw/training/image_2/{identity}.png",
                    "calibration_path": f"raw/training/calib/{identity}.txt",
                    "label_sha256": digest(label),
                    "calibration_sha256": digest(calib),
                    "image_size": list(size),
                    "p2": read_calibration(calib),
                    "objects": objects,
                }
                stream.write(json.dumps(record, allow_nan=False) + "\n")
        (output / f"{split}.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
        report["splits"][split] = {
            "images": len(ids),
            "objects": counts["accepted"],
            "classes": dict(classes),
            "filter_counts": dict(counts),
            "manifest_sha256": digest(output / f"{split}.jsonl"),
        }
    write_json(output / "manifest.json", report)
    logger.info(
        "Conversion complete counts=%s", {k: v["objects"] for k, v in report["splits"].items()}
    )
    return report


def main(argv=None):
    """Functionality: Convert only existing local KITTI data from the terminal.
    Inputs: raw/output/split policy options. Outputs: JSON summary/exception exit.
    Logic: default to seed42 and20% validation; --split-dir explicitly selects supplied IDs.
    Constraints: no network calls or overwrite; --random-split remains a compatible explicit flag.
    """
    parser = argparse.ArgumentParser(description="Prepare KITTI reference-point z targets")
    base = ROOT / "model_tests/datasets/kitti"
    parser.add_argument("--raw", type=Path, default=base / "raw")
    parser.add_argument("--output", type=Path, default=ROOT / ExperimentConfig().processed)
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--split-dir", type=Path, help="Use supplied train.txt/val.txt IDs")
    policy.add_argument(
        "--random-split", action="store_true", help="Explicit seeded split (default)"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument("--verify-images", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    report = convert(
        args.raw,
        args.output,
        split_dir=args.split_dir,
        seed=args.seed,
        val_fraction=args.val_fraction,
        verify_images=args.verify_images,
    )
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    main()
