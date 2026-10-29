# Walking-aid detection model (`yolo26n-walk`, default)

An 87-class fine-tune of the COCO `yolo26n`: the 80 COCO classes keep their ids and names, and seven
classes are added for blind-pedestrian navigation. The walk model is the default;
the original COCO model is selected explicitly with `PRS_DETECTION_MODEL=yolo26n`.

## Classes

| id | name | meaning |
|---|---|---|
| 0-79 | COCO names, unchanged order | same as `models/yolo26n.json` |
| 80 | `pole` | street-light / utility poles and building pillars |
| 81 | `bollard` | bollards, warning columns, spherical road blocks |
| 82 | `traffic_cone` | traffic cones |
| 83 | `stairs` | outdoor steps / stairs |
| 84-86 | `ped_red`, `ped_green`, `ped_off` | pedestrian signal colour (internal, see below) |

## Selecting the model

`PRS_DETECTION_MODEL` is read once at process start (`core.detection_model.selected_model`):

| value | weights / manifest |
|---|---|
| `yolo26n` | `models/yolo26n.pt` / `models/yolo26n.json` (original COCO, explicit) |
| unset, blank or `yolo26n-walk` | `models/yolo26n-walk.pt` / `models/yolo26n-walk.json` (default) |

Unknown values fail at startup; there is no fallback model. The selection applies to the local
inference worker (`integration.inference.services.load_detector`, local session hello and results)
and to the defaults of `model_tests.run_depth` (`--weights/--manifest` still override). Example from
`SystemCode/backend/`:

```bash
PRS_DETECTION_MODEL=yolo26n-walk python -X utf8 -m model_tests.run_depth --input <images> --output <new dir> --roi --max-edge 1280
```

The local PRF2 runtime freezes model/manifest selection before admission and uses it for health,
lazy loading and every session; the launcher verifies the selected weights. TensorRT FP32 depth
and explicit host ROI fusion are independent options described in [Depth deployment](DEPTH-EXPORT.md).
Their comparisons hold this detector's weights, all class thresholds and pedestrian-light rule
fixed. The optional model does not become the default when acceleration is selected.

**Not covered:** the HF ZeroGPU app, the HF video relay and the deployment tools still read
`models/yolo26n.json` and `models/inference-plan.json` directly. The FP16 / compiled / CUDA Graph
acceleration plan is qualified for the original weights only. Before the walk model can use that
path, the plan's COCO quality replicates must be re-measured for `yolo26n-walk` with the existing
acceleration tooling (see [ACCELERATION.md](ACCELERATION.md)); until then use the resident FP32 path.

## Changes that also apply to the default COCO model

- The browser client now honours `class_confidence_thresholds` from result metadata, accepting a
  per-class threshold only when it is below the 0.25 default. This fixes a pre-existing rejection:
  the backend already emits COCO traffic lights from 0.12 (`core.detector.CLASS_CONF`), which the
  client previously refused as invalid boxes. Default thresholds are unchanged
  (`{"traffic light": 0.12}`).
- Manifest checks are shared (`core.detection_model.validate_manifest`): `ResidentDetector` and the
  local protocol now also require unique class names and a 64-hex source hash for `yolo26n.json`,
  which the committed manifest already satisfies.
- Front-light marking prefers a `pedestrian light` when one exists; with the COCO model there is none,
  so selection is unchanged.

## Pedestrian-light colour (replaces the crop classifier in walk mode)

`core.detector.pedestrian_lights` runs after the ROI merge and before front-light marking:

1. Colour boxes (`ped_red`/`ped_green`/`ped_off`, decoded from 0.05 — the manifest's
   `class_confidence`, added to the thresholds in walk mode only) that overlap with IoU >= 0.3
   are one lamp.
2. Per lamp: `green` only if the highest green score >= **0.364** and is higher than the highest red
   score; otherwise `red` if any red box exists; otherwise `unknown`.
3. The lamp is emitted as one box labelled **`pedestrian light`** with `light_state`,
   `light_scores {red, green, off}`, a stable `class_id` (the `ped_red` id) and the highest-score
   geometry. Colour class names never leave the backend, so no consumer can announce "green"
   without the threshold.

Result metadata in walk mode adds `light_model`, `light_green_threshold`, and the
colour-class and `pedestrian light` entries in `class_confidence_thresholds`. The browser client
rejects raw colour labels, requires the `ped_red` class id on `pedestrian light` boxes, and admits a
per-class threshold only if it lowers the 0.25 default. `pedestrian light` boxes get the default
risk weight (5), like `traffic light`.

Threshold provenance (`models/yolo26n-walk.json` → `pedestrian_lights`): smallest value with
red->green <= 0.5 % on the 235 red-light images of ImVisible **validation** (1/235). On ImVisible
**test** (single frame, crosswalk pick, 226 red / ~226 green images):

| rule | red said green | green said green |
|---|---|---|
| walk model, 0.364 | 3/226 (1.3 %) | 0.42 |
| walk model, 0.5 (more conservative) | 1/226 | 0.31 |
| previous crop classifier (COCO model + `light_crop.pt`, 0.95) | 4/226 | 0.61 |

The threshold is a product decision recorded in the manifest; changing it requires updating the
manifest and this table. The crop classifier (`models/light_crop.pt`) is kept for comparison and
is not used in walk mode.

## Accuracy (fixed held-out data; full tables in the training outputs)

| metric | COCO yolo26n | yolo26n-walk |
|---|---|---|
| COCO val2017 mAP50-95 (Ultralytics val) | 0.399 | 0.393 |
| COCO val2017 large objects (pycocotools) | 0.586 | 0.569 |
| ExDark test (night) mAP50-95 | 0.325 | 0.351 |
| bollard AP50 (OOD test) | - | 0.75 |
| pole AP50 (Mendeley test) | - | 0.30 (recall ~0.18) |
| stairs AP50 (Mendeley / OOD test) | - | 0.34 / 0.37 |

Large-object changes on walking classes are small (mean -0.011 over 21 classes; largest:
suitcase -0.035, dog -0.027, car -0.022). Poles and stairs are often missed; bollards are reliable.

## Training (reproduction)

Scripts in `tools/walk_model/` run on Kaggle (2x T4, about 10 h): `prepare_data.py` builds the
87-class set (COCO 59k train subset + val2017 gate, Mendeley m68g3h7p87, Roboflow OOD v1 and
pedestrian-traffic-lights v16, ExDark, ImVisible train/val auto-boxes; scene-grouped splits with
near-duplicate grouping), `expand_head.py` grows the head 80 -> 87 while keeping COCO outputs
identical at step 0, `train.py` trains two rounds (backbone layers 0-9 frozen, feature distillation
from the original yolo26n, SGD lr 0.002/0.001, no hue augmentation; round 2 adds obstacle
pseudo-labels), `evaluate.py`, `soup_eval.py` and `large_eval.py` produce the numbers above.
`make_notebook.py` / `make_soup_notebook.py` build the Kaggle notebooks.

An unfrozen whole-model fine-tune scored better on new classes but lost COCO accuracy on near
objects (large-object mAP 0.586 -> 0.493), and weight interpolation broke the light colours, so
neither is used.

## Limits

- Pedestrian-light state is single-frame; no temporal confirmation.
- ImVisible (Shanghai) is the only colour benchmark; other cities' signals are unverified.
- The live path does not enable the ROI pass by default; distant lights need `roi=True`.
- Mendeley test images come from videos with generic frame names; its scores may be optimistic.
- Training data include ExDark (non-commercial academic use only) and AGPL-3.0 base weights.
- Risk weights for the new obstacles are defaults (`bollard`/`traffic_cone` 1, `stairs` 3), not
  user-tested.
