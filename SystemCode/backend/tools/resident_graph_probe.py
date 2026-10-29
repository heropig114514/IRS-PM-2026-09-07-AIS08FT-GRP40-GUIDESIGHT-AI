"""Responsibilities: Adapt the existing depth Graph probe to an explicit resident-service test.
Implementation: First real frame completes normally, then captures once before returning;
later frames/reconnects reuse that graph on the original service's single GPU worker.
Related Modules: tools.depth_graph_probe/resident_graph_asgi; realtime.services/depth/detector.
Declaration Index: prepare_depth_graph; GraphServiceProbe; GraphServiceProbe.__init__;
GraphServiceProbe.__call__; build_graph_detector.
Variable Index: logger — capture duration/count and safe failures; no image or secret logging.
"""

import logging
import time

from core.detector import DetectionError

logger = logging.getLogger("core.resident.graph_probe")


def prepare_depth_graph(detector, jpeg):
    """Functionality: Explicitly prepare a fixed-shape graph using the first real frame.
    Inputs: warmed resident detector and original JPEG. Outputs: installed DepthGraphProbe.
    Logic: reuse official preprocessing and inference; preparation requires exact raw equality.
    Constraints: no synthetic input, precision change, multi-shape cache, fallback or retry;
    the service must own serialization, allocation and the same GPU worker throughout.
    """
    import numpy as np

    from core.detector import decode_frame
    from tools.depth_graph_probe import DepthGraphProbe

    predictor = detector.depth_estimator.model.predictor
    image = np.ascontiguousarray(np.asarray(decode_frame(jpeg))[..., ::-1])
    example = predictor.preprocess([image])
    graph = DepthGraphProbe(predictor.inference, example)
    predictor.inference = graph
    return graph


class GraphServiceProbe:
    """Functionality: Measure full first-response cost and preserve one graph across sessions.
    Logic: wrap the original pipeline callable, then annotate explicit experimental execution.
    Constraints: standalone test adapter; ordinary service never imports it; pipeline failures
    latch until restart. Existing service owns input rejection, cancellation and tracking reset.
    """

    def __init__(self, detector):
        """Functionality: Retain original callable without doing CUDA work or warming an image.
        Inputs: initialized native full pipeline. Outputs: uncaptured probe state.
        Logic: bind predict before factory replaces it. Constraints: no alternate model loading.
        """
        self.detector = detector
        self.original_predict = detector.predict
        self.graph = None
        self.failed = False

    def __call__(self, jpeg):
        """Functionality: Deliver ordered predictions with explicit capture/replay evidence.
        Inputs: one real JPEG. Outputs: metadata/JPEG, or latched failure.
        Logic: first result waits for graph preparation; all calls include wrapper wall time in
        server_ms, with original pipeline duration separately preserved. Later calls use replay.
        Constraints: no unmeasured warmup between frames, hidden first-frame exclusion, graph
        recreation, retry or fallback; shape/dtype/device errors propagate from the graph.
        """
        if self.failed:
            raise RuntimeError("Experimental graph service requires explicit restart")
        started = time.perf_counter()
        replayed = self.graph is not None
        preparation_ms = 0.0
        try:
            metadata, result = self.original_predict(jpeg)
            if self.graph is None:
                preparing = time.perf_counter()
                self.graph = prepare_depth_graph(self.detector, jpeg)
                preparation_ms = (time.perf_counter() - preparing) * 1000
                logger.info("Experimental depth graph captured count=1 prepare_ms=%.3f",
                            preparation_ms)
        except Exception as error:
            if not isinstance(error, DetectionError) or error.code not in {
                "invalid_frame", "frame_size"
            }:
                self.failed = True
            logger.error("Experimental graph pipeline failed type=%s", type(error).__name__)
            raise
        return {**metadata, "pipeline_server_ms": metadata["server_ms"],
                "server_ms": (time.perf_counter() - started) * 1000,
                "depth_graph_experiment": {"replayed": replayed, "capture_count": 1,
                    "prepare_ms": preparation_ms, "evidence": self.graph.evidence}}, result


def build_graph_detector(root, mode):
    """Functionality: Bind the experimental callable to the unchanged full detector factory.
    Inputs: asset root and explicit full mode. Outputs: original detector with wrapped predict.
    Logic: preserve model/manifest/tracker fields for the existing resident service.
    Constraints: only full workload is supported by this browser experiment; no default changes.
    """
    from realtime.services import build_detector

    if mode != "full":
        raise ValueError("Graph service experiment requires full workload")
    detector = build_detector(root, mode)
    detector.predict = GraphServiceProbe(detector)
    return detector
