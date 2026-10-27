# Pixel-exact A detector preprocessing

## Responsibilities and scope

`--a-preprocess-fast` selects `core.detection_preprocess.letterbox_fast` for A. It is independent
of `--a-fast`, which retains the previously implemented Depth input/ROI optimizations. Both
flags default off in `sanpo_comparison`; explicit A/Depth768/FP32 is required. The original
`core.detector.letterbox` remains unchanged. Model weights, confidence/IoU thresholds, coordinate
restoration, distance calculations, Depth normalization and precision are not changed.

The fast transform retains Pillow BILINEAR interpolation, Python round for resized dimensions,
the original scalar inverse scale, integer centered padding and FP32 division by 255. It
normalizes the resized image directly into the caller's NCHW tensor and fills the padding
with the exact FP32 value 114/255. All output pixels are overwritten, including after shape
changes; there is no cached image/padding content. The source image is never mutated.

Both versions already reuse the final FP32 tensor (4,915,200 bytes); this is not a newly claimed
optimization. The removed operations are PIL canvas allocation/fill and paste, followed by
export of the full padded canvas to NumPy. The optimized export covers only resized pixels.
Pillow resize and its internal workspace, the resized-image byte export and final FP32 stores
remain. CHW/NCHW transforms are views, not physical transposition copies. Pixel-exactness is
checked at the FP32 bit level, including padding and normalization.

## CPU attribution and ingress

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m model_tests.unified_evaluation.preprocess_profile `
  --output model_tests/outputs/sanpo-a-preprocess-profile-new
```

Defaults select all 1,771 existing continuous SANPO frames. `--root`, `--frames` and an optional
`--limit` are available. A fresh output directory is required. The profiler writes `metrics.json`,
`stages.csv` and `frames.jsonl`. It alternates the original-operation diagnostic replica and
the actual optimized transform, checking both against unmodified `letterbox` for every frame.
CPU timings use `perf_counter_ns` or `perf_counter` (Windows QueryPerformanceCounter), not
the coarse `monotonic` clock. CUDA synchronization is unnecessary for these CPU-only operations.
Diagnostic substage times are separate from formal inference measurements.

Stages include geometry/buffer checks, resize (including Pillow workspace), canvas allocation
and fill/paste or direct FP32 padding, NumPy export (allocation/copy), CHW/NCHW views, FP32
division/output stores, and cleanup/control. The final tensor allocation occurs once outside
the per-frame loop. Copy/store costs are inside their owning stages; do not add memory-copy
estimates again. Substage P95 values are separate distributions and cannot be summed.

The actual SANPO manifest contains PNG files; formal E2E begins after RGB decode. Profiling
reads encoded bytes first to separate file IO from decode, then forces Image.load and measures
the independent RGB copy. Existing RGB PNG data needs no separate color-space conversion.
The source format/mode counts are exported so this cannot be mislabeled as JPEG performance.

The historical JPEG probe uses maximum-edge640 images. Current main accepts maximum-edge1280
JPEG images in production; the probe remains fixed at 640 for reproducibility. A separate diagnostic
encodes a representative subset at JPEG80 and that size, then measures header opening, decode
and RGB copy/conversion; it verifies decoded pixels against `decode_frame`. These derived JPEG
images are **not** used for the SANPO evaluation. Native JPEG YCbCr-to-RGB conversion is fused
into Image.load and cannot truthfully be isolated by Python wall timers. The high-resolution
SANPO resize cost is not a claim about the already-bounded camera ingress.

OpenCV INTER_LINEAR_EXACT is tested only as a candidate on sampled images. Any pixel differences
are recorded with count/max delta, and the candidate is not adopted or used in model evaluation.
Its resize timing excludes conversion from the PIL source to NumPy; it is not a complete ingress
speed comparison. No changed interpolation candidate is silently substituted.

## Formal counterbalanced model comparison

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m model_tests.unified_evaluation.a_performance `
  --experiment preprocess --rounds 2 `
  --reference model_tests/outputs/sanpo-a-fast-formal-01/predictions.jsonl `
  --output model_tests/outputs/sanpo-a-preprocess-formal-new
```

This compares A+a-fast with A+a-fast+a-preprocess-fast. Both keep the existing Depth/ROI
optimization enabled. `--experiment distance` remains the default and preserves the earlier
baseline-versus-a-fast comparison. New comparison tables include `prepare_ms` and its P95.

The same resident models, warmup protocol, decoded frame and coarse timing boundaries apply to
both conditions. AB/BA alternates per frame and reverses in the second round. Each condition
performs complete model inference; no prediction or depth result is reused. No detailed
preprocessing timers run inside formal inference. CPU subtimings come from the separate
attribution run. End-to-end timing retains the original completed-GPU boundaries and excludes
file decode, model initialization, warmup, scoring and JSON output. Accuracy counts each unique
GT observation once; two rounds give 3,542 timed calls per variant.

Outputs retain `comparison.csv`, `strata.csv`, `metrics.json`, `predictions.jsonl`, exact
`prediction_checks.json`, and per-variant unique GT observations. The optional reference is
used only for exact regression auditing, never as the speed baseline. Mixed original SANPO
splits remain a runtime/consistency diagnostic, not a newly held-out accuracy benchmark.

## Optimized-only run

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m model_tests.unified_evaluation.sanpo_comparison `
  --method a --a-fast --a-preprocess-fast `
  --processed model_tests/datasets/sanpo/processed-continuous-diagnostic-01 `
  --frames model_tests/datasets/sanpo/reports/continuous-diagnostic-01/frames.jsonl `
  --allow-diagnostic-sessions --depth-image-size 768 --depth-precision fp32 `
  --output model_tests/outputs/sanpo-a-preprocess-only-new
```

Leave off `--a-preprocess-fast` for the current a-fast baseline. Existing defaults and old
reports remain untouched. The new path stays opt-in even if the measured benefit is small
or hardware-dependent. Run performance experiments without concurrent training/inference.
