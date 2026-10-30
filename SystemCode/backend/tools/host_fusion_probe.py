"""Responsibilities: Test one bounded depth transfer and exact FP32 host ROI partitioning.
Implementation: Copy the same-frame CUDA plane once, then reuse canonical box assembly with an
explicit NumPy reducer preserving positive-finite filtering and half-before-add arithmetic.
Related Modules: realtime.depth/distance; tools.benchmark_video/resident_host_fusion_probe.
Declaration Index: host_roi_median; host_fuse_object_depth.
Variable Index: None. No map, output, tracker or scratch buffer survives a call.
"""

from unittest.mock import patch

import numpy as np

from core.detector import DetectionError
from realtime.depth import fuse_object_depth, validate_depth_map


def host_roi_median(depth_map, bounds):
    """Functionality: Compute the CUDA FP32 control's exact statistical median on a host plane.
    Inputs: FP32 NumPy HxW map and validated bounds or None. Outputs: Python float or NaN sentinel.
    Logic: copy only finite positive ROI values; partition at central ranks without a full sort.
    Even counts use two FP32 divisions then FP32 addition, matching the original CUDA operations.
    Constraints: no float64 median, approximation, source mutation or retained cross-frame state;
    odd values bypass arithmetic to preserve subnormals. Not the production NumPy reducer.
    """
    if not isinstance(depth_map, np.ndarray) or depth_map.dtype != np.float32:
        raise ValueError("Host fusion requires an explicit FP32 NumPy plane")
    if bounds is None:
        return float("nan")
    x1, y1, x2, y2 = bounds
    roi = depth_map[y1:y2, x1:x2]
    valid = roi[np.isfinite(roi) & (roi > 0)]
    count = valid.size
    if not count:
        return float("nan")
    middle = count // 2
    if count % 2:
        valid.partition(middle)
        return float(valid[middle])
    valid.partition((middle - 1, middle))
    return float(valid[middle - 1] / np.float32(2) + valid[middle] / np.float32(2))


def host_fuse_object_depth(depth_map, boxes, image_size):
    """Functionality: Evaluate exact host fusion with its complete transfer cost inside the call.
    Inputs: aligned FP32 CUDA plane, original boxes and bounded image size. Outputs: canonical
    fused box dictionaries. Logic: validate before transfer; synchronously copy one owned plane;
    temporarily substitute only ROI reduction while original assembly/diagnostics remain intact.
    Constraints: explicit single-worker experiment only, no CPU/precision fallback or retry;
    no transfer for an empty box list, no full-map retention, no production entry-point import.
    """
    import torch

    validate_depth_map(depth_map)
    if (not isinstance(depth_map, torch.Tensor) or depth_map.device.type != "cuda"
            or depth_map.dtype != torch.float32):
        raise ValueError("Host fusion requires an explicit FP32 CUDA source")
    if (tuple(depth_map.shape) != (image_size[1], image_size[0])
            or not 0 < min(image_size) <= max(image_size) <= 640):
        raise DetectionError("depth_map_size", "Expected aligned bounded host-fusion input.", 502)
    if not boxes:
        return fuse_object_depth(depth_map, boxes, image_size)
    plane = depth_map.detach().cpu().numpy()
    with patch("realtime.depth.roi_median", host_roi_median):
        return fuse_object_depth(plane, boxes, image_size)
