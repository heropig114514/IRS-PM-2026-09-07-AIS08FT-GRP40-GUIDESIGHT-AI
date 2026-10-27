"""Responsibilities: Stage/publish only the current ZeroGPU live implementation.
Implementation: Validate the measured COCO-qualified plan before content-addressed staging;
one parent-pinned HF commit publishes its exact configuration and removes retired APIs.
Related Modules: zerogpu.app/live_transport/live_worker/runtime/aoti/jit_cache;
realtime.depth/distance/distance_config/performance/tracking/risk;
core.inference_plan;
core.detector/detection_model; realtime.protocol.
Declaration Index: stage_bundle; publish; main.
Variable Index: ROOT/SPACE_ID — fixed workspace/owned Space; FILES — source/destination allowlist;
RETIRED — explicit obsolete files; MAX_FILE_BYTES — per-file upload bound.
"""

import argparse
import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

from core.inference_plan import read_plan

ROOT = Path(__file__).resolve().parents[1]
SPACE_ID = "Heropig114514/prs-yolo26"
MAX_FILE_BYTES = 2 * 1024 * 1024
FILES = {
    "zerogpu/entrypoint.py": "app.py",
    "zerogpu/README.md": "README.md",
    "zerogpu/requirements.txt": "requirements.txt",
    "zerogpu/packages.txt": "packages.txt",
    "zerogpu/__init__.py": "zerogpu/__init__.py",
    "zerogpu/app.py": "zerogpu/app.py",
    "zerogpu/runtime.py": "zerogpu/runtime.py",
    "zerogpu/aoti.py": "zerogpu/aoti.py",
    "zerogpu/jit_cache.py": "zerogpu/jit_cache.py",
    "zerogpu/live_transport.py": "zerogpu/live_transport.py",
    "zerogpu/live_worker.py": "zerogpu/live_worker.py",
    "realtime/__init__.py": "realtime/__init__.py",
    "realtime/protocol.py": "realtime/protocol.py",
    "realtime/detector.py": "realtime/detector.py",
    "realtime/depth.py": "realtime/depth.py",
    "realtime/distance.py": "realtime/distance.py",
    "realtime/distance_config.py": "realtime/distance_config.py",
    "realtime/performance.py": "realtime/performance.py",
    "realtime/tracking.py": "realtime/tracking.py",
    "realtime/risk.py": "realtime/risk.py",
    "core/__init__.py": "core/__init__.py",
    "core/detector.py": "core/detector.py",
    "core/detection_model.py": "core/detection_model.py",
    "core/inference_plan.py": "core/inference_plan.py",
    "models/yolo26n.json": "models/yolo26n.json",
    "models/distance-config.json": "models/distance-config.json",
    "models/inference-plan.json": "models/inference-plan.json",
}
RETIRED = (
    "zerogpu/protocol.py",
    "zerogpu/experiments.py",
    "zerogpu/streaming.py",
    "zerogpu/boxes.py",
    "zerogpu/live.py",
    "zerogpu/profiling.py",
    "sample.json",
    "evaluation/000000000036.jpg",
    "evaluation/000000000042.jpg",
    "evaluation/000000000049.jpg",
    "evaluation/000000000061.jpg",
)


def stage_bundle():
    """Functionality: Prepare exactly the maintained live source, without credentials or media.
    Inputs: fixed local FILES and the actual measured plan/model manifest.
    Outputs: content-addressed bundle and sorted relative names.
    Logic: require three complete full-COCO confirmations within the quality boundary, then hash
    source bytes to select an owned subdirectory and write only explicit destinations.
    Constraints: missing/invalid evidence aborts before staging; no deletion, private files,
    weights, fixtures, installation or HF mutation. Metadata validation is not target GPU proof.
    """
    sources = {destination: (ROOT / source).read_bytes() for source, destination in FILES.items()}
    read_plan(ROOT / "models/inference-plan.json", json.loads(sources["models/yolo26n.json"]))
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    bundle = ROOT / ".space-bundle/live-only" / identity
    for name, raw in sources.items():
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return bundle, sorted(sources)


def publish(bundle, files):
    """Functionality: Publish reviewed live source and remove retired files in one HF commit.
    Inputs: staged allowlist; explicit HF_TOKEN env or excluded backend .env.hf.
    Outputs: commit metadata with deleted paths and parent SHA.
    Logic: read current owned repository metadata, intersect explicit deletions with existing files,
    send bounded NDJSON file/deletedFile operations with parentCommit to reject concurrent changes.
    Constraints: verified TLS, no anonymous/cache identity, retries, hardware changes or media;
    credentials never appear in output or source. HF commit history preserves recovery.
    """
    token = os.environ.get("HF_TOKEN") or dotenv_values(ROOT / ".env.hf").get("HF_TOKEN", "")
    if not isinstance(token, str) or not token.startswith("hf_") or any(c.isspace() for c in token):
        raise RuntimeError("Configure a valid private HF_TOKEN")
    headers = {"Authorization": f"Bearer {token}"}
    request = urllib.request.Request(
        f"https://huggingface.co/api/spaces/{SPACE_ID}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        repository = json.load(response)
    existing = {item["rfilename"] for item in repository["siblings"]}
    deleted = sorted(existing.intersection(RETIRED))
    plan = read_plan(bundle / "models/inference-plan.json",
                     json.loads((bundle / "models/yolo26n.json").read_bytes()))
    records = [
        {
            "key": "header",
            "value": {
                "summary": (f"Deploy COCO-qualified {plan['precision']} "
                            f"{plan['backend']} live inference"),
                "description": (
                    f"Kaggle Notebook v{plan['provenance']['version']}; "
                    f"candidate={plan['provenance']['candidate']}; "
                    f"summary_sha256={plan['provenance']['summary_sha256']}. "
                    "Three full5000-image confirmations; AP drop <=0.5 percentage points. "
                    "Preserve original weights/640/confidence0.25/JPEG80; "
                    "compile on the actual startup GPU, reuse verified artifacts, "
                    "and verify short live execution separately."
                ),
                "parentCommit": repository["sha"],
            },
        }
    ]
    for name in files:
        if name not in FILES.values():
            raise ValueError("File outside deployment allowlist")
        data = (bundle / name).read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Oversized staged file: " + name)
        records.append(
            {
                "key": "file",
                "value": {
                    "content": base64.b64encode(data).decode("ascii"),
                    "path": name,
                    "encoding": "base64",
                },
            }
        )
    records.extend({"key": "deletedFile", "value": {"path": name}} for name in deleted)
    body = b"".join(json.dumps(record).encode() + b"\n" for record in records)
    request = urllib.request.Request(
        f"https://huggingface.co/api/spaces/{SPACE_ID}/commit/main",
        data=body,
        headers={**headers, "Content-Type": "application/x-ndjson"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    return {**result, "deleted": deleted, "parent": repository["sha"]}


def main():
    """Functionality: Stage by default; publish only with explicit CLI selection.
    Inputs: --publish and private authentication; Outputs: safe hashes or commit metadata.
    Logic: fixed allowlist then optional one-shot commit; Constraints: failures propagate.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    options = parser.parse_args()
    bundle, files = stage_bundle()
    if options.publish:
        print(json.dumps({"space": SPACE_ID, **publish(bundle, files)}))
    else:
        print(
            json.dumps(
                {
                    "bundle": str(bundle),
                    "files": {
                        name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
                        for name in files
                    },
                }
            )
        )


if __name__ == "__main__":
    main()
