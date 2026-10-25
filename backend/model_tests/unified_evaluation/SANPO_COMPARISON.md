# Continuous SANPO Test comparison

Use the downloaded original Test stream for A/B/C/E evaluation, keeping the old KITTI E weights.
This is an external diagnostic: SANPO mask-surface median meters differ from KITTI reference-z.
No training, calibration fitting, threshold changes, downloads or original artifact overwrites occur.

From backend:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m model_tests.unified_evaluation.sanpo_comparison `
  --method all `
  --e-checkpoint models/experiments/object-distance-full-01/best.pt `
  --depth-interval 5 --device cuda:0 `
  --output model_tests/outputs/sanpo-abce-kitti-e-01
```

Each method runs serially in an isolated process. Use a new output directory per run.
For a chronological smoke prefix add `--limit 13 --warmup 1` and change the output directory.
The default paths are `sanpo/manifests/test-frames.jsonl` and `sanpo/processed-agnostic-manual`.
The expanded diversity subset is not substituted: its additional Test sessions lack continuous
downloaded RGB. Dataset/session counts and source/target fingerprints are recorded in metrics.json.

## Continuous inference and sparse scoring

All 430 original Test RGB frames from one official Test session feed detection and tracking in
order at nominal 15FPS timestamps. Only 72 human-labeled frames (1,430 object observations) are
scored; the other 358 frames contribute temporal state and inference latency, not machine-label
accuracy. Every method uses the same images and accepted GT. C resets once at the session start,
after cold/warmup state is discarded. It uses the existing track-ID cache on non-depth frames.

Manual annotations occur every six frames. With interval3, all scored frames coincide with depth
inference; that would hide cached-distance errors. Default interval5 scores both depth and cached
frames (15 depth and 57 non-depth annotated frames for the full stream). C runs 86 depth calls
over430 frames (20%). `--depth-interval 1` runs every frame. Other positive intervals are allowed;
the sampling phases and a warning for complete depth-phase alignment are recorded explicitly.

Only predicted-box mode is supported: sparse GT replacement would alter tracking and requires
guessing old E's class for SANPO's broad vehicle label. The old legacy single-method SANPO entry
point remains available and unchanged.

## Metrics and taxonomy

Overall and four-band MAE/AbsRel/coverage are reported. Bands are `[0,10)`, `[10,20)`, `[20,40)`,
`[40,infinity)`; Weighted MAE uses0.4/0.3/0.2/0.1. Empty or wholly missed bands produce null,
never renormalized weights. AbsRel/coverage are fractions; MAE/Weighted MAE are meters.

Old KITTI E supports car/person distances only. Predictions map COCO person to pedestrian and
car/bus/truck/motorcycle/bicycle to SANPO vehicle; unsupported E categories retain missing distance.
Generic vehicle GT does not imply every vehicle is a car. Sky/road were already excluded during
manual-instance preparation. All accepted independent object classes remain in the main denominator,
including classes the detector or estimator cannot support. The pedestrian/vehicle subset is also
reported in metrics.json, and common-valid-object errors are separate from full-GT coverage.

## Artifacts

- `comparison.md`: overall and four-band tables, with SANPO limitations.
- `comparison.csv`: A/B/C/E MAE, AbsRel, Weighted MAE, coverage, latency/FPS, depth calls/ratio.
- `distance_bands.csv`: four bands, counts, MAE, AbsRel and coverage for each method.
- `paired_comparison.csv`: errors on the same valid targets across successful requested methods.
- `temporal.csv`: depth/non-depth inference counts, manual scoring counts, latency and errors.
- `metrics.json`: complete results, subset metrics, paired denominator, source identity and phases.
- `passes/{a,b,c,e}-predicted/`: options, logs, predictions, all-GT observations and result.json.

Tables update after each pass. Only status=complete establishes all requested passes succeeded.
Failures stay visible and return a nonzero process status. Native A/B/C retain CUDA Graph FP32;
E retains its eager checkpoint settings. Timing excludes PNG decoding and output I/O for all.
Do not claim a controlled architecture-only speed comparison or navigation safety from this run.

## Multiple-session diagnostics and profiling

`--allow-diagnostic-sessions` explicitly permits original SANPO Train/Validation sessions for
cross-dataset diagnostics with the old KITTI checkpoint. Each source row must retain `source_split`
matching the original local split lists. Reports label this mixed scope; default official-Test
restrictions remain unchanged. New prepared manifests may combine existing manual target records
without changing boxes, distances, source manifests or original splits. They must be stored under
the same SANPO root and have their own manifest fingerprints.

`--profile-stages` wraps original instance methods without changing detector, depth, tracker or
association settings. `stage_latency.csv` reports depth-model calls (including its preprocessing),
GMC camera-motion estimation, association stages, tracker image/Boxes handling and tracker-state
residual. Tracking total includes all tracking sub-stages; never add inclusive and nested values
twice. Association includes associated state updates, not only the assignment solver. Depth timing
adds CUDA barriers; all compared methods use the same profiling option. `other_ms` captures the
remaining pipeline overhead. `sessions.csv` reports frame/GT/matched/valid counts, errors and
latency separately for each original Session. Main comparison also includes matched_objects,
which is distinct from valid_predictions when a matched detection has no distance (especially C).

The existing evaluation adapters enable BoT-SORT only for C. A does not include tracking in its
timing. This compares the current execution paths, not a controlled change of depth frequency
alone. `distance_postprocess_ms` is the distance-stage residual after depth-model prediction;
for E it includes distance-head work. All timings exclude file decoding and report writing.

## Depth precision and resolution experiments

For A-only optimization experiments, `--depth-precision fp16` and `--depth-image-size 640`
select independent depth settings. Defaults remain FP32 and 768. Detector weights, precision,
image size, thresholds and ROI median fusion are unchanged. The saved identity includes the
requested settings and actual backend precision; a precision mismatch fails the pass.
Nondefault depth experiments require `--method a`; existing A/B/C/E commands retain defaults.
Run each condition in a fresh output directory on identical source frames. Compare near-band
MAE and coverage as well as latency. FP16 and resolution changes may alter distance accuracy.
Profiling includes extra CUDA synchronization, and end-to-end timing excludes file decoding.

For a full mixed-session A experiment, run from backend:

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m model_tests.unified_evaluation.sanpo_comparison `
  --method a `
  --processed model_tests/datasets/sanpo/processed-continuous-diagnostic-01 `
  --frames model_tests/datasets/sanpo/reports/continuous-diagnostic-01/frames.jsonl `
  --allow-diagnostic-sessions --profile-stages --warmup 3 `
  --depth-image-size 640 --depth-precision fp16 `
  --output model_tests/outputs/sanpo-a-fp16-640-new
```

Use size 768 and precision fp32 for a fresh baseline, then size 768 and precision fp16 to
isolate precision before changing resolution. Use a distinct output directory per condition.
No frame limit is applied. Preserve the original official/mixed split scope in interpretation.

## Detailed synchronized stage profiling

`pipeline_breakdown.csv` lists mutually exclusive wall-time stages whose means sum to the
measured end-to-end mean. CUDA-event rows in the separate detailed table are nested diagnostics.
Detector runner wall time uses `perf_counter`, not GraphRunner's legacy returned `monotonic`
duration: Windows GetTickCount64 can have 15.625ms resolution. The existing main E2E timer already
used `perf_counter`; old main accuracy and E2E results are not rewritten.

Add `--profile-detailed` to an A-only comparison command. It implies `--profile-stages` and
retains original predictions. `stage_latency.csv` includes detector CPU preparation, the existing
CUDA Graph runner (H2D/replay/D2H), detector CPU decoding, Depth source conversion,
Depth preprocessing, forward, postprocessing, and ROI fusion/median stages. Existing
`detection_forward_event_ms` is the CUDA-event interval around graph replay only.
Depth forward and postprocess have synchronized wall and CUDA-event intervals; CPU/mixed
preprocessing uses synchronized wall timing. Whole fusion and summed ROI medians are nested.

Each CUDA stage drains previous work before starting, records events on the current stream,
waits for completion before reading elapsed time, and reuses event pairs. CUDA intervals can
include launch gaps and are not pure kernel busy time. They overlap wall time; never add them
to wall totals. Residual fields expose source/API, collection/host transfer and instrumentation
overhead rather than falsely labeling it as model forward. `predictions.jsonl` also records
`roi_timings`: per-detection bounds, pixel count, synchronized wall time and CUDA interval.
These include unmatched detections and non-manual frames, not only scored GT matches.
`passes/a-predicted/roi_timings.csv` exports all ROI records; `roi_summary.json` contains
object-weighted mean/P50/P95 statistics, rather than an average of per-frame averages.

Depth predictor hooks attach after the excluded cold call and before steady evaluation. Scoped
module hooks are restricted to one serial isolated evaluation worker and restored even on failure.
This diagnostic changes synchronization frequency, so its FPS is not normal production FPS.
The in-memory RGB boundary still excludes file decoding and report writing.

## Exact A optimization and formal counterbalanced timing

`--a-fast` is an opt-in A implementation at the original depth768/FP32 settings. The default
A path remains available. Neither weights nor detection/depth input resolution, matching,
thresholds, normalization, clipping or distance statistics change. B/C/E are not switched.

Direct PIL raw BGR packing avoids exporting a separate RGB array followed by CPU color
conversion. The official predictor still handles letterboxing and normalization. ROI fusion
downloads the aligned FP32 depth map once per nonempty frame, filters positive finite pixels,
and uses NumPy partition for the exact central order statistic(s). For even counts it halves
both central FP32 values before adding, preserving the original Torch arithmetic. It avoids
per-box GPU boolean compaction and two GPU kthvalue calls. This explicitly trades a full-map
host transfer/CPU workspace for fewer GPU launches and implicit synchronizations. Empty boxes
skip the transfer; sparse frames or different hardware may not benefit. No downsampling or
approximate percentile is used. Unsupported dtypes fail instead of silently falling back.

Run the dedicated formal comparison from backend:

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m model_tests.unified_evaluation.a_performance `
  --rounds 2 `
  --reference model_tests/outputs/sanpo-a-detailed-profile-01/passes/a-predicted/predictions.jsonl `
  --output model_tests/outputs/sanpo-a-fast-formal-new
```

Defaults select the existing 1,771-frame continuous diagnostic manifest and processed targets.
`--processed`, `--frames` and `--root` can override them. `--reference` is optional and adds an
exact audit against prior A outputs. No limit is applied unless `--limit` is explicitly given.
Two rounds measure 3,542 frames per variant, while accuracy counts the 5,432 unique GT
observations only once. Each frame runs both complete pipelines, with AB/BA order alternating
by frame; the second round reverses each frame's order. One shared resident detector/depth
pair eliminates between-process model setup differences. No output or depth map is reused.
Avoid concurrent training/inference jobs during measurement.

Detailed profiling is disabled. `FormalA` uses CPU `perf_counter` wrappers around source
packing/setup and the whole completed fusion. One depth-completion barrier separates depth
from ROI work for both variants. There are no per-ROI timing hooks, events or extra barriers.
The original detector's CUDA graph timing and normal predictor's own timing remain. Formal
input time includes packing plus `setup_source` for **both** variants; ROI time includes the
full-map download, filtering, partition and result packaging. End-to-end timing starts with
in-memory RGB and ends with completed object outputs, excluding file decode, scoring and
JSON writing. These are coarse stage measurements, not an absolutely zero-instrumentation run.

Outputs:

- `comparison.csv`: mean/P95 ROI, input and end-to-end time, FPS, MAE and coverage.
- `strata.csv`: per-round and first/second-position means/P95 to inspect order effects.
- `metrics.json`: configuration, dataset/weight identities, full latency and accuracy tables.
- `predictions.jsonl`: every measured frame/variant/round, output and coarse timing.
- `prediction_checks.json`: exact per-object paired, repeated-round and optional prior-baseline
  audits; missing values and structural changes are reported separately.
- `baseline-observations.jsonl`, `optimized-observations.jsonl`: unique first-round scored GT.

For an optimized-only evaluation, use the ordinary `sanpo_comparison --method a --a-fast`
command with the same processed/frames paths and a new output folder. Do not combine
`--a-fast` with `--profile-detailed`; use the formal comparison for this implementation.
Production callers may explicitly set `DepthEstimator(..., fast_input=True)` and
`DistanceStrategy.fast_roi = True` for A. Existing callers keep their original defaults.

## Independent detector preprocessing optimization

`--a-preprocess-fast` removes the intermediate detector letterbox canvas while retaining
exact Pillow BILINEAR pixels, coordinates and FP32 division. It can be combined with `--a-fast`
and remains off by default. Use `a_performance --experiment preprocess --rounds 2` to compare
the current a-fast baseline with the combined implementation. This holds Depth input/ROI
optimization on in both conditions. The previous distance experiment remains the default.

See [DETECTION_PREPROCESS.md](DETECTION_PREPROCESS.md) for full commands, CPU substage profiling,
PNG versus production JPEG ingress scope, exact-input auditing and rejected OpenCV candidates.

## Near-distance reports from saved predictions

Use `range_report` to compute `[0,5)`, `[5,10)` and `[10,20)` meter statistics without running
models again. Grouping uses GT distance, not predicted distance. Existing detections, matching
and all-GT denominators are preserved. MAE uses only matched valid distances; empty valid sets
produce null. GT at or above 20 meters is counted separately as outside the requested ranges.

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m model_tests.unified_evaluation.range_report `
  --run model_tests/outputs/sanpo-abce-kitti-agnostic-full-01 `
  --method a `
  --output model_tests/outputs/sanpo-abce-kitti-agnostic-full-01/a-near-ranges-02
```

The fresh output folder contains `distance_ranges.csv` (MAE, counts, coverage, RMSE and AbsRel)
and `metrics.json` (same values, source hashes and distance definition). Existing reports are
never overwritten; choose a new output folder for each invocation. Other methods can be selected
with `--method b`, `c` or `e`. Run from backend with the existing evaluation Python environment.
