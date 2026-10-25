# External A/B/C/E distance evaluation

This independent experiment evaluates existing methods against **nuScenes3D box-center camera z**
in meters. It does not train models, change checkpoints, alter production defaults or call
navigation, motion/TTC, risk or alert routes. A/B/C still default to A in production. E's existing
training, KITTI evaluation and prediction commands remain separate.

## Modules and interfaces

| File | Responsibility |
|---|---|
| `dataset.py` | Full referenced-media audit, official geometry, immutable keyframe conversion |
| `adapters.py` | Existing native graph/strategy and strict trained E inference wrappers |
| `scoring.py` | Original IoU/error implementations with shared bins, coverage and paired IDs |
| `performance.py` | CUDA barriers, percentiles, source-clock sequencing and RSS sampling |
| `runner.py`, `__main__.py` | Explicit CLI, isolated serial workers and failure persistence |
| `reporting.py` | CSV comparison/bin/class/resource tables and Markdown report |
| `requirements.txt` | Optional dataset dependencies in a separate environment |
| `../../core/test_unified_evaluation.py` | Numerical, official SDK fixture and lifecycle checks |

Every adapter accepts the same original in-memory PIL RGB image and emits `class_name`,
`confidence`, original-image continuous `bbox=[x1,y1,x2,y2]`, nullable `distance_m` and
`distance_source`. Other compatible legacy fields, including C's actual `track_id`, are retained.

`ResidentDetector.predict` includes bounded JPEG ingress, rendering and risk. Calling that entire
service would change image resolution and timing scope and trigger unrelated output stages.
The independent native adapter therefore reuses its exact `load_model`, `GraphRunner`,
`letterbox`, `decode_boxes`, `DepthEstimator`, `DistanceStrategy` and `ObjectTracker` components.
It does not duplicate their detection, depth, geometric, tracking or caching algorithms.
E reuses `load_checkpoint`, `prepare_image` and `run_frame`.

Historical experiment policy is explicit: native A/B/C and raw-proposal diagnostics call
`decode_boxes(..., class_conf={})` to retain uniform confidence 0.25. Production main now uses
traffic-light confidence 0.12 and offers an extra ROI pass; these changes must not silently alter
the saved experiment protocol. Evaluation retains one full-frame detection pass and records an
empty `class_confidence_thresholds` mapping and `traffic_light_roi_pass=false`. E keeps its own
checkpoint confidence setting. Production defaults are not modified by evaluation.

## Environment and local data

The existing `.venv` contains native CUDA inference packages. The optional official Devkit pulls
in a headless OpenCV distribution, so use an isolated overlay environment to avoid modifying the
existing inference environment. This workspace already has `.venv-evaluation` prepared. It uses
an absolute `.pth` link to this workspace's `.venv/Lib/site-packages`; recreate it after moving
the workspace. To reproduce on another Windows checkout with an existing `.venv`:

```powershell
cd D:\PRS-PracticeModule\PRS-PracticeModule\backend
New-Item -ItemType Directory -Force test-results | Out-Null
& .\.venv\Scripts\python.exe -m pip freeze --all > test-results/evaluation-constraints.txt
& .\.venv\Scripts\python.exe -m venv .venv-evaluation
$inferencePackages = (Resolve-Path .venv\Lib\site-packages).Path
Set-Content -Encoding ASCII .venv-evaluation\Lib\site-packages\prs_inference.pth "import site; site.addsitedir(r'$inferencePackages')"
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -m pip install --constraint test-results/evaluation-constraints.txt -r model_tests/unified_evaluation/requirements.txt
& $py -m pip check
```

The constraints prevent changing inherited inference package versions. Keep the generated
constraints file local; `test-results/` is an ignored location suitable for environment snapshots.
Missing dependencies fail explicitly; the tool never installs packages or downloads datasets or
weights while running. Only use trusted local detector/checkpoint files.

Expected data root:

```text
model_tests/datasets/nuscenes/
  raw/{samples,sweeps,maps,v1.0-mini}/
  processed/{dataset_summary.json,frames.jsonl}
```

Prepare once, or select a new sibling output with `--output`:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m model_tests.unified_evaluation prepare
```

The current local `CAM_FRONT` conversion contains404 keyframes in10 scenes and3,492 accepted
objects (2,351 cars,1,141 people). These are full conversion counts, not the GPU smoke subset.
The audit checks all13 metadata tables and existence/containment of every31,210 referenced
sensor/map file, and verifies each selected camera image's shape,mode and JPEG structure. It
does not decode every LiDAR/radar sweep. No source file is modified. Table/image/manifest SHA256,
classes and filter reasons are saved. Missing data aborts with explicit paths/counts.

Coordinates use the [official Devkit](https://github.com/nutonomy/nuscenes-devkit): global Box
minus ego translation, inverse ego rotation, minus sensor translation, inverse sensor rotation.
The ego pose is the pose attached to the camera exposure's `sample_data`. Devkit `view_points`
projects camera coordinates using calibrated K. Twelve cuboid edges are clipped at the explicit
near plane (default0.1m), projected hulls are intersected with the image rectangle, and empty,
behind-camera, nonfinite or invalid boxes are rejected. Boxes can surround the image even when
no corner lies inside it. This differs deliberately from requiring one visible corner.

GT boxes are **projected3D cuboid silhouettes**, including hidden portions, not manual2D instance
boxes or camera-specific occlusion masks. Visibility tokens are retained/validated; partially
visible objects remain. Objects hidden by other objects cannot be exactly removed using these
tokens. The first mapping is exactly `vehicle.car -> car` and `human.pedestrian.* -> person`;
other categories are counted as unsupported. No near/far difficulty selection is applied.

## Run commands and explicit weights

Run from `backend`, with existing local CUDA weights and a genuinely trained E checkpoint:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m model_tests.unified_evaluation run --method all --bbox-mode both --limit 6 --warmup 3 --depth-interval 3 --detection-weights models/yolo26n.pt --detection-manifest models/yolo26n.json --depth-weights models/yolo26n-depth.pt --e-checkpoint models/experiments/object-distance-full-01/best.pt --output model_tests/outputs/nuscenes-comparison-01
```

`--method a|b|c|e|all` selects existing depth/geometry/temporal_depth/object_distance.
`--bbox-mode gt|predicted|both` creates independent passes. Each method/pass runs in a fresh
subprocess, serially, without competing task-owned GPU workers. Run after other GPU jobs finish.
Default device is `cuda:0`, mandatory for original A/B/C. CPU is an explicit option for E only.

The tool requires either `--limit N` (ordered prefix for smoke, potentially biased) or explicit
`--full`. It never starts full evaluation by default. After reviewing a smoke run, replace
`--limit 6` with `--full` and choose a new output directory to score all404 front keyframes.
The current `processed` directory already exists: do not rerun prepare into the same directory.

Detection weights must have a matching operator-provided manifest with their exact SHA256,
pinned Ultralytics build and COCO80 class order. Original native loading still validates it.
E's checkpoint must bind to the identical detector file and record a positive training epoch
and training step count. A random/default initialized checkpoint is rejected. To use teammate
weights, explicitly pass both their compatible detector file/manifest and an E checkpoint trained
against those bytes. An incompatible E is reported failed; it is never silently rebound or trained.

The same detector path is passed to all methods. The manifest records weight/checkpoint hashes,
architectures,classes,confidence,resolution,executor,precision and coordinate inverse policy.
E retains its checkpoint configuration, including any optional feature tuning. Matching base
weight SHA alone does not establish identical detector predictions after feature fine-tuning.

## Protocol, matching and coverage

Primary reference: `distance_gt_m = box.center[2]` after global-to-camera transformation.
This is **not** global z, Euclidean distance or nearest surface range. No supplemental surface
metric or test-GT scale calibration is performed. A/C's positive finite ROI-depth median and
E's learned KITTI reference-point z are scored against that explicit external reference while
their semantic differences remain recorded. Do not present these results as an aligned physical
surface-distance benchmark or proof of pretrained-data independence.

GT passes preserve every supplied box/class slot. Detection still executes every frame so the
end-to-end timing scope includes detection for all methods; GT mode evaluates distance behavior
without detector localization/missing-box effects. C's GT mode still associates supplied boxes
using real BoT-SORT; annotation/instance IDs never provide oracle track identities.

Predicted passes reuse `object_distance.metrics.match_objects`: same-class pairs with IoU>=0.5
by default, greedy highest-IoU-first with deterministic tie breaking, one-to-one assignment.
Change all methods' shared threshold with `--match-iou`. Unmatched predictions are counted;
unmatched GT and matched-but-invalid distances remain in the full GT denominator. This is not
official nuScenes detection AP or its distance-based detection matching protocol.

Metrics: MAE,RMSE,AbsRel use positive finite predictions only. Report alongside:

- Detection match rate = matched GT / all GT.
- Distance validity rate = valid matched distances / matched GT.
- Coverage = valid matched distances / all GT.
- Paired MAE/RMSE/AbsRel = identical annotation IDs with valid results in every requested method.

GT detection match rate is1 by construction, not detector recall. Paired coverage is reported
against the original shared GT universe. A failed requested method makes that paired comparison
unavailable rather than silently reducing its membership. Empty error/rate denominators are
`null`, never zero. Unsupported predictions remain in exports but cannot match another class.

GT-based bins are nonoverlapping **[0,1),[1,3),[3,5),[5,10),[10,20),[20,infinity)** meters.
Exact edges enter the upper bin. `distance_bins.csv` retains every bin, including near-distance
bins with no samples. `classes.csv` adds per-class errors and coverage.

## C sequences and B calibration

Choose C's interval with `--depth-interval 1|2|3|5`; interval1 runs depth on every frame. Frames
are grouped by `(scene_id,camera)` and sorted by original integer camera microsecond timestamps.
Each stream resets tracker/GMC,epoch,TemporalDepthCache and frame index. Source seconds and actual
delta seconds are exported; inference wall time is never used as a tracking timestamp.
BoT-SORT's original frame-based association behavior is retained without retuning for Mini.
Different cameras never form one temporal stream; prepare accepts `--cameras CAM_FRONT CAM_BACK`
for later explicit experiments.

C uses its original Last Valid Distance policy. New/unconfirmed tracks can have null distance
on skipped frames; missing IDs purge cache entries and new scene/camera epochs cannot inherit
old distance. `metrics.json -> results -> <bbox-mode> -> c -> temporal` records actual depth
calls/ratio,depth/non-depth frame counts,metrics and latency. First frame of every stream calls
depth, so short subsets can exceed the asymptotic1/interval call ratio. Warmup/cold calls are
reported separately. Skipped labeled keyframes have real annotated GT; unlabelled sweeps are
never assigned fabricated distance truth. These approximately2Hz keyframes do not establish
30FPS temporal tracking accuracy.

B uses each selected image's true calibrated **fy** and original image width with the existing
GeometryDistanceEstimator. Detection boxes are restored to that original image; there is no
second focal rescaling based on padded640 input. Existing height assumptions come from
`realtime/distance_config.py`, optionally overridden by `--distance-config models/distance-config.json`.
The config's foreign-camera focal default is explicitly replaced per frame; class heights are
never estimated from test GT. All used intrinsics,frame sizes and height assumptions are saved.

## Performance boundaries and outputs

All methods time in-memory RGB preprocessing through canonical distance output, with CUDA
synchronization. Disk decode,source hash checks,metric matching,output writing,render/network
and navigation/risk are excluded. Separate model load,cold first call,warmup and scored steady
statistics are retained. Cold Graph setup includes its original internal capture warmups; cold
probe is before external warmups. Scored sequence state is reset after both phases.

`detection_ms` and `distance_ms` retain underlying executor stage boundaries: native detection
includes Graph transfers/decode; E's existing detector stage excludes its input transfer and
distance stage includes decode/ROI assembly. `model_pipeline_ms` is the sum of those measured
stages, **not pure GPU kernel time**. Native `detection_forward_event_ms` measures graph replay
using the original CUDA events. These differences are recorded; compare whole-pipeline timings
under the common boundary, not unrelated older E/native benchmarks or stage-only speed ratios.

Mean/P50/P95 and aggregate FPS=1000*frames/sum(end_to_end_ms) are calculated independently for
steady,depth and non-depth frames. GPU allocation and reservation peaks include resident model
tensors; cold/load/warm allocation peak is separate from scored steady peak. They are not GPU-wide
driver VRAM measurements. Process RSS is polled every10ms from before worker model load and can
miss shorter spikes; Windows OS lifetime peak RSS is separate. Small smoke-run percentiles/FPS
are diagnostic observations, not a robust throughput claim.

Comparison is explicitly **non-strict**: A/B/C use fused FP32 CUDA Graph, E retains eager/AMP and
an unfused shared detector. Native scalar resize inverse differs from E's exact rounded x/y
inverse. All methods use original RGB without production's bounded JPEG80 ingress, and may
retain different E checkpoint sizes/thresholds. The manifest compares actual recorded conditions
instead of asserting equality. No automatic parameter/precision/preprocessing changes hide these
differences.

```text
<output>/
  dataset_summary.json       # Full converted dataset audit/counts
  run_manifest.json          # Actual selected IDs/counts,config,code/env/weight identity,status
  predictions/{a,b,c,e}.jsonl # Predicted passes; GT files use -gt suffix
  passes/<method>-<mode>/    # Options,worker log,result,observations,partial failure traceback
  metrics.json              # Overall/class/bin,C subsets,latency,memory,paired results
  comparison.csv            # Separate GT/predicted rows with status,coverage,errors,FPS
  paired_comparison.csv     # Common-valid-target errors for requested methods
  distance_bins.csv
  classes.csv
  latency.csv
  resources.csv
  report.md
```

Inspect results in PowerShell:

```powershell
$result = 'model_tests/outputs/nuscenes-comparison-01'
Import-Csv "$result/comparison.csv" | Format-Table
Import-Csv "$result/distance_bins.csv" | Format-Table
Import-Csv "$result/resources.csv" | Format-Table
Get-Content "$result/report.md"
```

Any failed method/pass preserves completed methods and partial per-frame artifacts, marks its
table row failed/null,invalidates its requested paired comparison and returns nonzero. Read its
`passes/<method>-<mode>/worker.log` and `failure.txt`; no retries or hidden fallback occur.
Source and weight hashes before/after are checked. JSONL/images/labels/checkpoints/output logs
are ignored by Git; commit only code/docs/config/tests and small aggregate evidence.

## Verification

With a local Django test key, use `manage.py test core.test_unified_evaluation`. Official SDK
fixture tests run only with the optional Devkit; mocked CUDA boundaries and synthetic predictions
are not real GPU accuracy measurements. The repository's full backend checks remain required:
`manage.py check`, `makemigrations --check --dry-run`, `manage.py test`, `ruff check .`.
Run vision-dependent tests in the isolated evaluation environment to include official fixtures.
GPU smoke scope,conditions and actual results belong in structured benchmark evidence, not in
claims inferred from passing unit tests.

## Fixed-standard six-frame coverage diagnosis

Run this separate read-only tool to investigate the existing comparison without changing its
models,weights,confidence threshold,IoU threshold or result files:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m model_tests.unified_evaluation.diagnostics --run model_tests/outputs/nuscenes-comparison-01 --output model_tests/outputs/nuscenes-diagnostics-03
```

Use a new output directory outside the original run. Native FP32 Graph and E eager/AMP detector
outputs are observed in separate serial GPU subprocesses. No depth model is loaded for diagnosis.
The tool captures existing e2e top300 rows before the original external confidence filter; these
are proposals,not300 confident detections or pre-head dense predictions. Replay classes,boxes and
scores must reproduce saved predictions within1e-3px/1e-5 score,or attribution aborts.

`diagnostic_capture.py` observes original components; `diagnostics.py` audits raw annotations,
geometry,matching and denominators; `diagnostic_render.py` publishes JSON/CSV and red-GT/green-YOLO
overlays. `core/test_distance_diagnostics.py` contains synthetic and official-SDK fixture checks.
Independent checks include official camera Boxes,homogeneous transforms,official2D projection,
image/intrinsic identity,letterbox round trips,cached GT identity,IoU arithmetic and maximum possible
matching cardinality at the same class/IoU threshold. The original greedy assignments remain used.

The additional `diagnostic-policy.json` declares a diagnostic-only subset: visibility token>=3,
at least50% retained projected bbox area,at least8px width and16px height at640 input. Those size
gates represent one/two stride8 feature cells; they are not a universal detection or visibility
standard. nuScenes visibility spans all six camera images,not a per-CAM_FRONT segmentation mask.
Every original GT remains in the original denominator and red overlays. Subsets reuse original
assignments without rematching. Height sensitivity0/8/16/32px is reported together; no profile is
chosen to maximize a score. Foreground overlap is a depth-ordered bbox proxy only,never a filter.

Outputs include `diagnostic.json`,`gt-stages.csv`,`detection-funnel.csv`,`coverage.csv`,
`target-diagnostics.csv`,`unmatched-reasons.csv`,`distributions.csv`,
`proposal-confidence-distributions.csv`,`visibility-size-sensitivity.csv`,`large-person-cases.csv`,
`checks.json`,`preservation.json`,`report.md`,`index.html`,six full overlays and a contact sheet
containing every original person observation. GT confidence is correctly labeled GT/NA.
All original result/source/weight fingerprints are checked before and after; no formal evaluation
metric is replaced by the additional visible/size-subset coverage.

## KITTI / nuScenes distance-domain diagnosis

Use the exact checkpoint from the nuScenes run to create a separate KITTI validation reference.
Existing historical smoke evaluations are rejected when checkpoint or split fingerprints differ:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m object_distance.evaluate --checkpoint models/experiments/object-distance-full-01/best.pt --processed model_tests/datasets/kitti/processed-80-20-seed42 --bbox-mode both --device cuda:0 --output model_tests/outputs/kitti-domain-reference-02
& $py -X utf8 -m model_tests.unified_evaluation.domain_diagnostics --kitti-evaluation model_tests/outputs/kitti-domain-reference-02 --nuscenes-run model_tests/outputs/nuscenes-comparison-full-01 --output model_tests/outputs/domain-diagnostics-02
```

The second command performs no inference or training. It audits both KITTI splits against raw
labels/calibrations/image dimensions and all selected nuScenes camera centers against the official
SDK. It exports original/input camera parameters,all accepted GT-z distributions,per-class/range/
camera/scene signed errors,training-range support and same-target GT/predicted-box comparisons.
`bias_m=prediction-GT`; negative bias means underestimation. Original matcher assignments and
missing-distance denominators remain intact. Input and weight fingerprints are checked again
after analysis. JSON/CSV,an English report and exportable PNG/SVG charts use a new output tree.

An explicitly labeled `focal_sensitivity` multiplies unchanged E outputs by the per-frame effective
input fy divided by KITTI TRAIN frame-median effective input fy. It fits no nuScenes GT,changes no
original prediction file and is not an implemented inference mode or a validated model score.
Class/z reweighting separately describes sample-mixture effects,with unsupported/sparse strata
reported. Neither analysis proves a unique cause or removes scene/visibility/box-convention shifts.
KITTI bottom-center conversion changes camera y,not z; it must not be treated as a meter-scale
z-offset without evidence. E Softplus has no hard80m output cap. The80/20 image split does not
establish sequence independence,and public KITTI testing data has no GT for this analysis.

Run numerical/guard tests with `manage.py test core.test_domain_diagnostics` in the evaluation
environment,then the backend checks listed above. No production A/B/C/E code is modified.
