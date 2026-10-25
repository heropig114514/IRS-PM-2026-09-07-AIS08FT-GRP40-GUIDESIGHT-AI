"""Responsibilities: Prepare a separate, budgeted SANPO diversity subset without training.
Implementation: Cached inventory, explicit gcloud copies, manual frames, existing extractor.
Related Modules: object_distance.sanpo_instances/data; prior SANPO discovery and raw originals.
Declaration Index: read; write; catalogue; entries; download_group; download; inspect;
    screen; analyse; select_training_frames; verify_checksums; build; main.
Variable Index: BACKEND, ROOT, DISCOVERY, OUT, CLI, ROLES, EDGES, BIN_NAMES.
"""

import argparse
import base64
import collections
import concurrent.futures
import csv
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from object_distance.config import digest
from object_distance.sanpo_instances import TARGET, extract

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND / "model_tests/datasets/sanpo"
DISCOVERY = BACKEND / "model_tests/outputs/sanpo-discovery-20261023"
OUT = ROOT / "expansion-diversity-01"
CLI = Path(r"D:\gcloud cli\google-cloud-sdk\bin\gcloud.cmd")
ROLES = {"video_frames": "rgb", "depth_maps": "depth", "segmentation_masks": "mask"}
EDGES = [0, 2, 5, 10, 20, 40, 80, float("inf")]
BIN_NAMES = ["0-2", "2-5", "5-10", "10-20", "20-40", "40-80", "80+"]


def read(path):
    """Functionality: Read JSON. Inputs: Path. Outputs: value.
    Logic: UTF-8 decoding. Constraints: Reject missing/malformed input.
    """
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    """Functionality: Save new experiment reports. Inputs: Path/value. Outputs: None.
    Logic: Atomic sibling replacement. Constraints: Callers use only the new experiment outputs.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def catalogue():
    """Functionality: Load paired eligible sessions. Inputs: Cached official discovery.
    Outputs: Session dictionary. Logic: Existing daylight/outdoor/walking screen.
    Constraints: Metadata is a proxy, not visual or geographic verification.
    """
    with (DISCOVERY / "session-catalogue.csv").open(encoding="utf-8-sig") as stream:
        eligible = [r for r in csv.DictReader(stream) if r["eligible_daylight_proxy"] == "True"]
    return {r["session_id"]: read(DISCOVERY / f"objects/{r['session_id']}.json") for r in eligible}


def entries(sid, indices):
    """Functionality: Resolve exact triplet URLs and sizes. Inputs: Session/frame IDs.
    Outputs: Whitelist entries. Logic: Parse cached gcloud long listing; require all modalities.
    Constraints: No wildcard payload downloads and no propagated annotations.
    """
    result = []
    for line in (DISCOVERY / f"objects/{sid}.txt").read_text().splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        url = parts[2]
        role = url.split("/")[-2]
        if role not in ROLES:
            continue
        index = int(url.split("/")[-1].split(".")[0])
        if index in indices:
            result.append(
                {
                    "url": url,
                    "bytes": int(parts[0]),
                    "session_id": sid,
                    "frame_index": index,
                    "role": ROLES[role],
                    "path": "raw/" + url.split("/v0/")[1],
                }
            )
    if len(result) != 3 * len(indices):
        raise ValueError(f"Incomplete triplets: {sid}")
    return result


def download_group(item):
    """Functionality: Fetch one explicit destination group using installed gcloud.
    Inputs: Log key and entries. Outputs: transfer summary. Logic: One cp call, validate sizes.
    Constraints: No overwrite, no application retry, gzip payloads stay compressed.
    """
    key, group = item
    parent = (ROOT / group[0]["path"]).parent
    parent.mkdir(parents=True, exist_ok=True)
    logs = OUT / "logs"
    logs.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        CLOUDSDK_CORE_DISABLE_PROMPTS="1",
        CLOUDSDK_STORAGE_PROCESS_COUNT="1",
        CLOUDSDK_STORAGE_THREAD_COUNT="4",
    )
    with (logs / f"{key}.log").open("w", encoding="utf-8") as log:
        process = subprocess.run(
            [
                str(CLI),
                "storage",
                "cp",
                "--no-clobber",
                "--do-not-decompress",
                "--read-paths-from-stdin",
                f"--manifest-path={logs / (key + '.csv')}",
                str(parent),
            ],
            input="\n".join(e["url"] for e in group) + "\n",
            text=True,
            encoding="utf-8",
            stdout=log,
            stderr=log,
            env=env,
            check=False,
        )
    bad = [
        e["path"]
        for e in group
        if not (ROOT / e["path"]).is_file() or (ROOT / e["path"]).stat().st_size != e["bytes"]
    ]
    if process.returncode or bad:
        raise RuntimeError(f"Copy failed: {key}; {bad[:3]}; inspect {logs / (key + '.log')}")
    return {"group": key, "files": len(group), "bytes": sum(e["bytes"] for e in group)}


def download(items, phase):
    """Functionality: Enforce cumulative 10 GB limit and reuse existing raw files.
    Inputs: Whitelist/phase. Outputs: Local originals and append-only transfer ledger.
    Logic: Preflight sizes/free disk; independent destination groups use four workers.
    Constraints: Existing wrong-sized files abort; original files are never replaced.
    """
    unique = {e["path"]: e for e in items}
    missing = []
    for entry in unique.values():
        target = (ROOT / entry["path"]).resolve()
        target.relative_to(ROOT.resolve())
        if target.exists():
            if target.stat().st_size != entry["bytes"]:
                raise ValueError(f"Existing file differs: {target}")
        else:
            missing.append(entry)
    ledger_path = OUT / "new-files.json"
    ledger = read(ledger_path) if ledger_path.exists() else {}
    ledger.update({e["path"]: e["bytes"] for e in missing})
    needed = sum(e["bytes"] for e in missing)
    if sum(ledger.values()) > 10_000_000_000:
        raise ValueError("Cumulative download budget would exceed 10 GB")
    if shutil.disk_usage(ROOT).free < needed + 3_000_000_000:
        raise ValueError("Insufficient free disk with 3 GB reserve")
    write(ledger_path, ledger)
    write(OUT / f"{phase}-download-manifest.json", list(unique.values()))
    groups = collections.defaultdict(list)
    for entry in missing:
        groups[f"{phase}-{entry['session_id']}-{entry['role']}"].append(entry)
    print(
        f"{phase}: new={len(missing)} files {needed / 1e9:.3f} GB; "
        f"reused={len(unique) - len(missing)}",
        flush=True,
    )
    status = {"phase": phase, "groups": len(groups), "completed": [], "training_started": False}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(download_group, groups.items()):
            status["completed"].append(result)
            write(OUT / "status.json", status)
            print(
                f"{phase}: {len(status['completed'])}/{len(groups)} {result['group']}", flush=True
            )
    status["stage"] = "downloaded"
    write(OUT / "status.json", status)


def inspect(sid, index, cat):
    """Functionality: Validate one triplet and extract unchanged E supervision.
    Inputs: Session/frame and official metadata. Outputs: Frame record and exclusion counts.
    Logic: Full PNG decode, calibrated dimensions, existing mask-median extractor.
    Constraints: No model inference, new distance cutoffs, category or label remapping.
    """
    paths = {e["role"]: e["path"] for e in entries(sid, {index})}
    camera = cat[sid]["head_camera"]
    with Image.open(ROOT / paths["rgb"]) as image:
        image.load()
        if image.mode != "RGB" or image.size != (camera["image_width"], camera["image_height"]):
            raise ValueError(f"RGB/calibration mismatch: {sid}/{index}")
        size = image.size
    with Image.open(ROOT / paths["mask"]) as mask:
        mask.load()
        if mask.mode != "RGB" or mask.size != size:
            raise ValueError(f"Mask/RGB mismatch: {sid}/{index}")
    names = {v: k for k, v in read(ROOT / "raw/labelmap.json").items()}
    frame = {**paths, "shapes": {"rgb_shape": [size[1], size[0], 3]}}
    objects, excluded = extract(ROOT, frame, names)
    return {
        "image_id": f"{sid}/{index:06d}",
        "image_path": paths["rgb"],
        "image_size": list(size),
        "objects": objects,
        "camera": camera,
        "source": {
            "session_id": sid,
            "frame_index": index,
            "annotation_type": "HUMAN_ANNOTATED",
            "timestamp_s": index / cat[sid]["fps"],
            "depth": paths["depth"],
            "mask": paths["mask"],
        },
    }, dict(excluded)


def screen():
    """Functionality: Select 40 candidates and download five spaced probe triplets each.
    Inputs: Cached discovery/old split. Outputs: Fixed candidate list and raw probes.
    Logic: Reuse ten sessions; diversify metadata groups across official splits.
    Constraints: Probes rank training candidates only; no performance-based test selection.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "candidates.json").exists():
        raise ValueError("Candidate screen already exists; preserve the reviewed experiment")
    cat = catalogue()
    chosen = [s["session_id"] for s in read(DISCOVERY / "plan-B.json")["sessions"]]
    pool = sorted(set(cat) - set(chosen))
    while len(chosen) < 40:
        signatures = collections.Counter(
            (cat[s]["official_split"], tuple(cat[s]["metadata"]["environment_types"]))
            for s in chosen
        )
        selected = min(
            pool,
            key=lambda s: (
                signatures[
                    (cat[s]["official_split"], tuple(cat[s]["metadata"]["environment_types"]))
                ],
                -int("ELEVATION_CHANGE_STAIRS" in cat[s]["metadata"]["elevation_changes"]),
                -cat[s]["annotation_counts"]["HUMAN_ANNOTATED"],
                s,
            ),
        )
        chosen.append(selected)
        pool.remove(selected)
    candidates = []
    payload = []
    for sid in chosen:
        annotation = read(DISCOVERY / f"annotation-types/{sid}.json")
        manual = sorted(int(k) for k, v in annotation.items() if v == "HUMAN_ANNOTATED")
        probes = [manual[i] for i in np.linspace(0, len(manual) - 1, 5).astype(int)]
        candidates.append(
            {
                "session_id": sid,
                "official_split": cat[sid]["official_split"],
                "metadata": cat[sid]["metadata"],
                "manual_frames": manual,
                "probe_frames": probes,
                "reused_session": chosen.index(sid) < 10,
            }
        )
        payload.extend(entries(sid, set(probes)))
    write(OUT / "candidates.json", candidates)
    download(payload, "probe")


def analyse():
    """Functionality: Inspect probe objects and render contact sheets for route review.
    Inputs: Downloaded five-frame probes. Outputs: Probe JSON/CSV and visual sheets.
    Logic: Unchanged instance extraction, contact sheets retain session indexes.
    Constraints: Probe statistics are not full-session statistics or test scores.
    """
    cat = catalogue()
    summaries = []
    candidates = read(OUT / "candidates.json")
    sheet = None
    for number, candidate in enumerate(candidates):
        sid = candidate["session_id"]
        probe_path = OUT / f"probes/{sid}.json"
        if probe_path.exists():
            records = read(probe_path)
        else:
            records = [inspect(sid, i, cat)[0] for i in candidate["probe_frames"]]
            write(probe_path, records)
        distances = [o["distance_m"] for r in records for o in r["objects"]]
        keys = {(o["class_name"], o["instance_id"]) for r in records for o in r["objects"]}
        near = {
            (o["class_name"], o["instance_id"])
            for r in records
            for o in r["objects"]
            if 2 <= o["distance_m"] < 10
        }
        summaries.append(
            {
                "index": number,
                "session_id": sid,
                "official_split": candidate["official_split"],
                "reused": candidate["reused_session"],
                "objects": len(distances),
                "instance_keys": len(keys),
                "near_instance_keys_2_10": len(near),
                "distance_bins": np.histogram(distances, EDGES)[0].tolist(),
                "environment": candidate["metadata"]["environment_types"],
            }
        )
        if number % 8 == 0:
            sheet = Image.new("RGB", (1600, 8 * 210), "white")
        draw = ImageDraw.Draw(sheet)
        y = (number % 8) * 210
        draw.text(
            (5, y + 2),
            f"{number:02d} {sid} {candidate['official_split']} "
            f"near_keys={len(near)} objects={len(distances)}",
            fill="black",
        )
        for col, record in enumerate(records):
            with Image.open(ROOT / record["image_path"]) as image:
                image.thumbnail((316, 180))
                sheet.paste(image, (col * 320, y + 22))
        if number % 8 == 7 or number == len(candidates) - 1:
            sheet.save(OUT / f"contact-{number // 8 + 1}.jpg", quality=90)
        print(f"probe inspected {number + 1}/{len(candidates)}", flush=True)
    write(OUT / "probe-summary.json", summaries)


def select_training_frames(records):
    """Functionality: Reduce adjacent-frame redundancy without repeating rare instances.
    Inputs: Time-ordered records from one session. Outputs: Selected unique records.
    Logic: Base interval 0.8 seconds; retain intervening frames for new 2-10 m instances or
        near-distance changes of at least max(0.75 m, 20 percent) since the last retained view.
    Constraints: Training only; no fixed distance quotas or claims of statistical independence.
    """
    selected, previous = [], {}
    last_time = -float("inf")
    for record in records:
        if not record["objects"]:
            continue
        current = {
            (o["class_name"], o["instance_id"]): o["distance_m"]
            for o in record["objects"]
            if 2 <= o["distance_m"] < 10
        }
        novel = any(
            key not in previous or abs(distance - previous[key]) >= max(0.75, 0.2 * previous[key])
            for key, distance in current.items()
        )
        timestamp = record["source"]["timestamp_s"]
        if timestamp - last_time >= 0.8 - 1e-6 or novel:
            selected.append(record)
            previous.update(current)
            last_time = timestamp
    return selected


def verify_checksums(items):
    """Functionality: Verify raw bytes against cloud checksums from CLI copy manifests.
    Inputs: Selected explicit object list. Outputs: Local checksum audit.
    Logic: Merge prior/new successful CLI manifests, compare MD5 and expected length.
    Constraints: Missing remote checksums fail; never treat local hashes alone as validation.
    """
    expected = {}
    for folder in (ROOT / "reports/download-logs", OUT / "logs"):
        for path in folder.glob("*.csv"):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    if row.get("Result") == "OK" and row.get("Md5"):
                        expected[row["Source"]] = row["Md5"]
    with (OUT / "checksums.jsonl").open("w", encoding="utf-8") as stream:
        for entry in items:
            payload = (ROOT / entry["path"]).read_bytes()
            actual = base64.b64encode(hashlib.md5(payload, usedforsecurity=False).digest()).decode()
            if len(payload) != entry["bytes"] or expected.get(entry["url"]) != actual:
                raise ValueError(f"Remote checksum missing/mismatch: {entry['path']}")
            stream.write(
                json.dumps(
                    {
                        "path": entry["path"],
                        "bytes": len(payload),
                        "md5_base64": actual,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                )
                + "\n"
            )


def build():
    """Functionality: Download selected manual frames and build separate compatible manifests.
    Inputs: Reviewed selection with visual location groups. Outputs: Reports/processed/config.
    Logic: Validate manual frames, thin redundant training frames, preserve evaluation frames.
    Constraints: Official test and location groups cannot cross splits; no training or old writes.
    """
    selection = read(OUT / "selection.json")
    cat = catalogue()
    candidates = {c["session_id"]: c for c in read(OUT / "candidates.json")}
    groups, seen, payload = {}, set(), []
    for item in selection["sessions"]:
        sid, split, group = item["session_id"], item["split"], item["location_group"]
        if sid in seen or split not in ("train", "val", "test"):
            raise ValueError("Duplicate/invalid session split")
        if groups.setdefault(group, split) != split:
            raise ValueError("Visual location group crosses splits")
        if cat[sid]["official_split"] != ("official_test" if split == "test" else "official_train"):
            raise ValueError("Official split violated")
        seen.add(sid)
        payload.extend(entries(sid, set(candidates[sid]["manual_frames"])))
        metadata_dir = OUT / "metadata" / sid
        metadata_dir.mkdir(parents=True, exist_ok=True)
        for source, name in (
            (DISCOVERY / f"metadata/{sid}.json", "description.json"),
            (DISCOVERY / f"annotation-types/{sid}.json", "annotation-types.json"),
        ):
            target = metadata_dir / name
            if not target.exists():
                target.write_bytes(source.read_bytes())
            elif target.read_bytes() != source.read_bytes():
                raise ValueError("Metadata changed")
    download(payload, "selected")
    verify_checksums(payload)
    processed = ROOT / "processed-agnostic-diversity-01"
    if processed.exists():
        raise ValueError("Processed output already exists; never overwrite a completed subset")
    all_records = collections.defaultdict(list)
    session_stats, excluded = [], collections.Counter()
    for item in selection["sessions"]:
        sid = item["session_id"]
        records = []
        for i in candidates[sid]["manual_frames"]:
            cache = OUT / "validated" / sid / f"{i:06d}.json"
            if cache.exists():
                record, rejected = read(cache)
            else:
                record, rejected = inspect(sid, i, cat)
                write(cache, [record, rejected])
            record["source"]["location_group"] = item["location_group"]
            records.append(record)
            excluded.update(rejected)
        objects = [o for r in records for o in r["objects"]]
        distances = [o["distance_m"] for o in objects]
        session_stats.append(
            {
                **item,
                "frames": len(records),
                "effective_images": sum(bool(r["objects"]) for r in records),
                "objects": len(objects),
                "distance_bins": np.histogram(distances, EDGES)[0].tolist(),
                "instance_keys": len({(o["class_name"], o["instance_id"]) for o in objects}),
            }
        )
        retained = select_training_frames(records) if item["split"] == "train" else records
        session_stats[-1]["retained_images"] = len(retained)
        session_stats[-1]["retained_objects"] = sum(len(r["objects"]) for r in retained)
        all_records[item["split"]].extend(retained)
        print(
            f"validated {len(session_stats)}/{len(selection['sessions'])} {sid} "
            f"images={len(records)} objects={len(objects)}",
            flush=True,
        )
    processed.mkdir()
    report = {
        "distance_target": TARGET,
        "annotation_policy": "human_only",
        "splits": {},
        "sessions": session_stats,
        "excluded_instances": dict(excluded),
        "distance_bin_labels_m": BIN_NAMES,
        "new_download_bytes": sum(read(OUT / "new-files.json").values()),
        "location_isolation": selection["location_isolation"],
        "training_started": False,
        "sampling": "Human frames only; training base interval 0.8 s plus new 2-10 m instances "
        "or near-distance changes >= max(0.75 m, 20 percent); no duplicate rows or oversampling; "
        "empty train frames excluded; all human evaluation frames retained. "
        "Sparse 2.5 Hz subset is not a full-rate temporal benchmark.",
    }
    for split, records in all_records.items():
        path = processed / f"{split}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        objects = [o for r in records for o in r["objects"]]
        distances = [o["distance_m"] for o in objects]
        report["splits"][split] = {
            "images": len(records),
            "effective_images": sum(bool(r["objects"]) for r in records),
            "objects": len(objects),
            "manifest_sha256": digest(path),
            "sessions": [s["session_id"] for s in session_stats if s["split"] == split],
            "distance_bins": np.histogram(distances, EDGES)[0].tolist(),
            "distance_median_m": float(np.median(distances)),
            "classes": dict(collections.Counter(o["class_name"] for o in objects)),
        }
    write(processed / "manifest.json", report)
    write(OUT / "summary.json", report)
    config = read(BACKEND / "object_distance/config.sanpo-agnostic.json")
    config["processed"] = processed.relative_to(BACKEND).as_posix()
    write(OUT / "config.sanpo-agnostic-diversity.json", config)
    with (OUT / "distance-distribution.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["split", "distance_bin_m", "objects", "fraction"])
        for split, stats in report["splits"].items():
            for label, count in zip(BIN_NAMES, stats["distance_bins"], strict=True):
                writer.writerow([split, label, count, count / stats["objects"]])
    write(
        OUT / "status.json",
        {
            "stage": "verified",
            "training_started": False,
            "summary_sha256": hashlib.sha256((OUT / "summary.json").read_bytes()).hexdigest(),
        },
    )


def main():
    """Functionality: Run explicit preparation phase. Inputs: CLI phase. Outputs: Artifacts.
    Logic: Separate screening, inspection and reviewed selection. Constraints: No model training.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["screen", "analyse", "build"])
    args = parser.parse_args()
    {"screen": screen, "analyse": analyse, "build": build}[args.phase]()


if __name__ == "__main__":
    main()
