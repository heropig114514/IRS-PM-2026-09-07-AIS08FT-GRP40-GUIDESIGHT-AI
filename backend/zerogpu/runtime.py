"""Responsibilities: Verified original YOLO26n weights and FP32 PyTorch execution.
Implementation: Pin artifact hash/class order; fuse the eval model as in the existing ONNX export.
Related Modules: zerogpu.app/protocol; core.detector supplies unchanged pre/postprocessing.
Declaration Index: load_model — verified startup; forward — one CUDA inference.
Variable Index: None. Model/path/manifest belong to the caller; no hidden device fallback.
"""

import hashlib
import time


def load_model(path, manifest, device):
    """Functionality: Load the trusted pinned model once during application startup.
    Inputs: local weight Path, manifest dict, explicit device string.
    Outputs: fused FP32 eval module on the requested device, or a startup exception.
    Logic: verify SHA256 before deserialization; select the existing export's one-to-one head.
    Constraints: CUDA placement occurs at module root in ZeroGPU, never per-request; no downloads.
    """
    import torch
    import ultralytics
    from ultralytics import YOLO

    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["source_sha256"]:
        raise ValueError("Weight hash does not match the pinned manifest")
    if ultralytics.__version__ != manifest["build"]["ultralytics"]:
        raise ValueError("Unexpected Ultralytics version")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    model = YOLO(str(path)).model.float().eval()
    # The pinned export sets end2end=True for nms=False. Reapply that selection when loading
    # older checkpoint attribute layouts with the current pinned Ultralytics implementation.
    model.end2end = True
    names = [model.names[i] for i in range(len(model.names))]
    head = model.model[-1]
    if names != manifest["names"] or len(names) != 80 or not head.end2end or head.max_det != 300:
        raise ValueError("Model class order or end-to-end head does not match")
    # The ONNX exporter fuses the same eval branches and uses the plain top-k export path.
    model.fuse(verbose=False)
    head.export, head.format, head.dynamic = True, "onnx", False
    return model.to(device)


def forward(model, tensor):
    """Functionality: Execute one fixed-shape FP32 frame on the allocated CUDA device.
    Inputs: startup model, contiguous NumPy FP32 [1,3,640,640] from existing letterbox.
    Outputs: CPU NumPy predictions [1,300,6] and synchronized GPU-stage milliseconds.
    Logic: inference-mode H2D, forward and D2H; reject unexpected dtype/device/output structure.
    Constraints: no CPU substitution, autocast, NMS, resizing, compilation or mixed precision.
    """
    import torch

    started = time.monotonic()
    with torch.inference_mode():
        inputs = torch.from_numpy(tensor).to("cuda")
        if inputs.dtype != torch.float32 or next(model.parameters()).device.type != "cuda":
            raise RuntimeError("Expected a CUDA FP32 model and input")
        prediction = model(inputs)
        if not isinstance(prediction, torch.Tensor) or prediction.dtype != torch.float32:
            raise RuntimeError("Unexpected model output type")
        result = prediction.cpu().numpy()
    return result, (time.monotonic() - started) * 1000
