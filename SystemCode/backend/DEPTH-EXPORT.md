# Depth forward export and acceleration

## Responsibility and boundaries

`tools.benchmark_depth_export` exports the current YOLO26 depth checkpoint and compares its
device-resident forward pass using PyTorch eager, the existing PyTorch CUDA Graph probe, ONNX
Runtime CUDA and TensorRT. `realtime.depth_runtime` provides the verified TensorRT implementation
used by native resident/PRF2 inference and the benchmark. `tools.depth_export_runtime` retains
ONNX Runtime as an experiment. Backend selection is explicit; an unconfigured service uses Torch.

The workflow preserves depth `imgsz=768`, batch 1, FP32, disabled TF32, the released checkpoint's
calibration buffers, and SDK preprocessing/postprocessing. It uses the existing max640/JPEG80
benchmark fixture transformation, independently of the service's maximum accepted JPEG size.
There is no new random seed, dataset split, accuracy threshold, automatic retry or CPU fallback.

The pinned SDK's native depth head returns calibrated depth at one quarter of the tensor's spatial
resolution. Its ordinary export mode adds a bilinear 4x resize. This tool exports the warmed,
GPU-fused **native eval module with `head.export=False`**, using static ONNX opset 17. It avoids
comparing extra resize work against native forward. The unchanged SDK postprocessor performs
input-size interpolation, letterbox removal and original-frame alignment for every backend.

## Environment

Use a supported CUDA GPU, Python 3.12, the native environment's Torch `2.8.0+cu126` and Ultralytics
`8.4.171`. Install the optional dependencies from `requirements-depth-export.txt` into a separate
environment. The local workflow uses `.venv/depth-export` and an explicit `.pth` link to the
existing native environment's site-packages, preserving the service environment:

```powershell
# Run from backend. Create this directory only if it does not already exist.
.\.venv\Scripts\python.exe -m venv .venv/depth-export
$nativePackages = (Resolve-Path .venv/Lib/site-packages).Path
Set-Content .venv/depth-export/Lib/site-packages/native-runtime.pth $nativePackages
.\.venv\depth-export\Scripts\python.exe -m pip install --retries 0 -r requirements-depth-export.txt
```

The linked environment must remain available. For a self-contained server environment, install
the pinned native Torch/Ultralytics dependencies and export requirements together, then regenerate
artifacts on that server. A GPU engine is not a portable substitute for the checkpoint.
Keep `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=0`, and writable `YOLO_CONFIG_DIR`, `MPLCONFIGDIR`, `TEMP`
and `TMP` directories. TensorRT's Python wheel supplies the build API; no `trtexec` is required.
Torch is imported before the ONNX session and its CUDA/cuDNN DLL directory is loaded explicitly.

## Export, build and measurement

Stop this project's owned inference processes before measuring to avoid competing GPU work.
Use local, trusted weights and a **new** artifact directory and report filename. Existing outputs
are never overwritten by an implicit rebuild. The following uses the existing public fixtures:

```powershell
$fixtures = @(
  'model_tests/samples/bus.jpg',
  'models/experiments/coco8/images/val/000000000036.jpg',
  'models/experiments/coco8/images/val/000000000042.jpg',
  'models/experiments/coco8/images/val/000000000049.jpg',
  'models/experiments/coco8/images/val/000000000061.jpg'
)
.\.venv\depth-export\Scripts\python.exe -B -m tools.benchmark_depth_export `
  --weights models/yolo26n-depth.pt --inputs $fixtures `
  --artifacts models/experiments/depth-export/current-weights `
  --output benchmarks/depth-export/current-weights.json --phase all
```

`--phase export` creates checked ONNX files and a manifest. `--phase build` requires that manifest
and creates engines once; `--phase benchmark` validates and reuses complete artifacts. Each phase
requires a fresh report filename and the same explicit weights/inputs. Inspect failures before
selecting a subsequent phase; there is no built-in retry. `all` records independent backend
failures while completing the other requested comparisons and returns a nonzero exit status.
Progress is appended to a `.jsonl` sidecar; the final `.json` is published only when the process
finishes. Read complete JSONL lines while running, and inspect manifests after their phase ends.
This avoids replacing a report held open by a Windows reader. Failed attempts remain separate files.

`--stream default` retains the original caller schedule. `--stream dedicated` is an explicit
comparison: PyTorch and TensorRT use the same dedicated Torch CUDA stream. ONNX Runtime owns its
internal stream and uses explicit completion boundaries. Use a new report filename for each run.
TensorRT warns that default-stream execution may insert additional synchronization; compare the
complete measured runs rather than assuming a gain from the warning alone. Stream selection does
not change input tensors, arithmetic precision or sample counts.

One artifact is created per unique **actual SDK-preprocessed shape**. Images are not forced square.
TensorRT uses strongly typed FP32, disabled TF32/FP16/INT8, a 1 GiB builder workspace and no auxiliary
streams. The workspace limit bounds builder resources; it is not a guarantee of total process VRAM.
ONNX Runtime uses only CUDA kernels, disables CPU execution fallback and TF32, and uses I/O binding
with an ORT-owned compute stream. The caller's producer stream completes before submission, and
`synchronize_outputs()` completes bound outputs before return. Local Windows ORT 1.23.2 diagnostics
reproduced a native abort when destroying sessions borrowing a nonzero Torch stream, even after
explicit synchronization; that borrowing path is not offered. Both adapters allocate owned output
tensors, avoid host tensor copies during forward,
and reject incompatible shapes, types, devices, stream changes and extra inference options.

## Contracts and evidence

The artifact manifest binds checkpoint, ONNX and engine SHA256 hashes; SDK depth-head/predictor
source hashes; Python, Torch, CUDA, cuDNN, ONNX, ONNX Runtime and TensorRT versions; GPU model and
compute capability; FP32 policy; calibration values; static input/output shapes; and build settings.
The tool verifies provenance before loading an artifact. Standalone adapter callers must perform
the same `validate_manifest` check. These adapters support serial calls only, retain their runtime
objects. TensorRT requires input lifetime through completion and synchronization before host or
cross-stream access. ONNX calls complete synchronously; `close()` explicitly releases the session
and subsequent calls are rejected. There is no leaked-session workaround or automatic backend swap.

Reports retain all 30 steady calls after one separately reported first measured call, backend setup
cost, ONNX export and TensorRT build cost. The first measured call is after SDK/backend setup; it is
**not process-cold startup**. CUDA events measure the stream interval, including launch gaps;
synchronized wall time measures submit-to-completion. For ONNX, the caller-stream event interval
includes the explicit wait for the ORT-owned stream; it is not isolated ONNX kernel time.
Neither includes image preparation, model
loading, depth alignment, detection, tracking, drawing or transport. These are forward measurements,
not video throughput or end-to-end service latency.

Every fixture receives eager-before and eager-after controls. Reports compare complete raw and
aligned maps, retain finite/nonfinite counts, exact equality and absolute/relative error distributions,
and verify that a later mirrored-input call preserves the previous output. Mirroring is a separate
buffer/numerical diagnostic, not a replacement for the original performance inputs or a new dataset.
No observed error is silently converted into an accuracy pass. In particular, faster FP32 kernels
can still differ numerically because of fusion or operation ordering.

Machine-readable evidence is under [benchmarks/2026-10-26/depth-export](benchmarks/2026-10-26/depth-export).
Model binaries, serialized engines and local environments remain ignored; reports and source are
versioned. Follow [the optimization plan](INFERENCE_OPTIMIZATION_PLAN.md) for service-level and
ground-truth validation requirements.

## Native inference configuration

Install `requirements-inference-tensorrt.txt` into the selected GPU service environment, with the
native CUDA Torch wheel appropriate to that machine. Serving needs TensorRT but does not need
ONNX, ONNX Runtime or the source ONNX files. Export/build remains a separate operation. The local
linked export environment can also be explicitly selected as the worker interpreter; this is not
validation of the launcher's default `.venv-inference` environment. The standard launcher checks
the TensorRT package pin when this backend is selected.

Before starting the GPU process, set both variables (paths are relative to `backend` unless
absolute):

```powershell
$env:PRS_DEPTH_BACKEND = 'tensorrt'
$env:PRS_DEPTH_ENGINE_MANIFEST = 'models/experiments/depth-serving-fp32/manifest.json'
# Standard two-environment launcher; the GPU environment must contain the optional dependency.
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --check
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp
```

For an explicit Torch deployment, set `PRS_DEPTH_BACKEND=torch` and remove
`PRS_DEPTH_ENGINE_MANIFEST`. Unknown backends, a missing TensorRT manifest or a stray Torch
manifest fail configuration validation. PRF2 freezes the configuration at startup. Its handshake,
health response and frame metadata expose `depth_backend`; health also lists loaded engine tensor
shapes. Detection's existing `backend` field still describes detection, not the separate depth model.

### Detection model and ROI fusion

The independent `PRS_DETECTION_MODEL` selects `yolo26n` (default) or `yolo26n-walk` (87 classes).
PRF2 freezes its selected checkpoint and manifest before the first handshake; health checks and
lazy loading use that same selection. The launcher verifies the selected checkpoint, and
`tools.check_local_mvp` reads the same explicit model setting. See [WALK-MODEL.md](WALK-MODEL.md)
for the additional classes and pedestrian-light decision rule. Depth engines depend on the depth
checkpoint, so selecting the walk detector does not require rebuilding the unchanged depth model.

`PRS_DEPTH_FUSION=gpu` preserves the original per-object GPU reduction. Explicit
`PRS_DEPTH_FUSION=host` selects the existing FP32 host partition implementation in PRF2 mode A:
the full aligned depth plane transfers once, then each ROI uses the same bounds, finite-positive
filter and central order statistics. Transfer and partition time remain inside fusion/frame timing.
This does not move model forward to CPU or lower depth frequency. Mode B/C plus host fusion is
rejected at startup, rather than silently using another fusion policy. This setting is specific to
the PRF2 worker; the independent resident-v1/HF paths keep their own existing configuration.

```powershell
# Optional 87-class local PRF2 worker, with the unchanged TensorRT depth engine bundle.
$env:PRS_DETECTION_MODEL = 'yolo26n-walk'
$env:PRS_DEPTH_BACKEND = 'tensorrt'
$env:PRS_DEPTH_ENGINE_MANIFEST = 'models/experiments/depth-serving-fp32/manifest.json'
$env:PRS_DEPTH_FUSION = 'host'
```

Hello, health and each frame identify `depth_fusion`; health also names the detector and class
count. Unset `PRS_DEPTH_FUSION` defaults to `gpu`; unset `PRS_DETECTION_MODEL` defaults to the
original COCO model. The original HF acceleration plan does not qualify the walk checkpoint.

`DepthEstimator` uses `AcceleratedDepthPredictor` to replace only the depth forward call. Image
preparation, letterbox removal, calibration, depth alignment, distance fusion, tracking and risk
rules retain their native implementation. The prebuilt engines do not run the native Torch depth
warmup; the first actual engine execution is included in first-frame latency. FP32, depth edge
768 and batch 1 remain mandatory. HF paths retain their independently configured Torch behavior.

Activation verifies checkpoint and engine hashes, native SDK sources, runtime/GPU identity and
build policy before deserialization. A pool loads all declared shapes once, shares the TensorRT
runtime/logger and supports at most eight fixed shapes. The provisioned local bundle supports
`[1,3,768,576]`, `[1,3,768,608]`, `[1,3,576,768]`, `[1,3,608,768]` and `[1,3,448,768]`.
These are SDK-preprocessed tensor shapes, not raw JPEG dimensions. An unsupported aspect ratio
fails with `depth_input_shape`; there is no request-time build, square resize or Torch fallback.
Use only trusted manifests/engines. A shape-count cap does not establish a total VRAM budget.
For another deployment, export/build all required representative input shapes into one fresh
artifact directory and select that directory's manifest. The local bundle path is an example,
not a remotely downloadable engine or an automatic artifact discovery location.

## Whole-pipeline validation

`tools.benchmark_depth_integration` exercises the actual native service detector on every original
video frame with source timestamps. Run Torch-before, TensorRT and Torch-after in separate
processes. Use a new output directory for each run and an ignored depth-cache directory for the
reference's complete FP32 maps:

```powershell
$video = 'model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4'
$cache = 'models/experiments/depth-integration-cache/new-reference'
.\.venv\depth-export\Scripts\python.exe -B -m tools.benchmark_depth_integration `
  --video $video --backend torch --depth-cache $cache --output benchmarks/new-before
.\.venv\depth-export\Scripts\python.exe -B -m tools.benchmark_depth_integration `
  --video $video --backend tensorrt --depth-cache $cache --reference benchmarks/new-before `
  --manifest models/experiments/depth-serving-fp32/manifest.json --output benchmarks/new-tensorrt
.\.venv\depth-export\Scripts\python.exe -B -m tools.benchmark_depth_integration `
  --video $video --backend torch --depth-cache $cache --reference benchmarks/new-before `
  --output benchmarks/new-after
```

The report binds input, checkpoint, parameters and implementation hashes, separates first-frame
and steady timing, and retains every sample. Complete map comparison and disk I/O occur outside
prediction timing. Object numeric differences are reported separately from structural/discrete
decision differences; JPEG differences remain visible. `completed` means all frames were processed,
not that outputs are bitwise identical or truth-based quality is accepted. Processing rate derived
from mean offline frame latency is not camera FPS or browser end-to-end throughput.

Use `--detection-model yolo26n` or `--detection-model yolo26n-walk` to select independent comparison
groups; changing models invalidates a reference. `--fusion gpu|host` selects only ROI reduction.
For a TensorRT fusion comparison, use `--backend tensorrt` and the same manifest in all three
processes: GPU-before, host and GPU-after. A reference can use either explicitly selected depth
backend and always requires a fresh private cache. Source/checkpoint identity and every source
frame remain fixed; no speedup is inferred by comparing the two detector checkpoints.

The [walk/fusion summary](benchmarks/2026-10-27/walk-fusion/summary.json) retains same-model
whole-video controls, exact map/object/JPEG comparisons, per-phase times and actual service probes.
These street videos do not replace the walk model's held-out detection/colour quality evaluation.

The [integration summary](benchmarks/2026-10-26/depth-integration/summary.json) links complete video
controls, service probes and numerical differences. Read it together with the
[forward summary](benchmarks/2026-10-26/depth-export/summary.json): forward gains exclude all other
pipeline stages, and small FP32 output deviations can alter rendered text near a rounding boundary.

## Final weights and deployment

When final fine-tuned weights arrive, preserve the old checkpoint and artifacts, verify the new
checkpoint SHA256, choose a new artifact directory, and rerun export, build and numerical/forward
validation on the target GPU. Calibration is exported from the actual head, never assumed to be
identity. A changed checkpoint or environment invalidates old engine bindings.

Native service integration is available through the explicit configuration above. General rollout
still requires the team's fixed depth/distance truth data and evaluation protocol, target shape
coverage, resource budget and sustained concurrency validation. Local numerical/decision agreement
does not establish distance accuracy or safety. Precision reduction, reduced depth frequency and
looser accuracy criteria are separate experiments. Shape changes require explicitly prepared
artifacts; there is no dynamic build or eager fallback in a request.

## Implementation references

- [Ultralytics depth task and output contract](https://docs.ultralytics.com/tasks/depth)
- [ONNX Runtime CUDA provider, stream binding and TF32](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- [TensorRT Python installation](https://docs.nvidia.com/deeplearning/tensorrt/latest/installing-tensorrt/install-pip.html)

The installed SDK source is authoritative for the pinned implementation. Published GPU/FP16
performance numbers are not local FP32 measurements and are not used as a baseline.

The pinned ONNX Runtime CUDA [provider](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/core/providers/cuda/cuda_execution_provider.cc)
and [stream implementation](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/core/providers/cuda/cuda_stream_handle.cc)
distinguish provider-owned and external streams. Synchronizing a caller event alone does not establish
completion on an unrelated nonblocking stream; the adapter therefore waits on its bound outputs.
