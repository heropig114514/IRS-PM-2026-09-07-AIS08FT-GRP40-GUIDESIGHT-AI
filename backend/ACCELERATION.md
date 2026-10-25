# 2026-10-05 Pure-Inference Best-Configuration Migration

The user specified inference efficiency as the objective and asked to keep long experiments on
Kaggle. The current choice is the v8 configuration confirmed by three independent full-COCO runs:
FP16 + Conv/BN fusion + channels-last + torch.compile(default/fullgraph/static) + CUDA Graph,
with reusable device and pinned-memory buffers. The three v8 pure-forward medians were 1.710016,
1.751632, and 1.742272 ms; the group median was 1.742272 ms. The same-version fair FP32 baseline
was 14.938976 ms, about 8.574x slower. AP was 39.156308% in all three runs versus 39.170516% for
the baseline, a decrease of 0.014207 percentage points, below the approved 0.5-point limit. A
single faster candidate is not presented as the best option confirmed by three independent full
quality runs. Raw data and comparison tables remain available.

In v11, two fresh T4 processes in the same notebook restored the cache; raw SHA values for each of
20 frames matched v8 bit-for-bit. This was a cache/adapter diagnostic: the short run lacked a
same-session baseline and the original timing repetitions, so its measurements are not ranked
alongside v8. On the first HF release, `f72689d2`, the initial call passed 8/8. The second call
reused PID 1371; the compiled frontend bypassed the FX lookup, leaving the cleared counter empty
and triggering the check. This was a reuse/observation-logic issue, not evidence that JIT is
unsupported. The fix explicitly resets the Dynamo frontend for each allocation and then restores
the disk cache. FX hits remain mandatory and new backend code generation remains prohibited; the
model, precision, layout, preprocessing, and quality threshold are unchanged. The corrected HF
commit is `eb063ebc`. Notebook v12 adds diagnostics for two restores per PID. HF ran only two
eight-frame short calls; results and PRS activation identity are recorded together in
`benchmarks/2026-10-04/hf-jit-migration-contract.json`.

Official references: [Torch 2.8 reset](https://docs.pytorch.org/docs/2.8/generated/torch.compiler.reset.html)
and the [compile-cache API](https://docs.pytorch.org/tutorials/recipes/torch_compile_caching_tutorial.html).
HF can reuse a file cache, but each GPU allocation must rebuild the callable/Graph and verify the
hardware; a T4 binary cannot be reused directly across GPUs. The following sections preserve the
metrics and failures from earlier completed experiments and AOT migrations.

# 2026-10-04 Kaggle GPU Rebenchmark (Historical)

Experiments were versioned in the same private
[Notebook](https://www.kaggle.com/code/heropig/prs-yolo26-gpu-rebenchmark-20261004): v7 confirmed
all individual/combined methods and the top three combinations; v8 diagnosed and corrected
implementation/configuration issues and reran measurements; v9 stopped before measurement because
serialized duplicate experiment names failed admission; v10 reconfirmed the actual HF serving
module under unchanged conditions. V10 completed 14 reports successfully, and the qualified plan
was deployed to HF and verified on a real GPU. Full speed, AP, sample, and failure logs for v7/v8
are preserved.

The fair baseline includes eval, inference_mode, resident model, batch 1, fixed 640, and the
end-to-end NMS-free head, and shares original JPEG80 encode/decode, direct FP32 normalization,
vectorized postprocessing, and font caching. Every same-version candidate is compared against
this baseline. Individual CPU methods are also compared against explicit legacy CPU processing so
that optimizations already included in the baseline are not counted twice. Original weights,
RGB/Pillow bilinear letterbox, production threshold 0.25, and 300 output candidates are retained.
Full official COCO val2017 uses the same score 0.001/maxDets 100 for AP0.50:0.95 evaluation. Every
deployment repetition must cover all 5,000 images and keep AP loss at or below 0.5 percentage
points. Official train2017 is used for calibration on 128 images and does not overlap val.

The v7 single-method full-image latencies below include decoding through annotated JPEG encoding,
but exclude public-network/ZeroGPU queueing. Backends are mutually exclusive and cannot be treated
as additive components. Every value is compared only with the v7 baseline of 26.590 ms.

| Method | Median (ms) | Observation |
|---|---:|---|
| Fair FP32 baseline | 26.590 | COCO AP 39.1705% |
| Conv/BN fusion | 20.120 | Eliminates BN operators |
| Reusable device/CPU buffers | 25.492 | Small individual gain |
| Pinned input/output buffers | 24.913 | Explicit paired-buffer strategy |
| Reusable CPU preprocessing target | 25.327 | Algorithm and preprocessing unchanged |
| channels-last | 26.882 | Stride was active; method did not win individually |
| cuDNN autotune | 25.000 | Short tests do not establish a stable small gain |
| FP16 alone | 26.632 | Parameters/convolutions were FP16, but BN remained; no standalone speedup |
| CUDA Graph + required buffers | 15.925 | Retains fixed shape and input/output ownership |
| torch.compile default | 19.585 | Compilation executed |
| torch.compile reduce-overhead | 15.955 | Internal Graph separately identified |
| torch.compile max-autotune | 23.714 | Startup tuning cost reported separately |
| Stock AOT | 14.582 | Package/export/load logs retained |
| TensorRT FP32 | 15.343 | Actual engine precision audited |
| TensorRT FP16 | 14.215 | AP 39.1656%, quality passed |
| TensorRT INT8: 4/128 images | 13.338/13.314 | AP 35.1232%/33.4132%, quality failed for the original configuration |
| OpenVINO GPU/FP16 hint | 32.488/32.478 | Actual FP32 GPU operators separately verified in v8 |
| Native BF16/TF32 | N/A | Unsupported natively on T4; non-native BF16 tested separately |
| Non-native BF16 | 37.147 | Actual BF16 path, AP 38.7057%, slower individually |

ORT CUDA graph-optimization levels NONE/BASIC/EXTENDED/ALL, arena/memory-pattern/I/O Binding,
device-pool configurations, FP16/INT8, and Graph combinations are recorded independently. The
quality anomaly in the earlier stream configuration is not treated as method failure. After the
v8 correction, ORT_ALL measured 16.072 ms/AP 39.1706%, and fused Graph measured 15.623 ms/AP
39.1706%; these are compared only with the v8 baseline of 25.683 ms, giving 1.598x/1.644x.
Across 160 controlled stream diagnostics, the previous eager/Graph paths each had 19/20 natural
call errors and 20/20 errors with an injected delay. After fixing both paths, all 80 natural/delayed
calls matched. Stress calls are excluded from latency/AP rankings.

The best measured v7 fused JIT configuration was FP16/channels-last/Conv-BN fusion/buffers/
Graph/default compilation. All three independent full runs had AP 39.1563%; the combination
median was 11.671 ms, or 2.278x the same-version baseline. The same combination measured
12.358 ms in the v8 rerun. Serial stock AOT FP16/contiguous Graph measured 12.432 ms/AP 39.1569%,
or 2.066x the v8 baseline. Latencies from different versions are not ranked together; a roughly
0.6% combination difference does not establish statistical significance. JIT-cache reuse across
allocations had not yet been implemented at the time, so the AOT serving module was verified and
deployed first. The cache-migration work above revisited that limitation; it is not a general
conclusion that JIT cannot be deployed.

Diagnostic evidence is retained by implementation, configuration, and backend:

- FP16 execution was confirmed, but the standalone method still had 114 BN operators and 4,045
  kernels across five frames. Fusion eliminated BN, and compiled fusion reduced the count to 1,215
  kernels. Kernel counts do not establish Tensor Core instruction share or a unique bottleneck.
- OpenVINO weight conversion succeeded, but GPU profiling still showed FP32 Conv/GEMM; file
  precision must not be described as actual half-precision computation.
- A strict ORT INT8 audit found CPU floating-point dequantization. Optimizer configuration and
  same-graph full AP were separately controlled; CPU computation cannot be ignored.
- The original TensorRT INT8 configuration had 97 INT8-weighted layers but failed full AP. V10
  added a strong-typing comparison using the same calibration images, without FP16/INT8 builder
  flags. V10 full AP was 32.8600%; same-graph ORT with the optimizer disabled was 32.9314%.
  Compared with the 39.1705% baseline, these were decreases of 6.3106/6.2391 percentage points,
  so both independent backends failed the quality gate. This shows that the current whole-graph
  PTQ configuration's quantization error was not corrected by these settings; it does not show
  that every INT8 strategy is ineffective.
- Original exceptions were retained for integer BN-state drain errors in unfused Spaces AOT and an
  event-query error in the default AOT loader inside an external Graph. Independent comparisons
  verified the configuration fixes: BN fusion and stock `run_single_threaded=True`.
- After all 57 v8 reports completed, chart rendering failed because an invalid inherited
  `MPLBACKEND` was set. The original summary was unchanged; charts were regenerated and inspected
  with explicit Agg configuration, and v10 includes this configuration fix.

Raw data and complete per-method tables: `benchmarks/2026-10-04/kaggle-v7/output/results/summary.json`,
`kaggle-v8/output/results/summary.json`, and each version's `report/evidence-report.md`. For each
failure, retain report/process/worker logs, actual configuration, and execution graph. Source
files, original weights, calibration/validation images, and predictions have recorded SHA values.
Reports also preserve initialization, Graph preparation, P95, raw samples, and baseline drift.
The conclusion is the best candidate under the tested conditions, not a global optimum among all
possible combinations.

## v10 Serving-Module Confirmation

The final three serving configurations each ran three independent full COCO evaluations:
fair baseline 23.811608 ms, original FP32 Graph serving 14.411271 ms, FP16 Graph 12.559066 ms,
and serial AOT FP16 Graph 11.725171 ms. The best compatible configuration was 2.030811x the fair
baseline and 1.229088x the original service (18.64% lower processing latency). All three AOT AP
results were 39.156946% versus 39.170516% for the baseline, a 0.013570-point decrease; prediction
SHA values all began with `561228d6`. The final baseline measured 24.362520 ms, 2.31% slower than
the initial run; it is recorded as measured, without latency adjustment or sample deletion. Final
summary SHA: `d6fc3d933c390867abf3acb68c120c29bc11c3029b540ebfe846899cc739a5bd`.
`models/inference-plan.json` comes only from this version's actual `serving_adapter`. HF commit
`78f5bf06` was recompiled with AOT and executed on a real Blackwell GPU; the Git blob identities of
all 18 release files matched the pinned commit. No T4 binary was migrated directly. Startup
compilation (45.748 seconds), first-allocation load (1.186 seconds), Graph preparation
(1.847 seconds), and steady-state frames were recorded separately. Two independent calls each
delivered 16/16 frames, and four-image diagnostics matched 13/13 reference boxes; this does not
replace full COCO verification on HF.

A short public Chrome run measured 9.772 FPS and a maximum decode time of 448 ms. In a natural
55-second completion test, 571 frames were submitted and 566 returned; five in-flight frames were
not returned, maximum decode time was 566 ms, and the public 10 FPS/500 ms/full-delivery criteria
did not all pass. GPU computation, Graph reuse, and returned-frame hashes were normal. See
[PHONE-LIVE.md](PHONE-LIVE.md) for hard-deadline, continuous-sampling, and public-network wait
boundaries. No frames were deleted, the budget was not extended, and no automatic retry was added
to manufacture a passing result.

The following are historical CPU and legacy-interface experiments. Their conditions, original
reports, and failures are preserved and are not current GPU conclusions.


# YOLO26n Inference Acceleration Comparison and Fusion

2026-10-02: Retain official YOLO26n weights, FP32, 640×640, confidence 0.25, JPEG 80, the
client's 200 ms interval, and server limits of 20% CPU/256 MiB/32 tasks. Select a bounded ORT CPU
arena, I/O Binding, reusable input/output tensors, direct FP32 normalization, NumPy batch
postprocessing, and font caching. Existing ONNX, fixed shape, single session, and end-to-end NMS-free
head remain in use.

The user authorized testing INT8/FP16, with deployment decisions deferred until verification.
Neither outperformed fused FP32 in measurements, so FP32 remained deployed. Model-weight hashes
and detection/image parameters are unchanged; no automatic backend switching, fallback, or retry
was added.

## Measured Results

Server: Ubuntu 22.04, two logical CPUs (hyperthreads on one physical core), Intel Xeon Platinum;
AVX2/AVX512 supported, no GPU, and no VNNI/BF16 in CPU flags. Experiments ran serially in
temporary systemd units limited to CPUQuota=20%, MemoryMax=256M, and TasksMax=32, with OpenBLAS/OMP
single-threaded and Django included. The normal PRS service remained online and other applications
were not stopped; background load and CPU quotas can affect variance in short tests.

For each run, the public bus image used one untimed warmup and ten measurements. Timing includes
JPEG decode, preprocessing, inference, postprocessing, and drawing/encoding, but excludes network
and phone capture. Four COCO8 val images were also used to compare outputs. The baseline came from
commit `107e7e0`; intermediate candidates used its preprocessing/postprocessing, while production
used the final implementation. Raw JSON is in [benchmarks/2026-10-02](benchmarks/2026-10-02/).

| Method | Mean Total Processing (ms) | Median (ms) | P95 (ms) | Mean CPU (ms) | Peak RSS (MiB) | Conclusion |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Original FP32 / arena disabled | 625.3 | 608.6 | 746.0 | 130.3 | 192.8 | Baseline |
| FP32 / default arena | 551.7 | 546.2 | 606.6 | 111.4 | 221.3 | Lower allocation overhead |
| FP32 / binding and buffer reuse only | 641.2 | 613.5 | 701.0 | 129.6 | 179.8 | No clear standalone speedup from binding |
| FP32 / default arena + binding | 549.8 | 509.9 | 648.2 | 109.8 | 222.5 | Effective combination |
| FP32 / 128 MiB arena + binding | 560.5 | 548.8 | 655.3 | 112.6 | 222.8 | Explicit memory-pool cap |
| **Final fused FP32** | **509.7** | **502.2** | **555.6** | **102.2** | **220.5** | **Deployment candidate** |
| FP16 (initial Cast retained) | 608.6 | 603.2 | 693.7 | 120.8 | 228.4 | Slower than fused FP32 |
| INT8 QDQ / per-channel | 1161.8 | 1193.8 | 1252.1 | 233.8 | 218.8 | This configuration was notably slower |
| OpenVINO 2026.4.1 / forced FP32 | 572.4 | 553.2 | 695.0 | 116.7 | 246.1 | Slower with little memory headroom |

The final combination reduced mean processing time by **18.5%** and CPU time by **21.6%** versus
the baseline. This is a short resource-constrained test on fixed samples, not a statistical
significance conclusion, COCO accuracy evaluation, or phone-FPS guarantee. Four images cannot
establish output equality or recognition accuracy for all inputs. Network, phone encoding, and
the existing 200 ms interval remain, so expected actual return rate is about 1 FPS.

For the bus image, final-combination raw-prediction and JPEG SHA256 values matched the baseline;
both returned five objects. For all four COCO8 val images, final-combination raw SHA256 values were
identical and all 13 baseline boxes remained. FP16 matched 13/13, with a mean absolute confidence
difference of about 0.00155; OpenVINO matched 13/13, with a difference of about 4.7e-7; INT8
matched 12/13, missing one baseline backpack prediction, with a difference of about 0.0802. This
is a greedy output comparison for same-class boxes at IoU≥0.5. **The reference is FP32 prediction,
not annotated ground truth**; the result is not recall, precision, or mAP, and it does not prove
that the backpack is a real false negative. See `quality.json` and `production-quality.json`.

INT8 used four COCO8 train images for MinMax calibration; val images were excluded. It used QDQ,
S8S8, per-channel, and Conv/MatMul only. The calibration set is too small for production quality
acceptance. FP16 kept FP32 I/O and retained the original Cast node to avoid incorrect converter
type annotations; a full ONNX check was run after conversion. Neither experiment replaced the
production model. The exact cause of slower performance has not been diagnosed per operator and
cannot be inferred from the "INT8" label alone.

## Compatible Methods and Applicability Boundaries

The table distinguishes existing methods, changes in this work, tested options, and future
candidates. Mutually exclusive backends are not described as additive.

| # | Method | Treatment in This System |
| ---: | --- | --- |
| 1 | Export PyTorch to ONNX to avoid running the training framework online | Existing |
| 2 | Reuse InferenceSession for the process lifetime | Existing |
| 3 | Fixed 640 shape, batch=1 | Existing; suitable for low-latency single frames |
| 4 | Constant folding | Existing ORT_ENABLE_ALL default, now explicit |
| 5 | Remove redundant Identity/Reshape and similar nodes | Same as above; ORT applies it according to graph applicability |
| 6 | Fuse Conv/activation, matrix, and similar operators | Same as above; not every fusion is claimed to trigger in this graph |
| 7 | CPU NCHWc layout optimization | Same as above; the backend selects based on hardware |
| 8 | Weight prepacking | Retain ORT default; benefit not quantified separately |
| 9 | Static-shape memory pattern | Existing default, now explicitly retained |
| 10 | Reuse CPU arena | Enabled and verified in this work |
| 11 | Grow memory pool by allocation size and set a capacity limit | Added here; 128 MiB model pool, overall system limits unchanged |
| 12 | Preallocate and reuse input tensor | Added here; contiguous FP32 NCHW retained |
| 13 | Preallocate and reuse output tensor | Added here; fixed [1,300,6] |
| 14 | I/O Binding | Combined with memory pool here; on CPU this is not GPU-transfer acceleration |
| 15 | Normalize uint8→FP32 directly into the destination buffer | Added here; avoids intermediate FP32 arrays/copies |
| 16 | Write transposed view and normalization into contiguous destination | Added here; color order, scale, and padding unchanged |
| 17 | Batch-validate classes/scores, filter first, then create dictionaries | Added here; invalid low-confidence data still errors |
| 18 | Vectorize box restoration/clipping with NumPy | Added here; float64 matches original Python-float calculations |
| 19 | Cache the same font | Added here; font size, color, and drawing contract unchanged |
| 20 | Single-threaded sequential execution and OpenBLAS control | Existing; retain resource isolation |
| 21 | Prevent idle extra threads | Explicitly configured here; no extra speedup claimed in single-threaded mode |
| 22 | YOLO26 one-to-one head without NMS | Existing; no external NMS added |
| 23 | Native JPEG codec library | Keep Pillow; no different preprocessing library substituted |
| 24 | Do not write files or store media | Existing, avoids frame I/O |
| 25 | One in-flight frame and no backlog queue | Existing, avoids stale display; no dropped frames to inflate FPS |
| 26 | Offline-optimized graph, ORT format, compilation cache | Future startup optimization; steady-state gains in long sessions must not be counted again |
| 27 | ONNX simplification/manual graph reduction | Future candidate; may overlap existing graph optimizations and requires output checks |
| 28 | Warmup | Used in experiments; production does not use synthetic frames as camera input or add startup computation |
| 29 | OpenVINO/oneDNN CPU backend | OpenVINO did not win in measurements; oneDNN untested; do not deploy two engines together |
| 30 | FP16/BF16/mixed precision | FP16 did not win in measurements; BF16 requires hardware validation |
| 31 | INT8 PTQ, QDQ/QOperator, alternate calibration | Current QDQ did not win; other formats/calibration are separate candidates and cannot replace it automatically |
| 32 | QAT, structural pruning, distillation, smaller model | Requires training and quality evaluation; Nano is already the lightest model in this family |
| 33 | Smaller input, dynamic shapes, ROI cropping | May reduce compute but may miss targets; 640 was unchanged |
| 34 | Raise threshold, reduce max_det, prefilter classes | Would change output targets; existing contract unchanged |
| 35 | Multithreading, parallel graph execution, CPU pinning/NUMA | Requires tests under resource limits; current server has one physical core and no reserved core, and did not preempt other apps |
| 36 | Batching, multiple workers, pipeline concurrency | Throughput-oriented and may increase latency/memory; single-frame/single-worker boundaries unchanged |
| 37 | CUDA/TensorRT/CUDA Graph/GPU I/O Binding | Requires NVIDIA GPU; current server has none |
| 38 | XNNPACK/NCNN/TVM/custom SIMD/C++ operators | Adds build and compatibility-validation costs; untested, no benefit claimed |
| 39 | CoreML/LiteRT/NNAPI/QNN/phone NPU | Changes to on-device inference; requires support and quality verification on each phone |
| 40 | Browser WebGPU/WASM SIMD/WebNN | On-device candidates; compatibility and power use require validation |
| 41 | WebSocket detection, HTTP connection reuse, binary protocol | Network-layer candidates that do not reduce CNN computation; currently same-origin binary JPEG |
| 42 | Draw boxes in browser, lower JPEG quality/frame interval | Could reduce transfer or raise display frequency but changes existing annotated-image response/capture parameters; not implemented |
| 43 | Cache results, skip frames, or replace some detection with tracking | Changes realtime recognition semantics; not added |
| 44 | Add CPUs, separate inference server/GPU, raise CPUQuota | Largest resource headroom but requires an explicit new resource budget; not implemented |

References: [ORT graph optimization](https://onnxruntime.ai/docs/performance/model-optimizations/graph-optimizations.html),
[thread tuning](https://onnxruntime.ai/docs/performance/tune-performance/threading.html),
[I/O Binding](https://onnxruntime.ai/docs/performance/tune-performance/iobinding.html),
[allocator](https://onnxruntime.ai/docs/get-started/with-c.html),
[quantization](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html),
[OpenVINO precision](https://docs.openvino.ai/2026/openvino-workflow/running-inference/optimize-inference/precision-control.html),
and [Ultralytics deployment formats](https://docs.ultralytics.com/guides/model-deployment-options).
The listed items describe capabilities and candidates; they do not replace local measurements.

## Historical Records

The records above come from independent experiments performed at the time; measurements, data
splits, and evaluation protocols are retained. Execution tools for low-precision/OpenVINO
experiments that were not deployed have been removed; their original implementation can be
retrieved from Git history. Current runtime retains only Echo and the latest HF ZeroGPU video path.
Original reports remain under `benchmarks` for traceability. New same-class box-consistency checks
use `tools.benchmark_live_remote` and `tools.compare_detections`; they do not rerun the old backend
comparison.

## Kaggle GPU Atomic and Combined Experiments

On 2026-10-02, the user authorized testing acceleration methods on Kaggle and deploying methods
that were effective and compatible with ZeroGPU to HF. Private notebooks contain the
[atomic experiments](https://www.kaggle.com/code/heropig/prs-yolo26-acceleration-20261002) and
[combined experiments](https://www.kaggle.com/code/heropig/prs-yolo26-refinement-20261002). The
private data bundle is generated from an explicit manifest in `tools/prepare_kaggle.py`; it
contains only inference/benchmark source, pinned official weights, the public bus image, and the
four existing COCO8 val images. Credentials, private images, and the full project are not uploaded.

Reproduce with `python tools/prepare_kaggle.py --phase atomic` or `--phase refinement`. Generated
files go into Git-ignored `.kaggle-bundle`, then use the authenticated Kaggle CLI to upload a
private dataset version and submit the GPU notebook. Kaggle automatically extracts uploaded ZIPs
and the entry point reads mounted files. The first version incorrectly searched for the ZIP and
failed before inference; the second corrected this input contract. That failure is excluded from
speed results. The combined experiment also explicitly configured the existing CUDA driver's
linker name to fix an AOT compiler environment error where `-lcuda` could not be found. The driver
and model were not replaced.

Atomic experiments used a Tesla T4, PyTorch 2.8.0+cu128/CUDA 12.8, a single GPU on GPU0, and
single-threaded CPU-side numerical processing. Each configuration ran in a separate subprocess.
For the same public bus image, there were 10 warmups before measurement and three rounds, with 30
GPU-stage runs and 12 full-image runs per round. The GPU stage includes input upload, inference,
and output download; CUDA events separately measure forward. Full-image processing also includes
decode, existing Pillow preprocessing, threshold filtering, drawing, and JPEG80 encoding, but
excludes Gradio/network/queueing. Setup (initialization, warmup, and optional compilation/capture)
is recorded separately. A single first eager-forward duration is not a cold-start statistic.
Environment caches and load can vary across rounds; small differences are not treated as stable
gains.

The full raw report is `benchmarks/2026-10-02/kaggle-atomic.json`. First-round median GPU-stage/full
processing times were FP32 baseline 9.377/19.467 ms, buffer reuse 9.085/19.096 ms, and CUDA
Graph+buffer reuse 5.192/14.846 ms. For the latter, raw predictions matched bit-for-bit on all
four images and all 13 reference objects matched by class at IoU≥0.5. channels-last, cuDNN
autotuning, FP16, and BF16 did not outperform this configuration in the round. T4 lacks native
TF32/BF16 compute; the BF16 results used a runtime-supported non-native path and cannot be
extrapolated to Blackwell. The first AOT attempt failed because the linker could not find the
`libcuda.so` name; the failure is retained and AOT was reverified in a separate combined experiment.

Weights, batch 1, size 640, threshold 0.25, 300 candidates, JPEG80, and validation images remain
fixed. Low precision is used only in explicitly named experiment configurations. Prediction
consistency on four images is not mAP or real-video accuracy. Warm GPU results exclude ZeroGPU
per-task process and GPU scheduling costs. Kaggle-compiled binaries must not be deployed directly
to a different GPU on HF. Production choices and actual HF verification are recorded separately.

The full combined-experiment report is `kaggle-refinement.json`: same-round baseline GPU-stage/full
image 9.685/19.394 ms, FP32 Graph 5.051/14.500 ms, AOT 5.302/14.504 ms, and AOT+Graph
5.092/14.257 ms. FP16+channels-last+Graph processed a full image in 12.983 ms, but had a mean
confidence difference of about 0.00164, so production remained FP32. All configurations matched
13/13 reference boxes; only the Graph path had bit-identical raw predictions. The exact
`spaces.aoti_compile/aoti_apply` compatibility experiment is in
`kaggle-zerogpu-compatibility.json`; it also verified 13/13 matches but does not replace actual
ZeroGPU verification.

Actual HF hardware logs identify RTX PRO6000 Blackwell Server Edition MIG 2g.48gb. The first
AOT version failed while packing non-contiguous anchor constants. It ran successfully after making
only anchor storage contiguous before export; all 13 reference boxes matched across the four
original images, with a mean confidence difference of 4.79e-7. However, the single-frame GPU stage
took 1,415–1,960 ms, slower than the eager first frame at 438.7 ms. AOT was explicitly withdrawn,
the original single-frame path returned to eager, and no automatic switching was added. After the
revert, the first three of the original four-frame checks had GPU-stage times of 510/562/602 ms;
the fourth exceeded the established 55-second network deadline, so full verification did not
succeed. The failure was not retried. Partial results are not recorded as a complete success.

The user then explicitly selected "reuse CUDA Graph and buffers within one GPU task while retaining
the original per-frame interface." A separate `/detect_clip` endpoint was added for up to 16
ordered JPEGs, with batch remaining at 1 and no frame skipping. Within the GPU task it performs 10
warmups, captures and reuses fixed input/output buffers, then destroys the Graph when the task
ends. Existing `/detect` and `/status` are unchanged. The client remains limited to 55 seconds;
only the new clip-verification tool explicitly uses a 24 MiB response limit, while the original
single-frame limit remains 2 MiB.

The [clip experiment](https://www.kaggle.com/code/heropig/prs-yolo26-video-clips-20261002) used
three independent process rounds. Clips were formed by repeating the original four images; warmup
and Graph capture were included, while model-root initialization was excluded for both strategies.
The first version failed before inference because absolute-path execution lacked the module search
root. After correcting the import path, the second version completed without changing inputs,
warmup, or comparison method. Both `kaggle-video-clips-v1-failed.json` and
`kaggle-video-clips.json` are preserved.

| Frames | Eager GPU-Task Median (ms) | Graph GPU-Task Median (ms) | Eager/Graph | Raw Identical in Every Round |
| --- | ---: | ---: | ---: | --- |
| 1 | 493.639 | 678.764 | 0.727 | Yes |
| 4 | 551.134 | 711.709 | 0.774 | Yes |
| 8 | 561.838 | 743.555 | 0.756 | Yes |
| 16 | 656.897 | 818.014 | 0.803 | Yes |

For short clips, the warm Graph gains did not offset 667–685 ms of initialization. Repeated static
images validate the interface and outputs; they are not a video-dataset/mAP/FPS claim. HF server
logs show an 868.2 ms GPU task for four frames (setup 854.7 ms) and an 863.2 ms task for 16 frames
(setup 816.0 ms). Both tasks ran, but the client spent more than 55 seconds receiving each result.
An independent eight-frame check also timed out during receive and returned only 81,907 bytes.
The 16-frame POST obtained an event ID in about 2,922 ms, indicating this failure occurred during
result receive rather than submission. No specific network/hosting root cause was established; no
timeout increase, compression, frame dropping, or response fallback was added. Deployment and
failure reports are `hf-kaggle-deployment.json`, `hf-video-clips.json`, `hf-video-16-frames.json`,
`hf-video-8-frames.json`, and the filtered `hf-clip-server-log.json`.

The deployed Graph clip endpoint remains experimental; end-to-end acceleration within the
55-second budget has not been established. The existing phone per-frame entry still uses the
original endpoint. Longer clips, fewer initialization warmups, a different response format, or a
resident GPU would change current boundaries/experimental conditions and require an explicit
decision followed by a separate experiment. These changes cannot be deployed directly based on
warm-state measurements.

## Approved Independent Long-Clip Experiment

The user explicitly approved adding 32/64/128-frame experiments while retaining the short-clip
conditions and results above. Median GPU-task times including initialization across three
independent Kaggle rounds (eager/Graph, ms) were 813.749/869.644 for 32 frames,
1089.045/1048.759 for 64 frames, and 1678.652/1365.432 for 128 frames. The 128-frame result was
1.229x; the 64-frame gain was small. Per-frame raw hashes matched in every round. The report is
`kaggle-video-long-clips.json`; the notebook is
[prs-yolo26-video-long-clips-20261002](https://www.kaggle.com/code/heropig/prs-yolo26-video-long-clips-20261002).

An independent public-image diagnostic completed on HF: 16-frame eager/Graph GPU times were
696.813/770.603 ms; 128-frame times were 1216.825/678.289 ms (1.794x), with Graph initialization
at 310.472 ms. Per-frame raw and rendered JPEG hashes matched across all 128 frames; the original
four images matched 13/13 reference boxes. This is a single observation that transfers metadata
only, not full-video delivery or a stable latency distribution. Full report:
`hf-public-clip-profile.json`.

An explicit `/detect_video_stream` transport protocol was added. It uses the same JPEG and FP32
processing, then transfers complete JPEGs and boxes in ordered chunks no larger than 16 KiB after
one Graph task. The client validates every JPEG and the final completion event. The limit is 128
frames and remains 55 seconds; this does not replace the old endpoint and adds no fallback or
retry. Transport measurements are in `hf-video-stream.json`; successful computation does not
establish successful receipt.

In the first complete-JPEG transport measurement, all 16 frames were validated within 22,968 ms
and the GPU task took 947.593 ms; raw and rendered JPEG hashes matched the public compute test.
For 128 frames, TLS writing of the POST body exceeded the established deadline; zero frames were
received and no task event ID was obtained. This was not a GPU/SSE computation failure and no
platform root cause was assigned. The report preserves `complete:false` and the completed 16-frame
result. No retry, budget extension, or request-content substitution was made. End-to-end
acceleration for 128 frames has not been established.
