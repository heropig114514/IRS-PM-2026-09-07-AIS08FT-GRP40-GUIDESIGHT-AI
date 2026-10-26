# KITTI A/B/C/E validation comparison

This entry point reuses the existing A/B/C/E adapters and the original KITTI E checkpoint.
It reads `processed-80-20-seed42/val.jsonl`, verifies its fingerprint against the original
training `run.json`, and never trains, changes thresholds, alters labels or overwrites old outputs.

From backend, with the evaluation environment installed:

```powershell
$py = '.\.venv-evaluation\Scripts\python.exe'
& $py -X utf8 -m model_tests.unified_evaluation.kitti `
  --method all --bbox-mode both `
  --processed model_tests/datasets/kitti/processed-80-20-seed42 `
  --e-checkpoint models/experiments/object-distance-full-01/best.pt `
  --device cuda:0 --warmup 3 --depth-interval 3 `
  --output model_tests/outputs/kitti-abce-final-01
```

The default weights are `models/yolo26n.pt`, `models/yolo26n.json` and
`models/yolo26n-depth.pt`. Each method/box pass runs in a fresh serial subprocess, releasing GPU
memory before the next pass. There are eight passes with `all` and `both`. Add `--limit 3` and
choose a different output directory for a small smoke test. Omit limits for the full validation.
`--bbox-mode predicted` runs only four deployment-style passes; `gt` runs supplied-box regression.

## Outputs

- `comparison.md`: readable overall and per-distance tables, with experiment limitations.
- `comparison.csv`: method/box mode, MAE, AbsRel, Weighted MAE, coverage, stage latency and FPS.
- `distance_bands.csv`: four bands per method/box mode with counts, MAE, AbsRel and coverage.
- `paired_comparison.csv`: same metrics on common valid objects, when all requested methods succeed.
- `metrics.json`: full results, dataset/checkpoint identities, configuration and paired denominator.
- `passes/a-gt/`, `a-predicted/`, ..., `e-predicted/`: options, worker log, result, individual report,
  predictions and one observation per GT. Failed workers retain `failure.txt` and partial artifacts.

Tables update after every pass; only `status=complete` means every requested pass succeeded.
Failures return a nonzero process exit status, remain visible in tables and make paired results
unavailable. Existing outputs are rejected, so use a new name for every invocation.

## Metric and timing protocol

Reference labels remain KITTI camera-z. The four half-open bands are `[0,10)`, `[10,20)`,
`[20,40)`, `[40,infinity)` meters; Weighted MAE is `0.4*MAE1+0.3*MAE2+0.2*MAE3+0.1*MAE4`.
AbsRel and coverage are fractions. Empty or wholly missed bands produce null Weighted MAE,
never zero error or renormalized weights. Matching is the original same-class IoU matcher at 0.5.
Main errors use each method's valid predictions and retain all GT in coverage; paired errors
additionally use identical valid targets across the requested methods. Paired coverage is the
common-target count divided by full GT in `metrics.json`; per-method paired rows naturally have
100% validity within that restricted subset. Do not interpret that as whole-dataset coverage.

B uses each image's actual P2 fy and original image width, with existing category height priors.
A/C preserve bbox depth medians, which need not equal KITTI reference-point z. No label-fitted
scale or calibration is introduced. A/B/C retain fused FP32 CUDA Graph execution; E retains
its existing eager precision and coordinate handling. These are comparisons of existing pipelines,
not a controlled architecture-only benchmark. Both GT and predicted passes include detector compute.

Latency excludes image decoding and report I/O for all methods. It includes per-image state reset,
RGB preprocessing and synchronized inference through canonical results. Cold and warmup calls
are separate. This timing scope differs from `object_distance.evaluate`, which includes decoding.

## Temporal and validation limitations

KITTI object validation images do not provide a verified continuous stream. C therefore resets
tracking/cache/schedule before every image and runs depth on every frame. Its rows are independent-
image controls; depth ratio is 100%, regardless of `--depth-interval`. Non-depth frame latency is
unavailable. Never concatenate filenames into a synthetic stream to claim temporal speedups.
Actual low-frequency C evaluation requires a verified continuous sequence and corresponding labels.

The same validation split selected E's best checkpoint. Report these as KITTI in-domain validation
results, not independent final test accuracy or proof of pedestrian-navigation generalization.
