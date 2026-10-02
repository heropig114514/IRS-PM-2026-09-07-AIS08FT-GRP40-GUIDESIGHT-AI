"""Responsibilities: Record real fixed-Space validation under the existing four-frame protocol.
Implementation: JPEG80/max640 fixtures, fixed HTTPS/SSE transport and same-class IoU comparison.
Related Modules: core.hf_services; tools.compare_acceleration; prior baseline.json.
Declaration Index: fixture_bytes — original fixture preparation; main — real API validation.
Variable Index: None; CLI paths select reference/input/output; private token is read from env.
"""

import argparse
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image

from core.hf_services import TIMEOUT_SECONDS, exchange, validate_result
from tools.compare_acceleration import compare


def fixture_bytes(path):
    """Functionality: Reproduce the existing benchmark's camera-format bytes exactly once.
    Inputs: public source JPEG path; Outputs: JPEG80 bytes and resized dimensions.
    Logic: RGB/max-edge640/bilinear/round as benchmark_laptop.prepared_image before decoding.
    Constraints: preserves evaluation inputs; do not re-encode decoded prepared images again.
    """
    with Image.open(path) as original:
        image = original.convert("RGB")
        scale = min(1, 640 / max(image.size))
        image = image.resize((round(image.width * scale), round(image.height * scale)),
                             Image.Resampling.BILINEAR)
        output = io.BytesIO()
        image.save(output, "JPEG", quality=80)
        return output.getvalue(), image.size


def main():
    """Functionality: Verify real Space outputs, recording partial progress on failure.
    Inputs: CLI evaluation/reference/output; optional private HF_INFERENCE_TOKEN env.
    Outputs: JSON boxes/latencies and complete-set comparison, no returned image files.
    Logic: one call per fixture, validated contract, checkpoint report after each response.
    Constraints: no retry, no raw prediction hash available, no mAP or mobile-camera claim.
    """
    parser = argparse.ArgumentParser()
    for key in ("evaluation", "reference", "output"):
        parser.add_argument("--" + key, required=True, type=Path)
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8"))["evaluation"]
    files = sorted(args.evaluation.glob("*.jpg"))
    if {path.name for path in files} != set(reference) or len(files) != 4:
        raise ValueError("Expected exactly the original four public fixtures")
    report = {"backend": "PyTorch ZeroGPU", "precision": "FP32", "evaluation": {},
              "complete": False, "raw_output_hash_available": False}
    try:
        for path in files:
            raw, size = fixture_bytes(path)
            started = time.monotonic()
            deadline = started + TIMEOUT_SECONDS
            body = json.dumps({"data": [{
                "schema_version": 1, "jpeg_base64": base64.b64encode(raw).decode("ascii"),
            }]}).encode()
            receipt = exchange("POST", "/gradio_api/call/detect", body, deadline)
            event = receipt["event_id"]
            if not isinstance(event, str) or len(event) != 32 or any(
                    char not in "0123456789abcdef" for char in event):
                raise ValueError("Invalid event ID")
            result = exchange("GET", f"/gradio_api/call/detect/{event}", None, deadline, True)
            if not isinstance(result, list) or len(result) != 1:
                raise ValueError("Invalid envelope")
            data = result[0]
            validate_result(data, size)
            report["evaluation"][path.name] = {
                "boxes": data["boxes"], "raw_sha256": None,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "inference_ms": data["inference_ms"],
            }
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"{path.name}: {data['count']} boxes", flush=True)
        report["comparison"] = compare(reference, report["evaluation"])
        report["comparison"]["bit_identical_frames"] = None
        report["complete"] = True
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["comparison"]))


if __name__ == "__main__":
    main()
