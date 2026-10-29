"""Responsibilities: Substitute only the native depth forward implementation in the pinned SDK.
Implementation: Inherit official image preparation/alignment; dispatch a verified TensorRT pool.
Related Modules: realtime.depth/depth_runtime; ultralytics.models.yolo.depth.predict.
Declaration Index: AcceleratedDepthPredictor; AcceleratedDepthPredictor.__init__;
AcceleratedDepthPredictor.inference.
Variable Index: None. The caller owns the preloaded engine pool and serial worker.
"""

from ultralytics.models.yolo.depth.predict import DepthPredictor


class AcceleratedDepthPredictor(DepthPredictor):
    """Functionality: Keep official preprocessing/postprocessing around an accelerated forward.
    Logic: use native SDK metadata/rectangular letterbox and the supplied engine pool.
    Constraints: lazy-imported only for explicitly selected native TensorRT, never in HF paths.
    """

    def __init__(self, *, forward, **kwargs):
        """Functionality: Bind the already validated engine pool to a standard SDK predictor.
        Inputs: serial forward callable and original SDK constructor keywords. Outputs: predictor.
        Logic: initialize SDK state; skip native warmup because inference uses prebuilt engines.
        Constraints: first engine execution remains part of first-frame latency; no hidden warmup.
        """
        super().__init__(**kwargs)
        self.forward = forward
        self.done_warmup = True

    def inference(self, im, *args, **kwargs):
        """Functionality: Replace only depth forward on an unchanged prepared image tensor.
        Inputs: official SDK tensor, no extra inference options. Outputs: calibrated raw depth.
        Logic: delegate once to the explicit pool. Constraints: errors propagate, no eager fallback.
        """
        return self.forward(im, *args, **kwargs)
