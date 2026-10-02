"""Responsibilities: Gradio ZeroGPU API and public-fixture verification UI.
Implementation: Startup hash-verified CUDA model; GPU allocated only around raw tensor inference.
Related Modules: zerogpu.protocol/runtime; copied core.detector; tools.deploy_space stages assets.
Declaration Index: ensure_weights; gpu_forward; detect; preview; status.
Variable Index: ROOT/manifest/weight/model — verified startup assets;
logger — safe diagnostics; demo/request/result/rendered/verify/info — Gradio UI and API components.
"""

import base64
import hashlib
import json
import logging
import time
import urllib.request
import uuid
from pathlib import Path

# spaces must patch CUDA before importing PyTorch/Ultralytics; imports below are intentional.
import spaces
# ruff: noqa: I001 -- CUDA emulation must initialize before other third-party modules.
import gradio as gr

from core.detector import DetectionError, decode_boxes, letterbox
from zerogpu.protocol import decode_input, encode_result
from zerogpu.runtime import forward, load_model

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prs.zerogpu")
manifest = json.loads((ROOT / "models/yolo26n.json").read_text(encoding="utf-8"))
weight = ROOT / "models/yolo26n.pt"


def ensure_weights(path, specification):
    """Functionality: Fetch official public weights once when absent, with a verified boundary.
    Inputs: local target path and pinned manifest; Outputs: disk model asset or startup exception.
    Logic: one HTTPS transfer with 60-second socket timeout and 16 MiB maximum; SHA256 before save.
    Constraints: camera media never enters this path; no retries or alternate weights on failure.
    """
    if path.exists():
        return
    logger.info("Downloading pinned official model asset")
    with urllib.request.urlopen(specification["source_url"], timeout=60) as response:
        data = response.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Model asset exceeds download limit")
    if hashlib.sha256(data).hexdigest() != specification["source_sha256"]:
        raise ValueError("Downloaded model hash does not match")
    path.write_bytes(data)


ensure_weights(weight, manifest)
model = load_model(weight, manifest, "cuda")
logger.info("Pinned YOLO26n FP32 model initialized; GPU execution still requires a test request")


@spaces.GPU
def gpu_forward(tensor):
    """Functionality: Allocate the shared GPU only for the existing tensor inference stage.
    Inputs: validated FP32 tensor; Outputs: predictions and GPU-stage timing from runtime.forward.
    Logic: ZeroGPU decorator controls allocation/release; Constraints: default GPU size/duration.
    """
    return forward(model, tensor)


def detect(payload):
    """Functionality: Return annotated JPEG/boxes for a camera frame or the public bus fixture.
    Inputs: schema1 dict, or None selecting the bundled public fixture; Outputs: result JSON.
    Logic: CPU validation/letterbox, allocated GPU forward, shared decoding/rendering in memory.
    Constraints: no frame persistence, retries or fallback; logs IDs/count/timing, never media.
    """
    request_id, started = uuid.uuid4().hex[:12], time.monotonic()
    try:
        if payload is None:
            payload = json.loads((ROOT / "sample.json").read_text(encoding="utf-8"))
        image = decode_input(payload)
        tensor, scale, left, top = letterbox(image)
        predictions, inference_ms = gpu_forward(tensor)
        boxes = decode_boxes(predictions, image.size, (scale, left, top), manifest["names"])
        response = encode_result(image, boxes, inference_ms)
        logger.info(
            "Detection completed id=%s count=%d gpu_stage_ms=%.1f total_ms=%.1f",
            request_id, len(boxes), inference_ms, (time.monotonic() - started) * 1000,
        )
        return response
    except DetectionError as error:
        logger.warning("Detection rejected id=%s code=%s", request_id, error.code)
        raise gr.Error(f"{error.code} / {request_id}: {error.detail}") from None
    except gr.Error:
        # ZeroGPU's own quota/allocation errors are already intended for Gradio users.
        logger.warning(
            "GPU allocation/service rejected id=%s; see the returned Gradio error", request_id
        )
        raise
    except Exception as error:
        logger.error("Detection failed id=%s exception_type=%s", request_id, type(error).__name__)
        raise gr.Error(
            f"GPU detection failed / {request_id}; check Space logs and quota."
        ) from None


def preview(response):
    """Functionality: Show an already returned public-fixture JPEG without a Gradio media file.
    Inputs: own result dict; Outputs: HTML data-image; Logic: validate base64 before embedding.
    Constraints: no HTML from clients is interpolated; this UI event does not allocate another GPU.
    """
    encoded = response["jpeg_base64"]
    base64.b64decode(encoded, validate=True)
    return (
        '<img alt="YOLO26n annotated sample" style="max-width:100%" '
        f'src="data:image/jpeg;base64,{encoded}">'
    )


def status():
    """Functionality: Expose model configuration without spending GPU quota.
    Inputs: verified startup manifest; Outputs: readiness metadata; Logic: no model inference.
    Constraints: initialized means weights loaded, not proof of GPU availability or queue latency.
    """
    return {
        "status": "initialized", "schema_version": 1, "model": "yolo26n",
        "classes": 80, "precision": "FP32", "backend": "PyTorch ZeroGPU",
        "confidence_threshold": 0.25, "input_shape": [1, 3, 640, 640],
        "source_sha256": manifest["source_sha256"],
    }


with gr.Blocks(title="PRS YOLO26n ZeroGPU", analytics_enabled=False) as demo:
    gr.Markdown("# PRS YOLO26n · ZeroGPU\nCOCO 80 classes · FP32 · 640 input · confidence 0.25")
    gr.Markdown(
        "Test the public bus image below. GPU calls may queue and consume your daily free quota. "
        "The API accepts JPEG frames in JSON and returns annotated JPEGs in memory."
    )
    request = gr.JSON(value=None, visible=False)
    result = gr.JSON(visible=False)
    rendered = gr.HTML()
    verify = gr.Button("Test public sample")
    info = gr.JSON(label="Model configuration", value=status())
    verify.click(detect, inputs=request, outputs=result, api_name="detect").success(
        preview, inputs=result, outputs=rendered, api_visibility="private", queue=False,
    )
    demo.load(status, outputs=info, api_name="status", queue=False)

