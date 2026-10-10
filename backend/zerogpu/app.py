"""Responsibilities: Expose the single bounded ZeroGPU live detection API.
Implementation: Require a complete COCO-qualified plan; initialize its eager model or compile
its AOT package or JIT cache on the actual startup GPU; validate capability before one live task.
Related Modules: live_transport/live_worker/runtime/aoti/jit_cache; realtime.depth/tracking/risk;
inference_plan; deploy_space.
Declaration Index: ensure_weights; compile_startup; gpu_live; detect_live.
Variable Index: ROOT/manifest/weight — pinned startup assets; plan/config/model — qualified
execution identity and eager model or AOT/JIT descriptor; logger — safe diagnostics;
demo/live_request/live_result/live_api — the sole Gradio allocation endpoint.
OMP_NUM_THREADS/MKL_NUM_THREADS/OPENBLAS_NUM_THREADS — one CPU thread as in Kaggle protocol;
set before Spaces imports Torch and initializes its inter-op thread count.
depth_path/depth_estimator — explicitly enabled local depth weights and CPU-root-loaded model.
tracking_path/tracking_config — optional explicit JSON motion profile, defaults for BoT-SORT;
risk_path/risk_config — optional explicit JSON TTC/rule profile, validated once at startup;
mutable tracker/history are created only inside each allocated worker.
"""

import hashlib
import json
import logging
import os
import urllib.request
from pathlib import Path

# Match the measured CPU policy before Spaces reads Torch's thread count and patches CUDA.
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
# Compiler files survive worker forks; this policy must precede Spaces' Torch imports.
from zerogpu.jit_cache import configure_cache

configure_cache(Path(__file__).resolve().parent.parent / "compiled-jit")
import spaces
# ruff: noqa: I001, E402 -- Cache environment precedes Spaces/Torch initialization deliberately.
import gradio as gr

from zerogpu.live_transport import transport
from zerogpu.live_worker import run_worker
from zerogpu.runtime import load_model
from core.inference_plan import read_plan
from realtime.depth import DepthEstimator
from realtime.tracking import read_tracking_config
from realtime.risk import read_risk_config

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO)
# Functionality: Suppress transport INFO records containing ZeroGPU capability-token URLs.
# Logic: keep our phase/count/error diagnostics; Constraints: warnings/errors remain visible.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("prs.zerogpu")
manifest = json.loads((ROOT / "models/yolo26n.json").read_text(encoding="utf-8"))
weight = ROOT / "models/yolo26n.pt"
plan = read_plan(ROOT / "models/inference-plan.json", manifest)
config = plan["config"]
depth_path = os.environ.get("PRS_DEPTH_WEIGHTS", "").strip()
depth_estimator = (DepthEstimator(ROOT / depth_path, manifest["build"]["ultralytics"])
                   if depth_path else None)
tracking_path = os.environ.get("PRS_TRACKING_CONFIG", "").strip()
tracking_config = read_tracking_config(ROOT / tracking_path if tracking_path else None)
risk_path = os.environ.get("PRS_RISK_CONFIG", "").strip()
risk_config = read_risk_config(ROOT / risk_path if risk_path else None)


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


@spaces.GPU(duration=1500)
def compile_startup():
    """Functionality: Build the selected compiled implementation on the serving GPU once.
    Inputs: pinned weights/manifest and validated explicit plan. Outputs: local package descriptor.
    Logic: explicit JIT-cache or serial-AOT startup branch, preserving the measured configuration.
    Constraints: no public API, camera input, foreign GPU binary,
    paid hardware change or implicit recompilation on live failure; startup errors stop readiness.
    """
    if config.get("compile"):
        from zerogpu.jit_cache import prepare_cache

        return prepare_cache(weight, manifest, config, ROOT / "compiled-jit")
    from zerogpu.aoti import compile_package

    initialized = load_model(weight, manifest, "cuda", config)
    return compile_package(initialized, manifest, config, ROOT / "compiled-inference")


ensure_weights(weight, manifest)
model = (compile_startup() if config.get("aoti") or config.get("compile") else
         load_model(weight, manifest, "cuda", config))
logger.info("Qualified model initialized backend=%s precision=%s notebook_version=%s "
            "summary_sha256=%s; live GPU verification remains required",
            plan["backend"], plan["precision"], plan["provenance"]["version"],
            plan["provenance"]["summary_sha256"])


@spaces.GPU
def gpu_live(capability):
    """Functionality: Hold one allocated GPU while the separate live peer supplies frames.
    Inputs: capability, detection model/config, depth and immutable tracking/risk profiles.
    Outputs: count/timing; Logic:55s maximum loopback handoff/reused CUDA Graph.
    Constraints: original default GPU size/60s decorator ceiling; graph lifetime is one allocation,
    no cross-task callable retention, retries, changed numerical settings or additional live calls.
    """
    return run_worker(capability, model, manifest, config=config, depth_estimator=depth_estimator,
                      tracking_config=tracking_config, risk_config=risk_config)


def detect_live(capability):
    """Functionality: Start the connected live session through Gradio's quota context.
    Inputs: secret capability returned only to its admitted WS peer; Outputs: GPU session summary.
    Logic: reject stale/foreign/already-claimed parent session before calling the GPU decorator.
    Constraints: Gradio request preserves caller's HF identity; no anonymous quota workaround,
    implicit repeat allocation or camera persistence; quota errors remain explicit Gradio errors.
    """
    import hmac
    import re

    session = transport.session
    if (not isinstance(capability, str) or re.fullmatch(r"[0-9a-f]{64}", capability) is None
            or session is None or session.started is not None or session.stopped.is_set()
            or not hmac.compare_digest(capability, session.capability)):
        raise gr.Error("Connect a fresh live WebSocket session before requesting GPU.")
    try:
        return gpu_live(session.worker_capability)
    except gr.Error:
        logger.warning("Live GPU allocation/service rejected")
        raise
    except Exception as error:
        logger.error("Live GPU session failed type=%s", type(error).__name__)
        raise gr.Error("Live GPU session failed; check Space logs and quota.") from None


with gr.Blocks(title="PRS YOLO26n ZeroGPU", analytics_enabled=False) as demo:
    gr.Markdown(f"# PRS YOLO26n · ZeroGPU\nCOCO 80 classes · {plan['precision']} · "
                "640 input · confidence 0.25")
    gr.Markdown("Start camera detection from the PRS live page. One video test uses one bounded "
                "GPU allocation from the caller's daily free quota.")
    live_request = gr.Textbox(visible=False)
    live_result = gr.JSON(visible=False)
    live_api = gr.Button(visible=False)
    live_api.click(detect_live, inputs=live_request, outputs=live_result, api_name="detect_live")
