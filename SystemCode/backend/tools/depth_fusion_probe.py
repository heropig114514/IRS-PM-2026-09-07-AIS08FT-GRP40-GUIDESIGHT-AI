"""Responsibilities: Experiment with a fixed-size GPU reduction for exact ROI medians.
Implementation: Sort positive finite values before infinity sentinels; select central values
with device-side counts, avoiding the dynamically sized boolean-index result of the control.
Related Modules: realtime.depth; tools.benchmark_inference/validate_depth_fusion.
Declaration Index: sorted_roi_median.
Variable Index: None. This candidate is not selected by production inference.
"""


def sorted_roi_median(depth_map, bounds):
    """Functionality: Compute the existing positive finite median without a host sample count.
    Inputs: validated floating Torch HxW map, clamped nonempty bounds or None.
    Outputs: same-device scalar median or NaN sentinel, with FP16/BF16 promoted to FP32.
    Logic: mask invalid values with infinity, sort the full ROI, gather central positions
    using a device count; preserve the control's half-before-add arithmetic for even counts.
    Constraints: standalone serial experiment; no NumPy fallback, approximation, map transfer,
    input mutation or host scalar read. Odd medians are returned directly to preserve subnormals.
    """
    import torch

    if not isinstance(depth_map, torch.Tensor) or not depth_map.is_floating_point():
        raise ValueError("Fusion probe requires a validated floating Torch map")
    if bounds is None:
        return depth_map.new_tensor(float("nan"), dtype=torch.float32)
    x1, y1, x2, y2 = bounds
    roi = depth_map[y1:y2, x1:x2].reshape(-1)
    if not roi.numel():
        raise ValueError("Fusion probe requires nonempty clamped bounds")
    valid = torch.isfinite(roi) & (roi > 0)
    if roi.dtype in (torch.float16, torch.bfloat16):
        roi = roi.float()
    ordered = torch.where(valid, roi, float("inf")).sort().values
    count = valid.sum()
    lower = ordered.gather(0, ((count - 1) // 2).clamp_min(0).reshape(1))[0]
    upper = ordered.gather(0, (count // 2).reshape(1))[0]
    median = torch.where(count.remainder(2) == 0, lower / 2 + upper / 2, upper)
    return torch.where(count > 0, median, float("nan"))
