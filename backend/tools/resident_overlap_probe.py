"""Responsibilities: Expose same-frame tracking/depth overlap to the isolated browser experiment.
Implementation: Original first-frame Graph preparation; reuse the overlap adapter on later frames.
Related Modules: tools.resident_graph_probe/tracking_overlap_probe; realtime.services.
Declaration Index: OverlapServiceProbe; OverlapServiceProbe.__init__;
OverlapServiceProbe.__call__; build_overlap_detector.
Variable Index: None. The original service owns worker serialization and tracker session resets.
"""

import time

from tools.resident_graph_probe import GraphServiceProbe
from tools.tracking_overlap_probe import TrackingOverlapProbe


class OverlapServiceProbe(GraphServiceProbe):
    """Functionality: Keep first-response Graph readiness and add explicit steady overlap.
    Logic: delegate original capture/failure behavior; replace only its retained pipeline callable.
    Constraints: no device-observation events in this unprofiled browser path; never default.
    """

    def __init__(self, detector):
        """Functionality: Bind first-frame path. Inputs: full detector. Outputs: probe.
        Logic: parent retains original predict before installation. Constraints: no CUDA work here.
        """
        super().__init__(detector)
        self.overlap = None

    def __call__(self, jpeg):
        """Functionality: Account for capture/adapter setup in first response and reuse thereafter.
        Inputs: current JPEG. Outputs: unchanged result and explicit overlapping stage metadata.
        Logic: first real frame prepares Graph; bind the saved original pipeline, avoiding recursion
        through detector.predict; later parent calls use that adapter on the same worker.
        Constraints: parent latches inference failures; setup failures also latch, never retry.
        """
        started = time.perf_counter()
        metadata, result = super().__call__(jpeg)
        if self.overlap is None:
            try:
                self.overlap = TrackingOverlapProbe(self.detector, self.graph)
                self.overlap.original_predict = self.original_predict
                self.original_predict = self.overlap
            except BaseException:
                self.failed = True
                raise
        return {**metadata, "server_ms": (time.perf_counter() - started) * 1000}, result


def build_overlap_detector(root, mode):
    """Functionality: Build the original full model for one explicitly selected experiment.
    Inputs: model root and full workload. Outputs: wrapped detector with unchanged model fields.
    Logic: reuse the production factory. Constraints: no precision, parameter or default change.
    """
    from realtime.services import build_detector

    if mode != "full":
        raise ValueError("Tracking overlap experiment requires full workload")
    detector = build_detector(root, mode)
    detector.predict = OverlapServiceProbe(detector)
    return detector
