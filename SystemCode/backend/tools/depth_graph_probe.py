"""Responsibilities: Test fixed-shape FP32 depth CUDA Graph replay outside production code.
Implementation: Warm/capture the existing SDK inference callable; require exact output equality
on the preparation input; preserve preprocessing/postprocessing and return owned output tensors.
Related Modules: tools.benchmark_inference; realtime.depth; Ultralytics depth predictor.
Declaration Index: DepthGraphProbe; DepthGraphProbe.__init__; DepthGraphProbe.__call__.
Variable Index: None. Graph state belongs to one local diagnostic process, never an HF allocation.
"""

import time


class DepthGraphProbe:
    """Functionality: Evaluate launch-overhead reduction without changing depth model arithmetic.
    Logic: capture an initialized SDK callable at one explicit shape/device/dtype, then replay.
    Constraints: serial native CUDA diagnostics only; no precision changes, fallback or deployment;
    equality on one fixture is not a depth quality evaluation or a general equivalence proof.
    """

    def __init__(self, inference, example):
        """Functionality: Build and validate one graph around the existing depth inference call.
        Inputs: warmed bound SDK inference callable and its preprocessed CUDA FP32 example.
        Outputs: reusable graph plus setup/equality evidence. Logic: clone stable input, retain
        callable ownership, warm on a side stream, capture, then compare every output tensor.
        Constraints: fail on unsupported outputs or any numerical difference; initialization is
        reported separately and never included in the steady-state speedup calculation.
        """
        import torch
        from torch.utils._pytree import tree_flatten, tree_map

        if example.device.type != "cuda" or example.dtype != torch.float32:
            raise ValueError("Depth graph probe requires the unchanged CUDA FP32 input")
        started = time.perf_counter()
        self.inference = inference
        self.input = example.clone()
        self.graph = torch.cuda.CUDAGraph()
        with torch.inference_mode():
            reference = tree_map(lambda value: value.clone(), inference(example))
            stream = torch.cuda.Stream()
            stream.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(stream):
                for _ in range(10):
                    inference(self.input)
            torch.cuda.current_stream().wait_stream(stream)
            torch.cuda.synchronize()
            with torch.cuda.graph(self.graph):
                self.output = inference(self.input)
            self.graph.replay()
            torch.cuda.synchronize()
        before, specification = tree_flatten(reference)
        after, actual = tree_flatten(self.output)
        if specification != actual or not before:
            raise ValueError("Depth graph output structure differs")
        for left, right in zip(before, after, strict=True):
            if not isinstance(left, torch.Tensor) or not isinstance(right, torch.Tensor):
                raise ValueError("Depth probe requires tensor-only outputs")
            torch.testing.assert_close(right, left, rtol=0, atol=0)
        self.evidence = {"adapter": "experimental_native_fp32_depth_graph",
                         "shape": list(example.shape), "dtype": str(example.dtype),
                         "setup_ms": (time.perf_counter() - started) * 1000,
                         "preparation_output_equal": True,
                         "scope": "One fixture raw-output equality; no dataset quality claim"}

    def __call__(self, image, *args, **kwargs):
        """Functionality: Replay the fixed-shape depth calculation and preserve output ownership.
        Inputs: SDK-preprocessed tensor, no additional inference options. Outputs: cloned tensors.
        Logic: validate shape/dtype/device, copy current pixels, replay on current stream and clone.
        Constraints: serial calls only; changed shapes/options fail explicitly; no host transfer,
        added synchronization, numerical change, automatic capture or backend fallback.
        """
        import torch
        from torch.utils._pytree import tree_map

        if (args or kwargs or image.shape != self.input.shape or image.dtype != self.input.dtype
                or image.device != self.input.device):
            raise ValueError("Depth graph probe input contract changed")
        with torch.inference_mode():
            self.input.copy_(image)
            self.graph.replay()
            return tree_map(lambda value: value.clone(), self.output)
