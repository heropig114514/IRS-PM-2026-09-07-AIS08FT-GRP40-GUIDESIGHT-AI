"""Responsibilities: Add explicit host fusion to the isolated overlap browser experiment.
Implementation: Scoped fusion substitution around the original complete overlap service call.
Related Modules: tools.host_fusion_probe/resident_overlap_probe; realtime.distance/services.
Declaration Index: HostFusionServiceProbe; HostFusionServiceProbe.__call__;
build_host_fusion_detector.
Variable Index: None. The original service owns serialized execution and session resets.
"""

import time
from unittest.mock import patch

from tools.host_fusion_probe import host_fuse_object_depth
from tools.resident_overlap_probe import OverlapServiceProbe


class HostFusionServiceProbe(OverlapServiceProbe):
    """Functionality: Execute explicit host fusion with original Graph/session error semantics.
    Logic: transfer and partition occur within the parent's measured complete pipeline.
    Constraints: never default; no independent worker, retry, fallback or parameter change.
    """

    def __call__(self, jpeg):
        """Functionality: Substitute fusion for one frame and restore the original on all exits.
        Inputs: bounded JPEG. Outputs: original outputs plus experiment identity.
        Logic: measure complete call including transfer and hook setup/restoration, even first call.
        Constraints: original failure latching remains; no asynchronous work or retained plane.
        """
        started = time.perf_counter()
        with patch("realtime.distance.fuse_object_depth", host_fuse_object_depth):
            metadata, result = super().__call__(jpeg)
        return {**metadata, "server_ms": (time.perf_counter() - started) * 1000,
                "host_fusion_experiment": "exact_fp32_host_partition"}, result


def build_host_fusion_detector(root, mode):
    """Functionality: Build the unchanged full model for an explicitly selected experiment.
    Inputs: model root and full workload. Outputs: wrapped resident detector.
    Logic: retain the production factory and install one isolated probe.
    Constraints: only full workload; no precision, weight, sampling or default changes.
    """
    from realtime.services import build_detector

    if mode != "full":
        raise ValueError("Host fusion experiment requires full workload")
    detector = build_detector(root, mode)
    detector.predict = HostFusionServiceProbe(detector)
    return detector
