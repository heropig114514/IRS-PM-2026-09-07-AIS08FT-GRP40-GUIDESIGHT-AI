"""Responsibilities: Prepare and optionally publish the explicit PRS ZeroGPU file allowlist.
Implementation: Stage only inference source/public fixture; one authenticated HF commit, no retries.
Related Modules: zerogpu; core.detector; models/yolo26n.json; frontend/detection-sample.jpg.
Declaration Index: stage_bundle — reproducible allowlist; publish — single commit; main — CLI.
Variable Index: ROOT/SPACE_ID — fixed local root and owned deployment target;
FILES — source/destination allowlist; MAX_FILE_BYTES — text commit boundary.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path

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
    "zerogpu/protocol.py": "zerogpu/protocol.py",
    "zerogpu/runtime.py": "zerogpu/runtime.py",
    "core/__init__.py": "core/__init__.py",
    "core/detector.py": "core/detector.py",
    "models/yolo26n.json": "models/yolo26n.json",
}


def stage_bundle():
    """Functionality: Prepare deployable text files without exposing unrelated repository data.
    Inputs: fixed project source allowlist and public sample; Outputs: (bundle Path, file list).
    Logic: copy maintained files, encode public fixture JSON, report content hashes only.
    Constraints: does not delete files, include tokens/.env/private source or upload camera media.
    """
    bundle = ROOT / ".space-bundle"
    for source, destination in FILES.items():
        target = bundle / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
    sample = (ROOT.parent / "frontend/detection-sample.jpg").read_bytes()
    (bundle / "sample.json").write_text(
        json.dumps({"schema_version": 1, "jpeg_base64": base64.b64encode(sample).decode("ascii")}),
        encoding="utf-8",
    )
    files = sorted([*FILES.values(), "sample.json"])
    return bundle, files


def publish(bundle, files):
    """Functionality: Publish this allowlist to the user's existing Space in one commit.
    Inputs: staged bundle/list; HF_TOKEN env or user's existing standard local HF cache token.
    Outputs: commit response JSON; Logic: bounded text NDJSON commit, Bearer auth, fixed target.
    Constraints: no token creation/storage/logging, paid hardware upgrade, retries or repo deletion.
    """
    token_path = Path(os.environ.get("HF_TOKEN_PATH", Path.home() / ".cache/huggingface/token"))
    token = os.environ.get("HF_TOKEN", "")
    if not token and token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("HF authentication is missing; login or configure a private HF token")
    records = [{"key": "header", "value": {
        "summary": "Deploy verified YOLO26n FP32 ZeroGPU inference",
        "description": "Explicit allowlist; shared JPEG processing; no private configuration.",
    }}]
    for name in files:
        data = (bundle / name).read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"Oversized staged file: {name}")
        records.append({"key": "file", "value": {
            "content": base64.b64encode(data).decode("ascii"), "path": name, "encoding": "base64",
        }})
    body = "\n".join(json.dumps(record) for record in records).encode("utf-8")
    request = urllib.request.Request(
        f"https://huggingface.co/api/spaces/{SPACE_ID}/commit/main",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/x-ndjson"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    """Functionality: Stage by default; publish only when --publish is explicitly selected.
    Inputs: CLI arguments and source files/authentication; Outputs: file hashes or commit URL.
    Logic: fixed allowlist stage then optional one-shot commit.
    Constraints: default does not mutate HF; errors propagate without printing secret contents.
    """
    parser = argparse.ArgumentParser(description="Stage or deploy the PRS ZeroGPU Space")
    parser.add_argument("--publish", action="store_true")
    options = parser.parse_args()
    bundle, files = stage_bundle()
    if options.publish:
        result = publish(bundle, files)
        print(json.dumps({"space": SPACE_ID, "commit": result.get("commitUrl")}))
    else:
        print(json.dumps({"bundle": str(bundle), "files": {
            name: hashlib.sha256((bundle / name).read_bytes()).hexdigest() for name in files
        }}))


if __name__ == "__main__":
    main()
