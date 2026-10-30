"""Responsibilities: Opt-in exact A input packing and amortized host ROI medians.
Implementation: PIL raw BGR packing and one depth transfer followed by NumPy partition.
Related Modules: realtime.depth; realtime.distance; unified_evaluation.a_performance.
Declaration Index: pack_bgr; host_median; fuse_depth_host.
Variable Index: logger records missing distances without identifying image contents.
"""

import logging
import math

import numpy as np

from core.detector import DetectionError
from realtime.depth import roi_bounds, validate_depth_map

logger = logging.getLogger(__name__)


def pack_bgr(image):
    """Functionality: Pack RGB PIL pixels directly into contiguous BGR uint8 bytes.
    Inputs: nonempty RGB image. Outputs: HWC BGR array owning immutable packed bytes.
    Logic: PIL's C raw packer performs channel reversal in the export pass.
    Constraints: reject other modes; no resize, normalization, precision or pixel changes.
    """
    if image.mode != "RGB" or min(image.size) <= 0:
        raise ValueError("Fast depth input requires a nonempty RGB PIL image")
    return np.frombuffer(image.tobytes("raw", "BGR"), dtype=np.uint8).reshape(
        image.height, image.width, 3
    )


def host_median(plane, bounds):
    """Functionality: Reproduce Torch median reduction with exact central order statistics.
    Inputs: floating NumPy plane and clamped bounds. Outputs: Python scalar or NaN.
    Logic: filter positive finite values, partition once, halve before adding for even counts.
    Constraints: retain FP32/64 arithmetic; FP16 promotes to FP32 like the original Torch path.
    """
    if bounds is None:
        return float("nan")
    x1, y1, x2, y2 = bounds
    roi = plane[y1:y2, x1:x2]
    valid = roi[np.isfinite(roi) & (roi > 0)]
    if valid.dtype == np.float16:
        valid = valid.astype(np.float32)
    count = valid.size
    if not count:
        return float("nan")
    middle = count // 2
    valid.partition((middle - 1, middle) if count % 2 == 0 else middle)
    if count % 2:
        return float(valid[middle])
    half = valid.dtype.type(0.5)
    return float(valid[middle - 1] * half + valid[middle] * half)


def fuse_depth_host(depth_map, boxes, image_size):
    """Functionality: Fuse A boxes using one map download and exact host medians.
    Inputs: aligned Torch FP32 depth plane, boxes and image width/height.
    Outputs: original canonical fields and nullable distances in unchanged order.
    Logic: validate, skip download for no boxes, transfer once then partition each ROI on CPU.
    Constraints: explicitly trades a full FP32 map copy for fewer GPU kernels/synchronizations;
    unsupported dtype fails; no silent fallback, decimation, cropping changes or class gating.
    """
    import torch

    validate_depth_map(depth_map)
    if tuple(depth_map.shape) != (image_size[1], image_size[0]):
        raise DetectionError("depth_map_size", "Depth map is not aligned to the input frame.", 502)
    if not isinstance(depth_map, torch.Tensor) or depth_map.dtype != torch.float32:
        raise ValueError("Fast A fusion requires a Torch FP32 depth map")
    if not boxes:
        return []
    plane = depth_map.detach().cpu().numpy()
    fused, missing = [], 0
    for box in boxes:
        value = host_median(plane, roi_bounds(box["xyxy"], plane.shape))
        distance = value if math.isfinite(value) and value > 0 else None
        missing += distance is None
        fused.append(
            {
                **box,
                "class_name": box["label"],
                "confidence": box["score"],
                "bbox": list(box["xyxy"]),
                "distance_m": distance,
            }
        )
    if missing:
        logger.warning("Object depth unavailable objects=%d total=%d", missing, len(boxes))
    return fused
