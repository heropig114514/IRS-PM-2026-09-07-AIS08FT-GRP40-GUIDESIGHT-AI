"""Responsibilities: Opt-in pixel-exact YOLO RGB letterbox without an intermediate PIL canvas.
Implementation: Original Pillow bilinear resize, direct ROI division and explicit tensor padding.
Related Modules: core.detector.letterbox; unified_evaluation.adapters/preprocess_profile.
Declaration Index: letterbox_fast.
Variable Index: None; caller owns the reusable FP32 output, with no hidden image cache.
"""

import numpy as np
from PIL import Image

from core.detector import INPUT_EDGE


def letterbox_fast(image, output=None, *, stages=None):
    """Functionality: Preserve baseline pixels/coordinates while removing canvas copies.
    Inputs: RGB PIL image, optional contiguous FP32 NCHW output and diagnostic timing dict.
    Outputs: tensor, scale, left/top; every output element is overwritten on every call.
    Logic: keep Pillow bilinear/rounding; fill only padding and divide resized RGB into its ROI.
    Constraints: no interpolation, channel, normalization or resolution change; stages is CPU-only
    diagnostic timing, omitted in formal inference. Resize/array export still allocate memory.
    """
    if stages is not None:
        import time

        started = time.perf_counter()
    if image.mode != "RGB":
        raise ValueError("Fast detector preprocessing requires RGB")
    if output is None:
        output = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
    if (
        output.shape != (1, 3, INPUT_EDGE, INPUT_EDGE)
        or output.dtype != np.float32
        or not output.flags.c_contiguous
    ):
        raise ValueError("Input destination must be contiguous FP32 [1,3,640,640]")
    scale = INPUT_EDGE / max(image.size)
    width, height = round(image.width * scale), round(image.height * scale)
    left, top = (INPUT_EDGE - width) // 2, (INPUT_EDGE - height) // 2
    if stages is not None:
        now = time.perf_counter()
        stages["geometry_buffer_ms"], started = (now - started) * 1000, now
    resized = image.resize((width, height), Image.Resampling.BILINEAR)
    if stages is not None:
        now = time.perf_counter()
        stages["resize_ms"], started = (now - started) * 1000, now
    padding = np.float32(114) / np.float32(255)
    output[:, :, :top, :].fill(padding)
    output[:, :, top + height :, :].fill(padding)
    output[:, :, top : top + height, :left].fill(padding)
    output[:, :, top : top + height, left + width :].fill(padding)
    if stages is not None:
        now = time.perf_counter()
        stages["padding_ms"], started = (now - started) * 1000, now
    pixels = np.asarray(resized)
    if stages is not None:
        now = time.perf_counter()
        stages["numpy_export_ms"], started = (now - started) * 1000, now
    view = pixels.transpose(2, 0, 1)[None]
    if stages is not None:
        now = time.perf_counter()
        stages["layout_view_ms"], started = (now - started) * 1000, now
    np.divide(
        view,
        np.float32(255),
        out=output[:, :, top : top + height, left : left + width],
        dtype=np.float32,
    )
    if stages is not None:
        stages["normalize_store_ms"] = (time.perf_counter() - started) * 1000
    return output, scale, left, top
