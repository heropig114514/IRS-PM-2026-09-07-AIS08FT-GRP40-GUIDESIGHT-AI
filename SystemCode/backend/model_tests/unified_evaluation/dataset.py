"""Responsibilities: Audit and project local nuScenes keyframes into camera-center z targets.
Implementation: Official Devkit Box/Quaternion transforms and projection; clipped cuboid hulls.
Related Modules: unified_evaluation.runner/scoring; nuScenes raw tables remain immutable.
Declaration Index: map_category; camera_box; project_box; safe_path; audit_raw; prepare;
load_records; ordered_records.
Variable Index: TARGET; TABLES; EDGES; CLASS_MAPPING — explicit dataset protocol constants.
"""

import importlib.metadata
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from object_distance.config import digest, write_json

TARGET = "nuscenes_box_center_z_camera_m"
TABLES = (
    "category",
    "attribute",
    "visibility",
    "instance",
    "sensor",
    "calibrated_sensor",
    "ego_pose",
    "log",
    "scene",
    "sample",
    "sample_data",
    "sample_annotation",
    "map",
)
EDGES = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 0),
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 4),
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
)
CLASS_MAPPING = {"vehicle.car": "car", "human.pedestrian.*": "person"}


def map_category(name):
    """Functionality: Apply only declared car/pedestrian mappings.
    Inputs: nuScenes category name. Outputs: canonical name or None.
    Logic: exact car and pedestrian prefix. Constraints: no bus/truck/cyclist remapping.
    """
    if name == "vehicle.car":
        return "car"
    return "person" if name.startswith("human.pedestrian.") else None


def camera_box(box, ego_pose, calibrated_sensor):
    """Functionality: Transform an official global-frame Box into the camera frame.
    Inputs: Devkit Box, exposure ego pose and sensor extrinsics. Outputs: transformed copy.
    Logic: inverse ego translation/rotation, then inverse sensor translation/rotation.
    Constraints: camera forward z is unrelated to global vertical z; input is never mutated.
    """
    from pyquaternion import Quaternion

    result = box.copy()
    result.center = np.asarray(result.center, dtype=np.float64)
    for transform in (ego_pose, calibrated_sensor):
        result.translate(-np.asarray(transform["translation"], dtype=np.float64))
        result.rotate(Quaternion(transform["rotation"]).inverse)
    return result


def project_box(box, intrinsic, size, near=0.1):
    """Functionality: Project the visible near-clipped cuboid hull into continuous xyxy pixels.
    Inputs: camera Box,3x3 K, image(width,height), positive near plane. Outputs: bbox/filter reason.
    Logic: clip all twelve edges at near z, Devkit perspective projection, intersect convex hull
    with the image rectangle. Constraints: projected3D hull is not an instance segmentation;
    occluded pixels remain unknown. Centers behind the near plane are excluded explicitly.
    """
    from nuscenes.utils.geometry_utils import view_points
    from shapely.geometry import MultiPoint
    from shapely.geometry import box as rectangle

    intrinsic = np.asarray(intrinsic, dtype=np.float64)
    center, corners = np.asarray(box.center), box.corners()
    if not math.isfinite(near) or near <= 0 or len(size) != 2 or min(size) <= 0:
        raise ValueError("Invalid projection image/near plane")
    if (
        intrinsic.shape != (3, 3)
        or not np.isfinite(intrinsic).all()
        or min(intrinsic[0, 0], intrinsic[1, 1]) <= 0
        or not np.allclose(intrinsic[2], [0, 0, 1])
    ):
        raise ValueError("Invalid pinhole intrinsics")
    if (
        not np.isfinite(center).all()
        or not np.isfinite(corners).all()
        or not np.isfinite(box.wlh).all()
        or min(box.wlh) <= 0
    ):
        return None, "invalid_3d_box"
    if center[2] <= near:
        return None, "center_behind_near_plane"
    points = [point for point in corners.T if point[2] >= near]
    for first, second in EDGES:
        a, b = corners[:, first], corners[:, second]
        if (a[2] < near) != (b[2] < near):
            point = a + (b - a) * ((near - a[2]) / (b[2] - a[2]))
            points.append(point)
    if not points:
        return None, "behind_camera"
    pixels = view_points(np.asarray(points).T, intrinsic, normalize=True)[:2].T
    if not np.isfinite(pixels).all():
        return None, "invalid_projection"
    visible = MultiPoint(pixels).convex_hull.intersection(rectangle(0, 0, *size))
    if visible.is_empty or visible.area <= 0:
        return None, "outside_image"
    return list(visible.bounds), None


def safe_path(root, relative):
    """Functionality: Resolve a dataset path without allowing traversal or missing media.
    Inputs: root and relative path. Outputs: existing file path. Logic: resolved containment.
    Constraints: no search in alternative datasets, network fetch or substituted images.
    """
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or unsafe dataset path: {relative}")
    return path


def audit_raw(raw, version):
    """Functionality: Verify required tables, directories and every referenced sensor/map file.
    Inputs: local raw directory/version. Outputs: table fingerprints and media counts.
    Logic: validate JSON lists and unique tokens, scan referenced filenames, fail with examples.
    Constraints: no downloads or silent incomplete-data subsets; this is not a full LiDAR decode.
    """
    raw = Path(raw).resolve()
    for folder in ("samples", "sweeps", "maps", version):
        if not (raw / folder).is_dir():
            raise FileNotFoundError(f"Missing nuScenes directory: {raw / folder}")
    tables, hashes = {}, {}
    for table in TABLES:
        path = safe_path(raw, f"{version}/{table}.json")
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or len({r["token"] for r in rows}) != len(rows):
            raise ValueError(f"Invalid/duplicate nuScenes table: {table}")
        tables[table], hashes[table] = rows, digest(path)
    missing = []
    for row in tables["sample_data"] + tables["map"]:
        try:
            safe_path(raw, row["filename"])
        except ValueError:
            missing.append(row["filename"])
    if missing:
        raise FileNotFoundError(f"Missing/unsafe media files count={len(missing)}: {missing[:5]}")
    return {
        "tables": {key: len(rows) for key, rows in tables.items()},
        "table_sha256": hashes,
        "referenced_media_files": len(tables["sample_data"]) + len(tables["map"]),
    }


def prepare(raw, output, *, version="v1.0-mini", cameras=("CAM_FRONT",), near=0.1):
    """Functionality: Convert all requested labeled keyframes using official Devkit objects.
    Inputs: raw/new sibling output, version, camera tuple and fixed near plane. Outputs: summary.
    Logic: full media audit, per-exposure ego/calibration transform, cuboid projection and hashes.
    Constraints: original annotations never change; sweeps have no fabricated GT; visibility
    tokens describe partial visibility, not camera-specific masks or exact occlusion ordering.
    """
    from nuscenes.nuscenes import NuScenes

    raw, output = Path(raw).resolve(), Path(output).resolve()
    if output.exists() or output.parent != raw.parent or output == raw or not cameras:
        raise ValueError("Use a new processed directory beside raw and specify cameras")
    if len(set(cameras)) != len(cameras) or any(not c.startswith("CAM_") for c in cameras):
        raise ValueError("Choose distinct camera channels")
    audit = audit_raw(raw, version)
    nusc = NuScenes(version=version, dataroot=str(raw), verbose=False)
    records, filters, classes = [], Counter(), Counter()
    for sample in nusc.sample:
        scene = nusc.get("scene", sample["scene_token"])
        for camera in cameras:
            sd = nusc.get("sample_data", sample["data"][camera])
            if not sd["is_key_frame"]:
                raise ValueError("Expected labeled camera keyframe")
            sensor = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
            ego = nusc.get("ego_pose", sd["ego_pose_token"])
            path = safe_path(raw, sd["filename"])
            with Image.open(path) as image:
                if image.size != (sd["width"], sd["height"]) or image.mode != "RGB":
                    raise ValueError(f"Camera metadata/image mismatch: {sd['token']}")
                image.verify()
            objects = []
            for token in sample["anns"]:
                annotation = nusc.get("sample_annotation", token)
                category = map_category(annotation["category_name"])
                if category is None:
                    filters[f"unsupported_class:{annotation['category_name']}"] += 1
                    continue
                visibility = annotation["visibility_token"]
                if visibility not in {row["token"] for row in nusc.visibility}:
                    filters["invalid_visibility"] += 1
                    continue
                box = camera_box(nusc.get_box(token), ego, sensor)
                bbox, reason = project_box(
                    box, sensor["camera_intrinsic"], (sd["width"], sd["height"]), near
                )
                if reason:
                    filters[reason] += 1
                    continue
                objects.append(
                    {
                        "annotation_id": token,
                        "instance_id": annotation["instance_token"],
                        "class_name": category,
                        "bbox": bbox,
                        "distance_gt_m": float(box.center[2]),
                        "center_camera_m": box.center.tolist(),
                        "visibility_token": visibility,
                    }
                )
                classes[category] += 1
            records.append(
                {
                    "frame_id": sd["token"],
                    "sample_id": sample["token"],
                    "scene_id": scene["name"],
                    "scene_token": scene["token"],
                    "camera": camera,
                    "timestamp": sd["timestamp"],
                    "sample_timestamp": sample["timestamp"],
                    "image_path": sd["filename"],
                    "image_sha256": digest(path),
                    "image_size": [sd["width"], sd["height"]],
                    "camera_intrinsic": sensor["camera_intrinsic"],
                    "objects": objects,
                }
            )
    records = ordered_records(records)
    if not records:
        raise ValueError("No labeled camera frames")
    output.mkdir(parents=True)
    with (output / "frames.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
    summary = {
        "schema_version": 1,
        "version": version,
        "distance_target": TARGET,
        "raw_directory": str(raw),
        "cameras": list(cameras),
        "near_plane_m": near,
        "frames": len(records),
        "scenes": len({r["scene_id"] for r in records}),
        "objects": sum(classes.values()),
        "classes": dict(classes),
        "filter_counts": dict(filters),
        "class_mapping": CLASS_MAPPING,
        "raw_audit": audit,
        "frames_sha256": digest(output / "frames.jsonl"),
        "devkit_version": importlib.metadata.version("nuscenes-devkit"),
        "limitations": [
            "Projected cuboid hull, not an occlusion-aware instance box",
            "Box-center camera z is not visible-surface depth",
            "Annotated keyframes only; no sweep distance ground truth",
        ],
    }
    write_json(output / "dataset_summary.json", summary)
    return summary


def ordered_records(records, limit=None):
    """Functionality: Sort separate scene/camera streams using original integer microseconds.
    Inputs: frame records and optional total prefix limit. Outputs: validated ordered records.
    Logic: group by scene/camera, sort actual timestamps; reject duplicates and invalid clocks.
    Constraints: cameras never form one tracking stream; limiting is explicit smoke selection.
    """
    result = sorted(records, key=lambda r: (r["scene_id"], r["camera"], r["timestamp"]))
    seen, clocks = set(), {}
    for row in result:
        key, timestamp = (row["scene_id"], row["camera"]), row["timestamp"]
        if (
            type(timestamp) is not int
            or timestamp < 0
            or row["frame_id"] in seen
            or timestamp <= clocks.get(key, -1)
        ):
            raise ValueError(
                "Frame IDs/timestamps must be unique and strictly increasing per stream"
            )
        seen.add(row["frame_id"])
        clocks[key] = timestamp
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Limit must be positive")
    return result if limit is None else result[:limit]


def load_records(processed, limit=None):
    """Functionality: Load an immutable converted dataset with target and file identity checks.
    Inputs: processed directory and explicit prefix limit. Outputs: summary and sorted frames.
    Logic: check manifest hash and each raw image SHA before any model timing.
    Constraints: no implicit conversion, image resizing, replacement or partial missing-file runs.
    """
    processed = Path(processed)
    summary = json.loads((processed / "dataset_summary.json").read_text(encoding="utf-8"))
    path = processed / "frames.jsonl"
    if summary["distance_target"] != TARGET or digest(path) != summary["frames_sha256"]:
        raise ValueError("Dataset target/fingerprint mismatch")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(records) != summary["frames"]:
        raise ValueError("Dataset frame count mismatch")
    records = ordered_records(records, limit)
    for row in records:
        if digest(safe_path(summary["raw_directory"], row["image_path"])) != row["image_sha256"]:
            raise ValueError("Raw image changed after conversion")
    return summary, records
