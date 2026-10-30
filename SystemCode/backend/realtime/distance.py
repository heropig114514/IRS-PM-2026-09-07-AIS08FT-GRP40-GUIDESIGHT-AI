"""Responsibilities: Add geometry and per-track low-frequency depth around unchanged A fusion.
Implementation: Scalar geometry, isolated last-valid cache, per-stream strategy/schedule.
Related Modules: realtime.distance_config/depth/detector/tracking.
Declaration Index: distance_box; GeometryDistanceEstimator; GeometryDistanceEstimator.__init__;
GeometryDistanceEstimator.estimate; GeometryDistanceEstimator.fuse; TemporalDepthEntry;
TemporalDepthCache; TemporalDepthCache.__init__; TemporalDepthCache.reset;
TemporalDepthCache.apply; DistanceStrategy; DistanceStrategy.__init__; DistanceStrategy.fuse;
DistanceStrategy.apply; DistanceStrategy._fuse_depth.
Variable Index: logger — stream lifecycle diagnostics; cache.entries — scalar history only;
strategy.frame_index/stream_key — first-frame schedule and tracker epoch/geometry identity.
strategy.fast_roi — explicit A-only host partition; false retains original GPU medians.
strategy.depth_predict_ms — host duration of the actual depth call, zero when skipped.
"""

import logging
import math
import time
from dataclasses import dataclass

from realtime.depth import fuse_object_depth

logger = logging.getLogger(__name__)


def distance_box(box, distance, source):
    """Functionality: Preserve existing aliases while adding compatible B/C distance fields.
    Inputs: detector box, nullable meters and provenance. Outputs: copied canonical box.
    Logic: retain legacy coordinates/class/confidence/ID. Constraints: never modifies A fusion.
    """
    return {**box, "class_name": box["label"], "confidence": box["score"],
            "bbox": list(box["xyxy"]), "distance_m": distance, "distance_source": source}


class GeometryDistanceEstimator:
    """Functionality: Estimate camera-axis meters using fy * assumed height / bbox height.
    Logic: scale calibrated fy to the decoded frame, whose coordinates detection already returns.
    Constraints: no depth model, image processing, category guessing or calibration fitting.
    """

    def __init__(self, config):
        """Functionality: Store operator-selected calibration and category heights once.
        Inputs: DistanceConfig. Outputs: independent estimator. Logic: copy immutable profile map.
        Constraints: fy corresponds to reference_width_px, not the detector's padded 640 square.
        """
        self.config, self.heights = config, dict(config.object_heights_m)

    def estimate(self, box, image_size):
        """Functionality: Return finite positive meters or None for unsupported/invalid boxes.
        Inputs: legacy detector box and decoded (width,height). Outputs: float or None.
        Logic: validate full xyxy/frame bounds and category, use continuous pixel bbox height.
        Constraints: posture, occlusion and truncated objects can bias this simple baseline.
        """
        try:
            height = self.heights.get(box.get("label"))
            x1, y1, x2, y2 = map(float, box["xyxy"])
            width, frame_height = image_size
            if height is None or not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or not (
                    0 <= x1 < x2 <= width and 0 <= y1 < y2 <= frame_height):
                return None
            focal = self.config.focal_length_px * width / self.config.reference_width_px
            value = focal * height / (y2 - y1)
            return value if math.isfinite(value) and value > 0 else None
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
            return None

    def fuse(self, boxes, image_size):
        """Functionality: Attach geometry meters/null through the existing distance interface.
        Inputs: detector/tracker boxes and frame size. Outputs: copied canonical boxes.
        Logic: independent scalar estimate per box. Constraints: unknown classes remain null.
        """
        return [distance_box(box, self.estimate(box, image_size), "geometry") for box in boxes]


@dataclass(frozen=True)
class TemporalDepthEntry:
    """Functionality: Retain the last valid measurement and its identity/acquisition time.
    Logic: scalar immutable entry. Constraints: contains no depth maps or motion predictions.
    """

    last_distance: float
    last_depth_frame: int
    timestamp: float
    class_name: str


class TemporalDepthCache:
    """Functionality: Hold last-valid distance only for continuously visible, same-class IDs.
    Logic: remove absent IDs immediately; depth frames correct, skipped frames read only.
    Constraints: intentionally conservative on occlusion; never transfers a value to a new ID.
    """

    def __init__(self):
        """Functionality: Allocate session-local history. Inputs: None. Outputs: empty cache.
        Logic: instance ownership. Constraints: no global or cross-camera history.
        """
        self.entries = {}

    def reset(self):
        """Functionality: Clear history at tracker epoch/geometry changes.
        Inputs: None. Outputs: empty cache. Logic: discard scalar entries. Constraints: no ID reuse.
        """
        self.entries.clear()

    def apply(self, boxes, *, depth_frame, frame_index, timestamp):
        """Functionality: Correct measurements or supply Last Valid Distance on skipped frames.
        Inputs: associated boxes, actual depth-call flag, stream frame index and source seconds.
        Outputs: canonical boxes with distance_source. Logic: purge absent/class-changed IDs;
        store only valid positive measurements; never cache None or unconfirmed IDs.
        Constraints: invalid fresh depth stays null this frame; later skipped frames may reuse the
        previous valid measurement while continuously visible. State is bounded by current IDs.
        """
        active = {box.get("track_id") for box in boxes
                  if type(box.get("track_id")) is int and box["track_id"] > 0}
        self.entries = {key: entry for key, entry in self.entries.items() if key in active}
        results = []
        for box in boxes:
            identity, label = box.get("track_id"), box["label"]
            entry = self.entries.get(identity) if type(identity) is int and identity > 0 else None
            if entry is not None and entry.class_name != label:
                del self.entries[identity]
                entry = None
            distance = box.get("distance_m") if depth_frame else (
                entry.last_distance if entry is not None else None)
            valid = (not isinstance(distance, bool) and isinstance(distance, int | float) and
                     math.isfinite(distance) and distance > 0)
            if depth_frame and valid and type(identity) is int and identity > 0:
                self.entries[identity] = TemporalDepthEntry(distance, frame_index, timestamp, label)
            source = ("depth" if depth_frame else "cached") if valid else "unavailable"
            results.append(distance_box(box, distance if valid else None, source))
        return results


class DistanceStrategy:
    """Functionality: Select A/B/C while delegating A's exact median depth fusion unchanged.
    Logic: frame 0 then every interval; tracking epoch and frame geometry scope temporal state.
    Constraints: caller supplies existing tracker IDs and serial frames; no alternative tracker.
    """

    def __init__(self, config):
        """Functionality: Create strategy and independent scalar state before the frame loop.
        Inputs: validated DistanceConfig. Outputs: strategy. Logic: create only the selected helper.
        Constraints: no models are loaded here; geometry cannot call a supplied depth model.
        """
        self.config = config
        self.fast_roi = False
        self.geometry = (GeometryDistanceEstimator(config)
                         if config.distance_mode == "geometry" else None)
        self.cache = TemporalDepthCache() if config.distance_mode == "temporal_depth" else None
        self.frame_index, self.stream_key = 0, None
        self.depth_predict_ms = 0.0

    def _fuse_depth(self, image, boxes, depth_estimator):
        """Functionality: Preserve exact depth/fusion while measuring the actual model call.
        Inputs: original image, associated boxes and selected depth model. Outputs: fused boxes.
        Logic: use host clocks without extra CUDA synchronization; retain unchanged ROI algorithm.
        Constraints: pending device work may charge to fusion; this is not pure kernel timing.
        """
        started = time.perf_counter()
        depth_map = depth_estimator.predict(image)
        self.depth_predict_ms = (time.perf_counter() - started) * 1000
        return self.fuse(depth_map, boxes, image.size)

    def fuse(self, depth_map, boxes, image_size):
        """Functionality: Select the unchanged A median or opt-in exact host partition.
        Inputs: aligned depth, detections and original size. Outputs: canonical distances.
        Logic: fast mode downloads the depth plane once, then partitions valid ROI values.
        Constraints: A only; B/C keep their original path, no approximation or class filtering.
        """
        if self.fast_roi and self.config.distance_mode == "depth":
            from realtime.depth_fast import fuse_depth_host

            return fuse_depth_host(depth_map, boxes, image_size)
        return fuse_object_depth(depth_map, boxes, image_size)

    def apply(self, image, boxes, depth_estimator, *, timestamp, epoch=0):
        """Functionality: Execute only the selected distance stage for one associated frame.
        Inputs: original image/boxes, optional model, timestamp and tracker epoch.
        Outputs: (boxes, actual_depth_call), reset per-frame depth_predict_ms.
        Logic: B uses geometry; A uses unchanged fusion;
        C resets on stream change and invokes depth only on scheduled frames before cache update.
        Constraints: C requires a model; invalid depth raises unchanged errors, never a fallback.
        """
        self.depth_predict_ms = 0.0
        mode = self.config.distance_mode
        if mode == "geometry":
            return self.geometry.fuse(boxes, image.size), False
        if mode == "depth":
            return (self._fuse_depth(image, boxes, depth_estimator), True
                    ) if depth_estimator is not None else (boxes, False)
        if depth_estimator is None:
            raise ValueError("temporal_depth requires a depth estimator")
        key = (epoch, image.size)
        if self.stream_key != key:
            self.cache.reset()
            self.stream_key, self.frame_index = key, 0
            logger.info("Temporal distance stream reset epoch=%d interval=%d",
                        epoch, self.config.depth_interval)
        depth_frame = self.frame_index % self.config.depth_interval == 0
        if depth_frame:
            boxes = self._fuse_depth(image, boxes, depth_estimator)
        boxes = self.cache.apply(boxes, depth_frame=depth_frame, frame_index=self.frame_index,
                                 timestamp=timestamp)
        self.frame_index += 1
        return boxes, depth_frame
