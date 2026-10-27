"""Responsibilities: Resident YOLO26 metric depth and original-frame object/depth fusion.
Implementation: Official DepthPredictor removes its own letterbox; ROI statistics stay on device.
Related Modules: realtime.detector; zerogpu.app/live_worker; core.detector.DetectionError.
Declaration Index: roi_bounds; validate_depth_map; roi_median; get_object_distance;
fuse_object_depth; DepthEstimator; DepthEstimator.__init__; DepthEstimator.activate;
DepthEstimator.prepare_source; DepthEstimator.predict.
Variable Index: logger — safe load/alignment/invalid-ROI diagnostics;
DEPTH_EDGE — explicit independent depth input size, not the detector's experimental parameter.
DepthEstimator.model/model_name/active — loaded checkpoint identity and allocation device state.
DepthEstimator.image_size/half — explicit depth-only settings; defaults preserve baseline A.
DepthEstimator.fast_input — opt-in direct BGR packing; default PIL ingress is retained.
"""

import logging
import math
from pathlib import Path

import numpy as np

from core.detector import DetectionError

logger = logging.getLogger(__name__)
DEPTH_EDGE = 768


def roi_bounds(bbox, shape):
    """Functionality: Clamp finite xyxy coordinates to half-open pixel bounds.
    Inputs: four numeric coordinates and depth (height,width). Outputs: bounds or None.
    Logic: reject reversed/empty boxes before floor/ceil; intersect with the frame.
    Constraints: no inferred distance from geometry; wholly outside/invalid ROIs stay unavailable.
    """
    try:
        coordinates = [float(value) for value in bbox]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(coordinates) != 4 or not all(math.isfinite(value) for value in coordinates):
        return None
    x1, y1, x2, y2 = coordinates
    if x2 <= x1 or y2 <= y1:
        return None
    height, width = shape
    bounds = (max(0, min(width, math.floor(x1))), max(0, min(height, math.floor(y1))),
              max(0, min(width, math.ceil(x2))), max(0, min(height, math.ceil(y2))))
    return bounds if bounds[2] > bounds[0] and bounds[3] > bounds[1] else None


def validate_depth_map(depth_map):
    """Functionality: Require one nonempty real-valued original-frame depth plane.
    Inputs: NumPy array or Torch tensor. Outputs: None or safe depth_map_size error.
    Logic: inspect shape and dtype; validate Torch lazily so web runtime needs no Torch.
    Constraints: batches/channels/complex/object arrays are not silently squeezed or reinterpreted.
    """
    if isinstance(depth_map, np.ndarray):
        valid_type = np.issubdtype(depth_map.dtype, np.floating)
    else:
        try:
            import torch
        except ImportError:
            valid_type = False
        else:
            valid_type = isinstance(depth_map, torch.Tensor) and depth_map.is_floating_point()
    if not valid_type or depth_map.ndim != 2 or min(depth_map.shape) <= 0:
        raise DetectionError("depth_map_size", "Expected a nonempty floating HxW depth map.", 502)


def roi_median(depth_map, bounds):
    """Functionality: Reduce a valid ROI using positive finite metric depth only.
    Inputs: validated map and clamped bounds or None. Outputs: scalar median or NaN sentinel.
    Logic: average both central values for even samples; Torch reduction stays on its device.
    Constraints: NaN sentinel is internal only; never serialized or substituted with a distance.
    """
    if isinstance(depth_map, np.ndarray):
        if bounds is None:
            return float("nan")
        x1, y1, x2, y2 = bounds
        roi = depth_map[y1:y2, x1:x2]
        valid = roi[np.isfinite(roi) & (roi > 0)]
        return float(np.median(valid.astype(np.float64))) if valid.size else float("nan")
    import torch

    if bounds is None:
        return depth_map.new_tensor(float("nan"), dtype=torch.float32)
    x1, y1, x2, y2 = bounds
    roi = depth_map[y1:y2, x1:x2]
    valid = roi[torch.isfinite(roi) & (roi > 0)]
    if valid.dtype in (torch.float16, torch.bfloat16):
        valid = valid.float()
    if not valid.numel():
        return depth_map.new_tensor(float("nan"), dtype=torch.float32)
    middle = valid.numel() // 2
    if valid.numel() % 2:
        return valid.kthvalue(middle + 1).values
    # Halve before adding to avoid overflow when both finite central depths are large.
    return valid.kthvalue(middle).values / 2 + valid.kthvalue(middle + 1).values / 2


def get_object_distance(depth_map, bbox) -> float | None:
    """Functionality: Return median object depth in meters, or None for unavailable ROI depth.
    Inputs: floating NumPy/Torch HxW map already aligned to the frame, original xyxy bbox.
    Outputs: positive finite Python float or None; malformed map raises DetectionError.
    Logic: clamp ROI, filter NaN/Inf/zero/negative samples, compute statistical median.
    Constraints: Torch performs only a scalar host read; batch fusion avoids one read per object.
    """
    validate_depth_map(depth_map)
    value = float(roi_median(depth_map, roi_bounds(bbox, depth_map.shape)))
    return value if math.isfinite(value) and value > 0 else None


def fuse_object_depth(depth_map, boxes, image_size):
    """Functionality: Extend existing detections with canonical fields and metric ROI medians.
    Inputs: original-frame map, existing detection dictionaries, image (width,height).
    Outputs: new dictionaries retaining legacy fields plus class_name/confidence/bbox/distance_m.
    Logic: reject unaligned map; reduce on device then transfer only the distance vector once.
    Constraints: no mutation of detector boxes, full-map CPU transfer, geometric heuristic or retry.
    Invalid ROI/no positive finite samples yields None and a count-only diagnostic.
    """
    validate_depth_map(depth_map)
    if tuple(depth_map.shape) != (image_size[1], image_size[0]):
        raise DetectionError("depth_map_size", "Depth map is not aligned to the input frame.", 502)
    medians = [roi_median(depth_map, roi_bounds(box["xyxy"], depth_map.shape)) for box in boxes]
    if isinstance(depth_map, np.ndarray):
        values = medians
    elif medians:
        import torch

        values = torch.stack(medians).detach().cpu().tolist()
    else:
        values = []
    fused = []
    missing = 0
    for box, value in zip(boxes, values, strict=True):
        distance = float(value) if math.isfinite(value) and value > 0 else None
        missing += distance is None
        fused.append({**box, "class_name": box["label"], "confidence": box["score"],
                      "bbox": list(box["xyxy"]), "distance_m": distance})
    if missing:
        logger.warning("Object depth unavailable objects=%d total=%d", missing, len(boxes))
    return fused


class DepthEstimator:
    """Functionality: Keep a separate official YOLO26 depth model resident for video frames.
    Logic: deserialize once on CPU; activate once inside the native/ZeroGPU CUDA allocation.
    Constraints: serial frames; local weights only; no downloads, calibration, precision changes
    to detection, CPU fallback or retained allocation-local predictor across ZeroGPU tasks.
    """

    def __init__(self, weights_path, expected_version, *, image_size=DEPTH_EDGE, half=False,
                 fast_input=False):
        """Functionality: Load local metric depth weights once and verify task/build identity.
        Inputs: local .pt path, pinned version, independent depth size/precision and fast_input.
        Outputs: loaded model, inactive device state or safe missing/load error.
        Logic: check file before YOLO construction to prevent its automatic asset downloader.
        Constraints: defaults preserve depth768/FP32; never changes detector precision or weights.
        """
        if type(image_size) is not int or image_size < 32 or image_size % 32:
            raise ValueError("Depth image size must be a positive multiple of 32")
        if type(half) is not bool:
            raise ValueError("Depth half must be boolean")
        self.image_size, self.half = image_size, half
        self.fast_input = fast_input
        path = Path(weights_path)
        if path.suffix != ".pt" or not path.is_file():
            logger.error("Depth model missing or invalid local weight path")
            raise DetectionError("depth_model_missing", "Provide local YOLO26 depth .pt weights.",
                                 503)
        try:
            import ultralytics
            from ultralytics import YOLO

            if ultralytics.__version__ != expected_version:
                raise ValueError("Unexpected Ultralytics version")
            self.model = YOLO(str(path))
            if self.model.task != "depth":
                raise ValueError("Expected YOLO depth task")
        except Exception as error:
            logger.error("Depth model load failed type=%s", type(error).__name__)
            raise DetectionError("depth_model_load", "Unable to load YOLO26 depth weights.",
                                 503) from None
        self.active = False
        self.model_name = path.stem
        logger.info("YOLO26 depth model loaded task=depth input_edge=%d", self.image_size)

    def activate(self):
        """Functionality: Place the loaded depth model on the allocation GPU once.
        Inputs: loaded model and actual CUDA availability. Outputs: active reusable model.
        Logic: place source model on CUDA; predictor applies requested precision at first use.
        Constraints: must run inside spaces.GPU on HF; idempotent within one allocation.
        """
        if self.active:
            return
        try:
            import torch

            if not torch.cuda.is_available():
                raise RuntimeError("CUDA required for depth")
            self.model.to("cuda")
            self.active = True
        except Exception as error:
            logger.error("Depth GPU activation failed type=%s", type(error).__name__)
            raise DetectionError("depth_inference_failed", "Depth model requires usable CUDA.",
                                 503) from None
        logger.info("Depth model activated device=cuda requested_precision=%s",
                    "FP16" if self.half else "FP32")

    def prepare_source(self, image):
        """Functionality: Select original PIL ingress or exact one-pass BGR packing.
        Inputs: decoded image. Outputs: unchanged PIL or contiguous uint8 BGR array.
        Logic: opt-in packing avoids a separate RGB array and RGB-to-BGR conversion.
        Constraints: fast input requires RGB; official resize/normalization remain unchanged.
        """
        if not self.fast_input:
            return image
        from realtime.depth_fast import pack_bgr

        return pack_bgr(image)

    def predict(self, image):
        """Functionality: Infer dense metric depth from the same decoded RGB frame as detection.
        Inputs: PIL RGB image and activated resident model. Outputs: aligned CUDA float HxW map.
        Logic: pinned official predictor handles its own letterbox/padding; verify original shape,
        resize any remaining full-frame resolution difference with nearest on the same device.
        Constraints: reject missing/batched/CPU maps; no map download, heuristic, or detection-only
        fallback; nearest preserves invalid values so ROI filtering can exclude them.
        """
        if not self.active:
            raise DetectionError("depth_inference_failed", "Depth model is not GPU activated.", 503)
        try:
            import torch
            import torch.nn.functional as functional

            results = self.model.predict(source=self.prepare_source(image),
                                         imgsz=self.image_size, device="cuda",
                                         half=self.half,
                                         verbose=False, save=False, stream=False)
            if len(results) != 1 or tuple(results[0].orig_shape) != (image.height, image.width):
                raise DetectionError("depth_map_size", "Depth result frame identity mismatch.", 502)
            depth = getattr(results[0], "depth", None)
            depth_map = getattr(depth, "data", None)
            if not isinstance(depth_map, torch.Tensor) or depth_map.device.type != "cuda":
                raise DetectionError("depth_map_size", "Expected a dense CUDA depth tensor.", 502)
            validate_depth_map(depth_map)
            target = (image.height, image.width)
            if tuple(depth_map.shape) != target:
                logger.info("Resizing full-frame depth source=%s target=%s",
                            tuple(depth_map.shape), target)
                depth_map = functional.interpolate(depth_map[None, None].float(), size=target,
                                                   mode="nearest")[0, 0]
            return depth_map
        except DetectionError:
            logger.warning("Depth result rejected")
            raise
        except Exception as error:
            logger.error("Depth inference failed type=%s", type(error).__name__)
            raise DetectionError("depth_inference_failed", "YOLO26 depth inference failed.",
                                 502) from None
