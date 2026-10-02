"""Responsibilities: Memory-only JSON/JPEG contract for the optional ZeroGPU service.
Implementation: Strict base64 boundaries reuse the existing decoder, renderer and model manifest.
Related Modules: zerogpu.runtime/app; core.detector; tools.deploy_space.
Declaration Index: decode_input — validate request; encode_result — serialize annotated output.
Variable Index: MAX_BASE64_LENGTH — encoded 1 MiB limit; SCHEMA_VERSION — protocol version.
"""

import base64
import binascii
import math

from core.detector import MAX_FRAME_BYTES, DetectionError, annotate, decode_frame

MAX_BASE64_LENGTH = 4 * ((MAX_FRAME_BYTES + 2) // 3)
SCHEMA_VERSION = 1


def decode_input(payload):
    """Functionality: Validate a camera request before allocating a GPU.
    Inputs: payload dict with schema_version and jpeg_base64.
    Outputs: RGB Pillow image; invalid input raises DetectionError.
    Logic: reject unknown schema/keys, bound text before strict base64 and existing JPEG decoding.
    Constraints: no frame files, metadata extraction, external calls or automatic retries.
    """
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "jpeg_base64"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise DetectionError("invalid_request", "Invalid request schema.", 400)
    encoded = payload["jpeg_base64"]
    if not isinstance(encoded, str) or not 0 < len(encoded) <= MAX_BASE64_LENGTH:
        raise DetectionError("frame_size", "Encoded image is empty or oversized.", 413)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise DetectionError("invalid_base64", "Invalid image encoding.", 400) from error
    return decode_frame(raw)


def encode_result(image, boxes, inference_ms):
    """Functionality: Serialize a successful detection without writing media files.
    Inputs: validated RGB image, decoded boxes, finite nonnegative GPU-stage milliseconds.
    Outputs: JSON-compatible result including JPEG, boxes, count and explicit model identity.
    Logic: use the existing JPEG80 renderer and base64; GPU timing excludes queue/network/render.
    Constraints: schema1, FP32, COCO80; failures propagate and never become empty successes.
    """
    if not math.isfinite(inference_ms) or inference_ms < 0:
        raise ValueError("Invalid GPU-stage timing")
    return {
        "schema_version": SCHEMA_VERSION,
        "model": "yolo26n",
        "backend": "PyTorch ZeroGPU",
        "precision": "FP32",
        "confidence_threshold": 0.25,
        "classes": 80,
        "width": image.width,
        "height": image.height,
        "count": len(boxes),
        "inference_ms": round(inference_ms, 3),
        "boxes": boxes,
        "jpeg_base64": base64.b64encode(annotate(image, boxes)).decode("ascii"),
    }
