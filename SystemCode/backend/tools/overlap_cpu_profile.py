"""Responsibilities: Diagnose host stages around the same-frame tracking/depth overlap.
Implementation: Transparent original-call wrappers; keep Graph inference identity and SDK waits.
Related Modules: tools.inference_profile/benchmark_video/tracking_overlap_probe.
Declaration Index: OverlapStageTrace; OverlapStageTrace.install; OverlapStageTrace.write.
Variable Index: None. Trace state and clocks are owned by the inherited StageTrace.
"""

import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch

from tools.inference_profile import StageTrace


class OverlapStageTrace(StageTrace):
    """Functionality: Attribute remaining preparation, fusion and drawing costs with overlap.
    Logic: wrap CPU-facing boundaries without replacing the installed Graph inference callable.
    Constraints: nested host elapsed includes GPU waits; never sum stages or claim kernel time.
    """

    def install(self, detector, stack):
        """Functionality: Install reversible diagnostics after the first real Graph preparation.
        Inputs: warmed full detector and owning ExitStack. Outputs: original-call wrappers.
        Logic: measure original tracker, SDK pre/postprocessing, ROI reductions and Pillow calls.
        Constraints: Graph identity and original SDK synchronization stay intact; association is
        nested in depth inference. No model calls, retries, CPU transfers or altered output here.
        """
        from PIL import Image, ImageDraw
        from ultralytics.utils import ops

        import realtime.depth as depth
        import realtime.detector as pipeline
        import realtime.distance as distance

        predictor = detector.depth_estimator.model.predictor
        targets = [(predictor, name, f"depth_{name}") for name in
                   ("preprocess", "postprocess")]
        targets += [(detector.object_tracker, name, f"tracking_{name}")
                    for name in ("associate", "enrich")]
        targets += [(pipeline, name, name) for name in
                    ("decode_frame", "letterbox", "decode_boxes", "annotate")]
        targets += [(ImageDraw.ImageDraw, name, f"draw_{name}") for name in
                    ("textbbox", "text", "rectangle")]
        targets += [(ops.Profile, "time", "depth_profile_clock"),
                    (distance, "fuse_object_depth", "depth_fusion"),
                    (depth, "roi_median", "roi_median"),
                    (Image.Image, "copy", "image_copy"), (Image.Image, "save", "jpeg_save")]
        for owner, name, label in targets:
            original = getattr(owner, name)
            source = Path(inspect.getsourcefile(original))
            self.sources[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
            stack.enter_context(patch.object(owner, name, self.wrap(label, original)))

    def write(self, output):
        """Functionality: Retain all events with an explicit overlapping timing contract.
        Inputs: existing output directory. Outputs: stage trace and metadata.
        Logic: reuse original complete/failure counters, then annotate the persisted report.
        Constraints: stages are nested, ROI host wall may wait for GPU, and cold setup is excluded.
        """
        metadata = super().write(output)
        metadata.update(stage_timing_model="overlapping_cpu_depth",
                        scope="Nested host diagnostics only; compare untraced before/after runs")
        path = output / "stage-trace.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**report, **metadata}, indent=2) + "\n", encoding="utf-8")
        return metadata
