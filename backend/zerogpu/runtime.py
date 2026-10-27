"""Responsibilities: Verified YOLO26n weights and explicitly selected Torch precision/layout.
The class count is the manifest profile's (80 COCO or 87 walk model, core.detection_model).
Implementation: Pin/fuse eval model; allocation-local Graph buffers with FP32 owned CPU outputs;
fixed-shape AOT export permits dense channels-last constants while normalizing anchor storage.
Related Modules: zerogpu.live_worker reuses the allocation-local graph;
tools.kaggle_acceleration tests AOT export;
core.detector supplies shared pre/postprocessing; realtime.detector owns a native benchmark graph.
Declaration Index: configure_runtime — measured floating flags; load_model — verified startup;
export_program — contiguous-constant export;
GraphRunner/GraphRunner.__init__/GraphRunner.__call__ — fixed-shape capture, replay and CUDA
event forward timing, separately from the synchronized transfer-inclusive stage.
Duration sampling uses perf_counter so sub-tick GPU calls remain measurable on Windows/Python3.12;
this does not change any allocation or transport deadline.
Variable Index: logger — initialization/capture/precision diagnostics. Config dtype/layout/
benchmark are explicit; defaults preserve the original FP32 control, with no device fallback.
"""

import hashlib
import logging
import time

from core.detection_model import PROFILES

logger = logging.getLogger(__name__)


def configure_runtime(config):
    """Functionality: Establish explicit floating runtime flags inside the current process.
    Inputs: measured dtype/benchmark/TF32 configuration. Outputs: None or ValueError.
    Logic: validate supported floating precision; forbid TF32; set both CUDA TF32 flags off and
    the selected cuDNN benchmark flag. Constraints: flags are process-local; eager initialization
    and each AOT allocation call this independently; no precision or backend substitution.
    """
    import torch

    if config.get("dtype", "float32") not in ("float32", "float16", "bfloat16"):
        raise ValueError("Unsupported explicit Torch model precision")
    if config.get("tf32"):
        raise ValueError("The production model contract requires TF32 disabled")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = config.get("benchmark", False)


def load_model(path, manifest, device, config=None):
    """Functionality: Load the trusted pinned model once during application startup.
    Inputs: weight Path, manifest, device, optional explicit dtype/layout/benchmark configuration.
    Outputs: fused eval module at requested precision/layout/device, or startup exception.
    Logic: verify SHA256 before deserialization; select the existing export's one-to-one head.
    Constraints: default FP32 is the existing control; supported floating dtype only, TF32 disabled;
    CUDA placement occurs at eager module root or dedicated AOT startup allocation, never per
    frame; no downloads/fallback. Each process establishes the same explicit floating flags.
    """
    import torch
    import ultralytics
    from ultralytics import YOLO

    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["source_sha256"]:
        raise ValueError("Weight hash does not match the pinned manifest")
    if ultralytics.__version__ != manifest["build"]["ultralytics"]:
        raise ValueError("Unexpected Ultralytics version")
    config = {} if config is None else config
    configure_runtime(config)
    model = YOLO(str(path)).model.float().eval()
    # The pinned export sets end2end=True for nms=False. Reapply that selection when loading
    # older checkpoint attribute layouts with the current pinned Ultralytics implementation.
    model.end2end = True
    names = [model.names[i] for i in range(len(model.names))]
    head = model.model[-1]
    expected_classes = PROFILES.get(manifest.get("model"), (None, None, 0))[2]
    if (names != manifest["names"] or len(names) != expected_classes
            or not head.end2end or head.max_det != 300):
        raise ValueError("Model class order or end-to-end head does not match")
    # The ONNX exporter fuses the same eval branches and uses the plain top-k export path.
    model.fuse(verbose=False)
    head.export, head.format, head.dynamic = True, "onnx", False
    model = model.to(device=device, dtype=getattr(torch, config.get("dtype", "float32")))
    if config.get("layout", False):
        model.to(memory_format=torch.channels_last)
    logger.info("Verified model initialized device=%s dtype=%s channels_last=%s fused=True",
                device, config.get("dtype", "float32"), config.get("layout", False))
    return model


def export_program(model, inputs):
    """Functionality: Export fixed floating inference with packable dense constant storage.
    Inputs: verified model and dense CUDA FP32/FP16/BF16 [1,3,640,640] tensor;
    contiguous NCHW or channels-last layout.
    Outputs: ExportedProgram; mutates only the head's cached anchor tensor storage layout.
    Logic: initialize anchors, copy their transposed view to contiguous storage, then export.
    Constraints: values/weights/shape remain unchanged; preserve selected dtype and dense layout;
    normalize before tracing so stride metadata
    matches actual storage. Reject remaining noncontiguous constants rather than patching the
    packager's pointer arithmetic or changing a compiled graph's constants after tracing.
    """
    import torch

    if (inputs.shape != (1, 3, 640, 640)
            or inputs.dtype not in (torch.float32, torch.float16, torch.bfloat16)
            or inputs.device.type != "cuda" or not (inputs.is_contiguous()
                or inputs.is_contiguous(memory_format=torch.channels_last))):
        raise ValueError("Expected dense CUDA floating [1,3,640,640] export input")
    with torch.inference_mode():
        model(inputs)
        head = model.model[-1]
        head.anchors = head.anchors.contiguous()
        exported = torch.export.export(model, (inputs,))
    invalid = [name for name, value in {**exported.state_dict, **exported.constants}.items()
               if isinstance(value, torch.Tensor) and not value.is_contiguous() and not (
                   value.ndim == 4 and value.is_contiguous(memory_format=torch.channels_last))]
    if invalid:
        raise ValueError(f"AOT constants must be contiguous before packaging: {invalid}")
    return exported


class GraphRunner:
    """Functionality: Reuse fixed selected-precision storage throughout one GPU allocation.
    Logic: warm on a side stream before capture; copy each frame into stable pinned/device buffers.
    Constraints: ZeroGPU callers never retain a graph across allocations; native resident callers
    may reuse it while the device/model stay resident. Serial calls, batch1, no dropped frames.
    """

    def __init__(self, model, tensor, config=None):
        """Functionality: Initialize an allocation-local graph for the selected callable.
        Inputs: verified CUDA Torch/AOT callable, first FP32 array, explicit dtype/layout config.
        Outputs: retained callable, graph/input/host/FP32 output state, setup_ms and evidence.
        Logic: check process-local floating flags; pinned compute input; dense selected CUDA layout;
        ten side-stream warmups,
        validate CUDA output contract, capture forward and FP32 conversion together.
        Constraints: FP32 input/output API unchanged; serial invocation only; native BF16 required;
        warmup/capture costs reported; errors propagate without eager or backend fallback.
        """
        import torch

        started = time.perf_counter()
        if tensor.shape != (1, 3, 640, 640) or str(tensor.dtype) != "float32":
            raise ValueError("Graph requires FP32 [1,3,640,640] input")
        config = {} if config is None else config
        if config.get("dtype", "float32") not in ("float32", "float16", "bfloat16"):
            raise ValueError("Unsupported explicit Graph compute precision")
        if (config.get("tf32") or torch.backends.cuda.matmul.allow_tf32
                or torch.backends.cudnn.allow_tf32
                or torch.backends.cudnn.benchmark != config.get("benchmark", False)):
            raise ValueError("Graph runtime flags differ from the measured configuration")
        self.dtype = getattr(torch, config.get("dtype", "float32"))
        # Constants allocated before capture are owned by the model/package, not the Graph pool.
        # Retain the callable until this allocation-local runner is released to keep them alive.
        self.model = model
        if self.dtype == torch.bfloat16 and not torch.cuda.is_bf16_supported(
            including_emulation=False
        ):
            raise ValueError("Native BF16 is required on the actual serving GPU")
        if hasattr(model, "parameters"):
            parameters = list(model.parameters())
            if any(value.device.type != "cuda" or value.dtype != self.dtype
                   for value in parameters):
                raise ValueError("Graph/model device or explicit precision mismatch")
        self.host = torch.empty(tensor.shape, dtype=self.dtype, pin_memory=True)
        self.input = torch.from_numpy(tensor).to(device="cuda", dtype=self.dtype)
        if config.get("layout", False):
            self.input = self.input.contiguous(memory_format=torch.channels_last)
        logger.info("Graph warmup start dtype=%s stride=%s calls=10 GPU=%s",
                    self.dtype, list(self.input.stride()), torch.cuda.get_device_name())
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.inference_mode(), torch.cuda.stream(stream):
            for _ in range(10):
                warm_output = self.model(self.input)
        torch.cuda.current_stream().wait_stream(stream)
        torch.cuda.synchronize()
        if (not isinstance(warm_output, torch.Tensor) or warm_output.device.type != "cuda"
                or warm_output.shape != (1, 300, 6)):
            raise RuntimeError("Unexpected CUDA forward output during Graph preparation")
        forward_dtype = str(warm_output.dtype)
        del warm_output
        self.graph = torch.cuda.CUDAGraph()
        logger.info("Graph warmup completed; capture start forward_dtype=%s", forward_dtype)
        with torch.inference_mode(), torch.cuda.graph(self.graph):
            self.output = self.model(self.input).float()
        if not isinstance(self.output, torch.Tensor) or self.output.dtype != torch.float32:
            raise RuntimeError("Unexpected graph output")
        self.host_output = torch.empty(self.output.shape, dtype=torch.float32, pin_memory=True)
        self.events = [torch.cuda.Event(enable_timing=True) for _ in range(2)]
        self.forward_event_ms = None
        torch.cuda.synchronize()
        self.setup_ms = (time.perf_counter() - started) * 1000
        self.evidence = {"compute_dtype": str(self.dtype), "forward_dtype": forward_dtype,
                         "input_stride": list(self.input.stride()), "pinned_input": True,
                         "graph_captured": True, "gpu": torch.cuda.get_device_name(),
                         "capability": list(torch.cuda.get_device_capability()),
                         "torch": torch.__version__, "cuda": torch.version.cuda,
                         "cpu_threads": torch.get_num_threads(),
                         "cpu_interop_threads": torch.get_num_interop_threads(),
                         "tf32_matmul": torch.backends.cuda.matmul.allow_tf32,
                         "tf32_cudnn": torch.backends.cudnn.allow_tf32,
                         "cudnn_benchmark": torch.backends.cudnn.benchmark}
        logger.info("Graph initialized setup_ms=%.2f evidence=%s", self.setup_ms, self.evidence)

    def __call__(self, tensor):
        """Functionality: Replay one frame. Inputs: same-shape contiguous FP32 array.
        Outputs: independent CPU prediction array and synchronized H2D/replay/D2H milliseconds.
        Logic: fill pinned input, async H2D, CUDA events around replay only, blocking D2H,
        copy out of reusable storage; save the independent forward_event_ms. Use perf_counter
        for sub-millisecond host timing; Windows GetTickCount64 can quantize short calls to zero.
        Constraints: output ownership prevents later frames overwriting earlier predictions.
        """
        import torch

        if tensor.shape != tuple(self.input.shape) or str(tensor.dtype) != "float32":
            raise ValueError("Graph frame shape/dtype changed")
        started = time.perf_counter()
        with torch.inference_mode():
            self.host.copy_(torch.from_numpy(tensor))
            self.input.copy_(self.host, non_blocking=True)
            self.events[0].record()
            self.graph.replay()
            self.events[1].record()
            self.host_output.copy_(self.output, non_blocking=False)
            result = self.host_output.numpy().copy()
            self.events[1].synchronize()
            self.forward_event_ms = self.events[0].elapsed_time(self.events[1])
        return result, (time.perf_counter() - started) * 1000
