# Scheme E: YOLO26 shared features and object-distance regression

This is an explicit offline PyTorch experiment. Production A/B/C, their default `depth` mode,
CUDA Graph, tracking, TTC, risk and phone routes are unchanged. No production `object_distance`
mode is registered. E never loads YOLO26-Depth.

## Target, classes and architecture

The target `kitti_camera_reference_z_m` is KITTI `location_z`: the annotated 3D reference point's
forward camera coordinate in meters. It is **not** Euclidean range or nearest obstacle-surface
distance. Do not send these predictions directly to real safety alerts. `distance_m` remains the
compatible object field, with `distance_source="object_distance_head"` and explicit target metadata.

Only `Car -> car` and `Pedestrian -> person` are supervised. Cyclist's combined rider/cycle box
is not guessed into COCO person/bicycle. Van, Truck, Tram, Person_sitting, Misc and DontCare are
skipped with separate counts. All80 detection classes remain available; unsupported distances
are null. There is no geometry/depth fallback.

Ultralytics is pinned to the inspected **8.4.171**. The loaded model must match the inspected
YOLO26n graph routing, parameter/buffer shapes, COCO80 names/order and Detect/e2e structure.
`yolo26n_contract.json` records that ABI. Actual Detect inputs are sources `[16,19,22]`, channels
`[64,128,256]`, strides `[8,16,32]`. Code reads `Detect.f` and captures its actual inputs rather
than assuming older YOLO layer numbering.

One forward computes backbone, neck and unchanged `[B,300,6]` detections. A scoped pre-hook shares
the same feature tensors with the distance branch; no second YOLO forward occurs. Multiscale
ROI Align uses3x3, aligned=True, sampling_ratio=2. Flattened features have4032 values, plus six
normalized bbox position/size values and two class indicators. The default head is:

```text
4040 -> Linear128 -> ReLU -> Linear1 -> FP32 Softplus + epsilon
```

Head parameters:517,377; detector parameters:2,572,280. Initial meter-scale output bias is
configurable and defaults to20. Default training freezes all detector parameters and keeps BN
statistics unchanged; `no_grad` detector features still permit head gradients.

Input uses RGB, bilinear resizing,114 padding and FP32/255. Exact rounded resize x/y scales and
padding map original boxes into letterbox coordinates and back. ROI scale is measured from each
feature/input shape. Raw RGB is used without production JPEG80; this must be aligned for fair
comparisons.

## Modules

| File | Role |
|---|---|
| `config.py`, `config.json` | Validated parameters, paths, seeds and compute settings |
| `prepare.py` | Full raw audit, KITTI parsing/filtering and immutable splits |
| `data.py` | Dataset, variable-object batches and exact coordinate transforms |
| `model.py`, `yolo26n_contract.json` | Shared model, ABI checking and strict checkpoints |
| `train.py` | GT-ROI training, validation, loss and checkpoint artifacts |
| `metrics.py`, `evaluate.py` | Errors/coverage, GT versus predicted matching and external scoring |
| `predict.py` | Explicit image inference, synchronized cold/warm timing and memory |
| `../core/test_object_distance.py` | Numerical/file, real local model, gradient and checkpoint tests |

## Local data and conversion

Expected ignored layout:

```text
model_tests/datasets/kitti/
  raw/training/{image_2,label_2,calib}/
  raw/testing/{image_2,calib}/
  splits/{train.txt,val.txt,provenance.json}
  processed-80-20-seed42/{manifest.json,train.jsonl,val.jsonl,train.txt,val.txt}
  processed/  # Retained historical common split; not the training default.
```

The default uses all7,481 labeled images with a seeded80/20 image-level split: **5,985 training
and1,496 validation**, seed42. The validation count rounds7,481*0.2 to1,496; IDs are disjoint,
and their union covers all labeled images. The7,518 test images have no public labels and are
not used for training or validation. Image splitting does not establish sequence independence.

The historical common split (3,712 train/3,769 validation) and its experiment artifacts remain
unchanged in `processed`. Its maintained split files are:
[train](https://raw.githubusercontent.com/open-mmlab/OpenPCDet/master/data/kitti/ImageSets/train.txt),
[validation](https://raw.githubusercontent.com/open-mmlab/OpenPCDet/master/data/kitti/ImageSets/val.txt).
These community splits are not the official hidden-test evaluation, and sequence-level independence
is not established. See [KITTI](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d).

The historical ID files and their URL/SHA provenance are provisioned locally in `splits`.
The default seeded split does not require these metadata files.
The converter itself never downloads images, labels, split metadata or weights.

```powershell
$repoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location (Join-Path $repoRoot 'backend')
$py = '.\.venv\Scripts\python.exe'
$env:YOLO_OFFLINE = '1'
$env:YOLO_AUTOINSTALL = '0'

# Run once: the default80/20 directory already exists on this workspace.
& $py -X utf8 -m object_distance.prepare --verify-images

# Reproduce80/20 into a new directory (never overwrite an existing split):
& $py -X utf8 -m object_distance.prepare --seed 42 --val-fraction 0.2 --output model_tests/datasets/kitti/processed-80-20-copy

# Explicit historical policy, using the provisioned common split files:
& $py -X utf8 -m object_distance.prepare --split-dir model_tests/datasets/kitti/splits --output model_tests/datasets/kitti/processed-common-copy
```

Output must be new and beside `raw`. `--verify-images` checks every PNG's CRC, in addition to
exact image/label/calibration ID pairing and finite P2 projection validation. KITTI zero-based
text field13 is z; xyz, original/visible bbox, class, image ID/path/size, P2, truncation/occlusion
and source-label/calibration hashes are retained. Nonfinite/nonpositive-z, malformed, reversed,
outside boxes and invalid3D dimensions have distinct filter counts. Boxes are clipped to the
visible image. No hidden distance cap or difficulty selection is applied. Empty images remain.
Raw inputs are never overwritten; split hashes are checked by the loader.

Both `config.json` and Python defaults select `processed-80-20-seed42`. For a custom output,
pass its path through training's `--processed` option. `--random-split` remains supported as an
explicit synonym for the default seeded policy; it cannot be combined with `--split-dir`.
Old checkpoints retain their historical dataset configuration. Their metrics do not describe
the new20% validation set: train a fresh head on the new80% split before evaluating it, since
historical training images can overlap the new validation set. Existing reports are preserved.

## Windows training and low memory

Use the existing CUDA-capable `.venv`; a separately prepared `.venv-inference` also works.
Dependencies already exist in `requirements-inference.txt`: Torch2.8.0, Torchvision0.23.0,
Ultralytics8.4.171, NumPy and Pillow. No package upgrade or installation is performed implicitly.

```powershell
$repoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location (Join-Path $repoRoot 'backend')
$py = '.\.venv\Scripts\python.exe'
$env:YOLO_OFFLINE = '1'
$env:YOLO_AUTOINSTALL = '0'

# Full prepared splits; frozen detector, batch2, AMP,640px.
& $py -X utf8 -m object_distance.train --config object_distance/config.json --output models/experiments/object-distance-full-01

# Explicit diagnostic subset, not full-dataset training.
& $py -X utf8 -m object_distance.train --config object_distance/config.json --train-limit 64 --val-limit 32 --epochs 5 --batch-size 2 --device cuda:0 --output models/experiments/object-distance-smoke-02
```

Directories must be new. Config paths/output paths are backend-relative; absolute paths work.
CLI options override JSON. Available options include `--freeze-backbone/--no-freeze-backbone`,
`--unfreeze-last-layers`, `--learning-rate`, `--batch-size`, `--epochs`, `--image-size`, `--device`,
`--seed`, `--amp/--no-amp`, `--workers`, `--processed`, `--max-steps` and explicit subset limits.
Limits select the first N fixed-split IDs and are recorded as potentially biased smoke subsets.
Training shuffles epoch order using a dedicated seeded generator; validation does not shuffle.

Default loss is Smooth L1, beta1 meter; optimizer is AdamW,lr0.001,weight_decay0.01. Only parameters
with requires_grad=True enter it. CUDA AMP/GradScaler defaults on; Windows workers default0.
No augmentation, ROI subsampling or implicit label/metric/seed changes are introduced.

Artifacts: `history.jsonl` records every step's loss/object count; `loss.csv` records epoch losses
and GT validation MAE/RMSE; `run.json` records settings, fingerprints, initial/final validation,
parameter counts, detector freeze audit, elapsed time and peak PyTorch allocated GPU memory.
`best.pt` selects minimum validation GT MAE; `last.pt` stores the latest epoch. Both save the
complete detector/head state, base weight identity, architecture, config and optimizer snapshot.

```powershell
Get-Content models/experiments/object-distance-full-01/history.jsonl -Tail 10 -Wait
Get-Content models/experiments/object-distance-full-01/loss.csv
```

Default frozen detector parameters and BN buffers are audited by full tensor fingerprints.
Head gradients must be finite/nonzero. Nonfinite loss and OOM fail with partial reports retained;
there is no automatic retry. For8GB or smaller GPUs retain freezing/AMP and start batch2. If OOM
actually occurs, start a new recorded run with `--batch-size 1`. Reducing edge to512/416 changes
conditions and must be recorded separately. Peak memory means `max_memory_allocated`, not total
driver/display VRAM.

Iteration count and actual optimizer-update count are distinct: AMP can skip an overflowing
gradient update while adjusting its scale. `history.jsonl` records update decisions/scales;
`run.json` separately reports optimizer updates and AMP overflow skips.

Optional partial distance-supervised tuning:

```powershell
& $py -X utf8 -m object_distance.train --config object_distance/config.json --init-checkpoint models/experiments/object-distance-full-01/best.pt --unfreeze-last-layers 2 --learning-rate 0.0001 --batch-size 1 --output models/experiments/object-distance-partial-01
```

Unfreezing counts final feature modules before Detect. Detect stays frozen/eval, but changing
features can alter detection quality: this option has distance loss, not YOLO detection loss.
Detection quality therefore needs separate re-evaluation. `freeze_backbone=false` allows all
feature parameters and is not the low-memory default. Optional CUDA ROI backward may remain
nondeterministic despite fixed seeds. Init-checkpoint starts a new optimizer, not exact resume.

## Teammate weights

Change `detection_weights` in JSON or pass a compatible local YOLO26n checkpoint:

```powershell
& $py -X utf8 -m object_distance.train --config object_distance/config.json --detection-weights D:\team\best.pt --output models/experiments/object-distance-teammate-01
```

This starts a fresh head. Changed architecture, COCO80 ordering or output ABI fails explicitly.
Changed detector bytes require retraining; an old E checkpoint is not silently reused. Loading
uses strict state keys/shapes and the original base SHA. `--detection-weights` at prediction may
relocate identical bytes. Only use operator-trusted local Ultralytics `.pt` detectors.

## Evaluation, inference and timing

```powershell
& $py -X utf8 -m object_distance.evaluate --checkpoint models/experiments/object-distance-full-01/best.pt --bbox-mode both --device cuda:0 --output model_tests/outputs/object-distance-eval-full-01

# Add --limit 128 for an explicitly reported small validation subset.
& $py -X utf8 -m object_distance.predict --checkpoint models/experiments/object-distance-full-01/best.pt --input model_tests/datasets/kitti/raw/training/image_2/000001.png --device cuda:0 --warmup 3 --repeat 30 --output model_tests/outputs/object-distance-image-01.json
```

GT and predicted modes have separate passes/files. GT ROIs measure the head itself; predicted
ROIs use real detections/confidence0.25, then same-class, highest-IoU-first one-to-one GT matching
at0.5. Missed GT detections enter missing-prediction coverage. GT scores are never represented as
full-system accuracy. Reports include MAE,RMSE,AbsRel, valid/GT counts, missing ratio, per-class
results and GT-z bins `[0,10),[10,20),[20,40),[40,80),[80,infinity)` meters. Errors are conditional
on valid predictions; coverage uses every accepted GT. Empty-group errors are null. Unmatched
predictions may include unsupported classes; this is not official KITTI detection AP or its
DontCare ignore protocol. Per-image JSONL exports retain the same schema for external scoring.

CUDA timing synchronizes the selected device. First-call probe, warmups and stable mean/P50/P95
are separate; aggregate FPS uses total stable processing time. Parameter counts and peak allocated
GPU memory are separate. First-call-for-mode is not process-cold after another mode warmed the
model. Evaluation end-to-end includes PNG decode/preparation through distance serialization,
excluding metric matching/output I/O/JPEG rendering/network. Image prediction starts with in-memory
RGB preprocessing, excluding file decode. Checkpoint loading is separately timed by image inference.
Run GPU benchmarks without competing workloads; repeated-image timing is not independent accuracy.

`distance_head_ms` times ROI validation/Align and MLP through synchronized GPU completion.
`distance_ms` additionally includes detection decoding, ROI construction/transfers and Python
distance output. Both are reported, avoiding attribution of all postprocessing to the neural head.

## KITTI final in-domain validation report

Keep `models/experiments/object-distance-full-01/best.pt` and the original
`model_tests/datasets/kitti/processed-80-20-seed42` validation split. No retraining or
checkpoint conversion is required. From backend run:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m object_distance.evaluate `
  --checkpoint models/experiments/object-distance-full-01/best.pt `
  --processed model_tests/datasets/kitti/processed-80-20-seed42 `
  --split val --bbox-mode both --device cuda:0 `
  --output model_tests/outputs/kitti-e-final-weighted-01
```

Use a new output directory for every invocation. Existing metrics and prediction JSONL files
are preserved. Each mode additionally produces `gt_report.md` or `predicted_report.md`;
its JSON gains `distance_report` containing four GT-distance bands `[0,10)`, `[10,20)`,
`[20,40)`, `[40,infinity)` meters, their MAE, dimensionless AbsRel and coverage.
Weighted MAE is `0.4*MAE1 + 0.3*MAE2 + 0.2*MAE3 + 0.1*MAE4` in meters.
The last band includes targets at and beyond 80 meters. Errors use valid predictions only;
missing targets remain in each band's coverage denominator. If any band has no valid prediction,
Weighted MAE is null, with no weight renormalization or zero substitution. The original overall
MAE and configurable distance bins remain unchanged. Training loss and checkpoint selection
remain unchanged. Report this as in-domain validation performance: this same validation split
selected the best checkpoint, so it is not an independent held-out final test.

## A/B/C/E comparison interface

External adapters can emit JSONL records of this form:

```json
{"image_id":"000001","distance_target":"kitti_camera_reference_z_m","objects":[{"class_name":"car","bbox":[100,100,200,200],"distance_m":12.0}]}
```

```powershell
& $py -X utf8 -m object_distance.evaluate --predictions model_tests/outputs/calibrated-a.jsonl --processed model_tests/datasets/kitti/processed-80-20-seed42 --output model_tests/outputs/comparison-a-01
```

The interface checks IDs, target semantics and split identity, with identical class/IoU scoring.
Merely renaming a surface-depth median or geometry estimate does not make it KITTI reference z.
First define/calibrate equivalent targets, then align inputs, preprocessing/JPEG, detector weights,
precision/boxes, thresholds, classes and split. C additionally needs real ordered source sequences;
independent KITTI object images cannot validate temporal tracking. Compare identical warmups,
batch/hardware and timing boundaries. E eager execution and production A/B/C CUDA Graph currently
differ. No acceleration ratio or A/B/C/E accuracy ranking is claimed.

## Checks and artifact hygiene

From backend with the configured Django test key run `python manage.py check`,
`python manage.py makemigrations --check --dry-run`, `python manage.py test`, and `ruff check .`.
Frontend: `node --test tests/*.test.mjs`. Vision tests skip explicitly without local inference
dependencies/weights; synthetic or CPU tests do not establish actual GPU training accuracy.

Existing ignores cover `model_tests/datasets/kitti/`, `models/experiments/`, generated
`model_tests/outputs/` and logs. Do not commit PNGs, labels, calibration, split IDs, prepared JSONL,
per-object predictions or checkpoints. Only code/config/docs and small aggregate experiment
evidence should be tracked. Verify `git check-ignore` and `git status` before staging.

## Class-agnostic KITTI experiment

Use `config.kitti-agnostic.json` to train a new unified KITTI distance head. It reuses the
existing class-agnostic implementation: shared YOLO features, ROI Align and six bbox geometry
values enter the MLP, without class indicators or a category-based distance gate. Every valid
YOLO detection ROI is eligible for distance regression; detection labels and output fields stay
unchanged. Invalid geometry or nonfinite predictions still produce unavailable distance.

The existing KITTI 80/20 split and camera-reference z labels are preserved. `supported_classes`
still lists car/person for dataset metadata and per-class evaluation; in this mode it does not
restrict predicted-box distance inference. This experiment does not add training samples for
other categories, so availability on unseen categories does not establish accuracy.

Start a fresh head, with frozen YOLO, using a new output directory:

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m object_distance.train `
  --config object_distance/config.kitti-agnostic.json `
  --output models/experiments/object-distance-kitti-agnostic-01
```

Defaults match the legacy KITTI config except for the explicit class-agnostic flag: batch 2,
10 epochs, seed 42 and 640-pixel input. Do not use the old class-conditioned head as an
initialization checkpoint: its input dimensions differ. Training is explicit, never automatic.
The legacy `config.json`, `object-distance-full-01` checkpoints and all SANPO configs/checkpoints
remain independent. Loading a checkpoint selects its saved head architecture; changing a JSON
file cannot turn the old trained head into a trained class-agnostic head.

After training, evaluate against the same KITTI validation split:

```powershell
& .\.venv-evaluation\Scripts\python.exe -X utf8 -m object_distance.evaluate `
  --checkpoint models/experiments/object-distance-kitti-agnostic-01/best.pt `
  --processed model_tests/datasets/kitti/processed-80-20-seed42 `
  --split val --bbox-mode both --device cuda:0 `
  --output model_tests/outputs/kitti-e-agnostic-01
```

## SANPO manual pedestrian training

Run `python -m object_distance.prepare_sanpo` after the complete SANPO audit reaches
`verified`. Then run `python -m object_distance.train --config object_distance/config.sanpo.json
--output models/experiments/object-distance-sanpo-person-01` (one command line).
The converter preserves the 8:1:1 session split, uses human masks only, and extracts
pedestrian boxes and median visible-surface metric depth from the verified audit.
Generic vehicles are excluded rather than relabeled as cars. Empty frames remain.
The two-slot head architecture is preserved; SANPO predicted inference provides distance
only for person. Car, stairs and curb distance are not supervised by this experiment.

SANPO uses `sanpo_visible_surface_median_m`; KITTI retains `kitti_camera_reference_z_m`.
Checkpoints and loaders check this definition. Start a fresh distance head; KITTI checkpoint
initialization is rejected for SANPO. Default SANPO settings are frozen YOLO, AMP, batch 8,
640 pixels and 10 epochs. Test sessions are not used for checkpoint selection.
Training does not begin automatically when downloading finishes. Source data and old runs
are preserved. Use a new output directory for every run.

## Class-agnostic SANPO experiment

`python -m object_distance.sanpo_instances` creates independent manual-instance manifests.
The configured object taxonomy excludes semantic regions and panoptic crosswalk regions.
Instances with ID zero or no finite positive depth are counted as excluded. No bbox-size or
range cutoff is applied. RGB/mask2208x1242 and depth1280x720 are aligned by nearest mask
sampling on the native depth grid. Labels use median visible-surface meters, not center-z.

`config.sanpo-agnostic.json` uses one4038-input MLP (4032 ROI features +6 geometry values),
without class features or category-dependent ROI validity. Category IDs remain solely for
statistics. `class_agnostic=false` preserves old checkpoint compatibility and defaults.
Train from scratch with frozen YOLO and batch8; no old head checkpoint is used.

Run `python -m object_distance.train --config object_distance/config.sanpo-agnostic.json
--output models/experiments/object-distance-sanpo-agnostic-01` on one line.
For comparison run `python -m model_tests.unified_evaluation.sanpo --method a
--checkpoint models/experiments/object-distance-sanpo-agnostic-01/best.pt --output OUTPUT`
with a fresh OUTPUT per method (a/b/c/e). All430 Test frames feed inference in sequence;
only72 manual frames are scored. C uses interval3 and actual BoT-SORT IDs. Shared adapter,
IoU matcher, coverage/error metrics and timing implementations are reused. Prediction class
mapping is explicitly saved; unmapped GT objects remain missing, never filtered from coverage.
Every method uses the same surface-median reference; original A/C bbox fusion and B size-based
formula remain unchanged, so estimator semantics still differ. No test-based fitting is done.
