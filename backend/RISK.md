# TTC and Interpretable Risk Scoring

Pipeline: original detection → BoT-SORT → original metric-depth/ROI fusion → original TrackHistory motion estimate → `realtime.risk.enrich_object_risk()` → annotation/PRS1. The added stage is stateless and adds no second track history. It does not change YOLO, depth, BoT-SORT, EMA, regression, confidence, or NMS. No model, dependency, TTS, or navigation feature is added.

## TTC

`estimate_ttc(smoothed_distance_m, relative_speed_mps, motion_state)` returns seconds or `None`. TTC is calculated as `smoothed_distance_m / abs(speed)` only when distance is finite and positive, speed is finite and negative, state is `approaching`, and closing speed satisfies `abs(speed) >= min_closing_speed_mps`. The default minimum closing speed is 0.20 m/s, inclusive. `None`, NaN, Inf, booleans, strings, non-positive distance, other motion states, and zero or positive speed do not produce finite TTC.

TTC values beyond the default 10-second evaluation horizon are **retained unchanged**, while the TTC component is 0 and the reasons include `ttc_beyond_horizon`; other distance/motion/direction/class risk is not forcibly lowered. If the current raw depth is invalid, smoothed depth is absent, or the ID is unconfirmed, output `ttc_sec: null`, `risk_score: null`, `risk_level: unknown`, and no alert. A brief depth gap retains the existing TrackHistory motion estimate; scoring neither resets history nor treats a cached distance as a current measurement.

## Rules and All Defaults

`RiskConfig` is defined centrally in `realtime/risk.py`. Configuration is read once during startup/CLI initialization. Score: `clamp(distance + motion + TTC + direction + class, 0, 100)`. Each value contributes only its matching band; multiple distance/TTC bands are not accumulated. The score is a sum of rule weights, **not an accident probability**. `RiskAssessment.components` retains the five components for Python debugging; the protocol emits `risk_reasons` instead of including a component dictionary for every box. Identical inputs/configuration produce identical results.

| Parameter/component | Default thresholds and weights |
|---|---|
| `min_closing_speed_mps` | 0.20 m/s; TTC/approach bonus applies at or above this closing speed |
| `max_ttc_sec` | 10.0 seconds; TTC component is 0 above this value |
| `distance_bands` | (0,1.5]m→40；(1.5,3]→25；(3,5]→15；(5,10]→5；>10→0 |
| `ttc_bands` | (0,1.5]s→40；(1.5,3]→30；(3,5]→15；(5,8]→5；>8→0 |
| `motion_weights` | approaching→15；moving_away→−10；stationary→0；unknown→0 |
| `closing_speed_bands` | +5 at closing speed ≥1 m/s; +10 at ≥2 m/s; use only the highest matching band |
| `direction_weights` | front→10；front_left/front_right→7；left/right→3；behind/unknown→0 |
| `class_weights` | truck/bus→18；car/motorcycle→15；bicycle/scooter→12 |
| Medium weights from the same class table | person/animal/bird/cat/dog/horse/sheep/cow/elephant/bear/zebra/giraffe → 5 |
| Low weights from the same class table | bench/chair/stop sign/fire hydrant/pole/sign → 1 |
| `default_class_weight` | 5 for unlisted classes; class alone never triggers an alert |
| `risk_level_thresholds` | medium ≥25; high ≥50; critical ≥75; other known scores are low |
| `alert_priorities` | low/unknown → 0; medium → 2; high → 3; critical → 4; 1 is reserved for normal |
| `medium_alert_enabled` | true; medium alerts require valid closing speed, smoothed distance ≤3 m, and a front region |
| `medium_alert_max_distance_m` | 3.0 m, inclusive |
| `medium_alert_directions` | front、front_left、front_right |
| `front_left_fraction` / `front_right_fraction` | 1/3 and 2/3; a box center between or on the boundaries is front |

Low/unknown does not alert. High/critical is eligible for an alert, subject to the vehicle filter below. Medium priority is also 0 unless its alert condition is met. During motion-classification hysteresis, a label may temporarily disagree with the current speed sign: `approaching` receives its bonus only with valid negative speed, and `moving_away` receives a reduction only with valid positive speed. Unknown motion earns no motion-only bonus, though short distance can still be high risk. A supplied TTC that does not satisfy the current formula or eligibility contributes nothing to the TTC component and cannot bypass the vehicle filter; an inconsistent supplied TTC adds `ttc_inconsistent`.

Reuse an existing valid `direction` when available; otherwise derive `front_left`, `front`, or `front_right` from the original box center's horizontal position. Clamp the box to frame boundaries first; invalid geometry yields `unknown`. Direction is **relative to the camera view**; walking direction and path intersection are not known. Box size is used only for direction, not distance.

## Vehicle Alert Eligibility (`rules-v2`)

An otherwise eligible alert for `car`, `truck`, `bus`, `motorcycle`, `bicycle`, or `scooter` must pass one of two gates: the vehicle is within its region's distance window, **or** its validated approaching TTC is within the urgent window. Limits are inclusive. The filter only suppresses existing alert requests; it never creates an alert, changes the five score components, lowers the displayed risk level, or removes a detection. Other classes, including people, obstacles, and traffic lights, keep their existing behavior.

| Configuration | Default | Meaning |
|---|---|---|
| `vehicle_alert_filter_enabled` | `true` | Apply the vehicle eligibility gate after original scoring/alert selection |
| `vehicle_front_distance_m` | 10 m | Distance window for `front`; also used for `unknown`/`behind` without known lateral evidence |
| `vehicle_side_distance_m` | 3 m | Distance window for `front_left`, `front_right`, `left`, and `right` |
| `vehicle_front_ttc_sec` | 3 s | Preserve urgent approaching vehicles beyond the frontal distance window |
| `vehicle_side_ttc_sec` | 2 s | Preserve urgent approaching vehicles beyond the lateral distance window |

The 10 m frontal window retains the existing outer distance-scoring band. The 3 m lateral window prioritizes nearby vehicles while reducing distant adjacent-road notifications. The 3 s/2 s TTC exceptions retain rapid approaches without using a blanket maximum distance. These are initial tunable heuristics, not calibrated collision thresholds. Side limits must not exceed frontal limits, and the frontal TTC limit must fit within `max_ttc_sec`. All four limits must be positive finite numbers; the enable flag must be boolean.

Suppressed results retain their original score/level and TTC, set `alert_required=false` and `alert_priority=0`, and append `vehicle_outside_front_alert_range` or `vehicle_outside_side_alert_range` to `risk_reasons`. The frontend uses the alert fields for speech/vibration eligibility. A high/critical annotation can therefore remain visible without automatic output; suppression is not a safety classification. A missing, inconsistent, nonapproaching, or out-of-horizon TTC never enables the urgent exception. Close targets still use original alert eligibility even when motion is unavailable.

Synthetic examples with unchanged valid input measurements:

| Input vehicle | TTC | Previous alert | Filtered alert |
|---|---|---|---|
| Front, 20 m, closing at 2 m/s | 10 s | Yes | No |
| Front-left, 20 m, closing at 5 m/s | 4 s | Yes | No |
| Front-right, 8 m, closing at 2 m/s | 4 s | Yes | No |
| Front, 20 m, closing at 10 m/s | 2 s | Yes | Yes |
| Front-right, 8 m, closing at 4 m/s | 2 s | Yes | Yes |
| Front-left, 3 m, closing at 0.5 m/s | 6 s | Yes | Yes |

For a controlled comparison with the previous policy, pass `RiskConfig(vehicle_alert_filter_enabled=False)` or load `{"vehicle_alert_filter_enabled": false}` through the existing JSON profile reader. Scores, distance estimates, motion, TTC, and upstream model settings remain identical between paired inputs. This disables only the new gate; it still reports the current `rules-v2` implementation identity. Historical output files are not rewritten. Existing detection/distance experiments do not gain a new detector pass or scoring metric.

This filter cannot distinguish opposing lanes, sidewalks, or paths that intersect the user. A vehicle in the center of the image can still be in another lane, and rotating the phone changes the camera-relative region. Monocular distance/motion errors can still produce nuisance alerts or delay useful ones. Real video replay and field calibration are needed to measure the tradeoff; synthetic regression tests do not establish a real-world false-alarm reduction percentage.

## Configuration and Use

Call `RiskConfig(...)` directly or use JSON. The following partial-override example is not an experiment configuration applied automatically:

```json
{
  "min_closing_speed_mps": 0.20,
  "max_ttc_sec": 10.0,
  "distance_bands": [[1.5, 40], [3.0, 25], [5.0, 15], [10.0, 5]],
  "ttc_bands": [[1.5, 40], [3.0, 30], [5.0, 15], [8.0, 5]],
  "class_weights": {"bicycle": 12},
  "direction_weights": {"front": 10},
  "motion_weights": {"approaching": 15},
  "risk_level_thresholds": [25, 50, 75],
  "medium_alert_enabled": true
}
```

JSON name maps (`class_weights/direction_weights/motion_weights/alert_priorities`) override defaults by key; unspecified keys retain their defaults. Band arrays, level thresholds, and direction lists replace their entire defaults. Invalid fields/values/order/files fail explicitly; they are not repaired, retried, or replaced with a default file. The maximum TTC scoring band must not exceed the evaluation horizon.

Run the following commands from `backend`:

```powershell
# Local rule/fusion/protocol tests; configure DJANGO_SECRET_KEY as described in README.
.\.venv\Scripts\python.exe -X utf8 manage.py test model_tests.test_risk model_tests.test_tracking

# Real dual-model inference, tracking, TTC, and risk; output must use a new directory.
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --repeat 6 --output model_tests/outputs/risk-run-1

# Optional: save the configuration above or provide explicit parameters of your own.
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --input model_tests/datasets/sequence-frames --risk-config model_tests/risk.json --output model_tests/outputs/risk-sequence-1
```

Repeated static images can test fields/IDs/stationarity only; they do not produce credible approach TTC. The CLI uses local inference-start time. Directory-image timing differs from original video time and cannot establish real-video speed/TTC. Timestamped callers can pass increasing source times for the same sequence through `detector.predict(jpeg, timestamp=source_seconds)`; this work did not change the capture protocol. The original real-time tracking path computes risk automatically; independent photo tests disable tracking and risk by default. The CLI writes the full effective configuration to `run.json.settings.risk` and stores fields in `results.jsonl.metadata.boxes`.

HF can set `PRS_RISK_CONFIG=models/risk.json` (path relative to the Space root); otherwise defaults are used. Users must provide custom files themselves. Source packaging does not upload user configuration or models; this change updates only the source allowlist and was not released. The HF root process loads immutable configuration and passes it to the detector within an allocation; actual tracks remain owned by the existing ObjectTracker.

## Output Examples

All distances below are valid smoothed meters passed to the rules, and all directions are `front`; actual output also retains the original box/class/confidence fields.

| Object | Distance/motion/speed | TTC | Score/level | Alert/priority |
|---|---|---|---|---|
| person | 20m / stationary / 0m/s | null | 15 / low | false / 0 |
| car | 5m / moving_away / +1m/s | null | 30 / medium | false / 0 |
| person | 0.8m / stationary / 0m/s | null | 55 / high | true / 3 |
| bicycle | 2m / approaching / −1m/s | 2s | 97 / critical | true / 4 |

```json
{
  "track_id": 7, "class_name": "bicycle", "confidence": 0.93,
  "bbox": [120, 80, 360, 620], "distance_m": 2.1,
  "smoothed_distance_m": 2.2, "relative_speed_mps": -1.2,
  "motion_state": "approaching", "direction": "front", "ttc_sec": 1.8333333333333335,
  "risk_score": 97, "risk_level": "critical", "alert_priority": 4, "alert_required": true,
  "risk_reasons": ["distance_m_le_3", "approaching", "closing_mps_ge_1", "ttc_s_le_3", "direction_front", "class_bicycle"]
}
```

High/critical label: `ID 7 | bicycle | 2.1m | approaching | TTC 1.8s | CRITICAL`. Short-distance stationary high risk displays `TTC N/A`. Low/medium/unknown retain the original ordinary label; no new panel or announcement is added. Current frame metadata reports `risk_model: rules-v2` and a separate `risk_ms`, which excludes model, tracking, rendering, and transport time. Existing fields remain, and the frontend accepts legacy `rules-v1` and packets without risk fields. Only v2 permits a suppressed high/critical vehicle with an explicit recognized suppression reason and zero alert priority. Local hello and result versions must match. Update/restart both local backend processes and reload the frontend together; an old cached client rejects v2 explicitly. New risk fields must be provided as a complete group; invalid results are rejected explicitly. Metadata remains limited to 256 KiB and JPEG to 1 MiB; tests cover the full 300-target case including suppression reasons.

## Checks and Limitations

Tests cover valid/invalid TTC inputs, minimum speed, the 10-second horizon, distance/TTC/score boundaries, risk scenarios A–G, speed bonuses, optional medium alerts, configuration errors, box direction, preservation of existing fields, real TrackHistory fusion, integration with the real BoT-SORT `predict`, missing-depth cache behavior, annotation, the 300-target protocol, and worker/CLI configuration reuse. This recorded run passed 142 backend tests (including 18 new risk tests), 70 frontend tests (including 3 new risk-protocol tests), `manage.py check`, `makemigrations --check --dry-run`, `ruff check .`, and `git diff --check`. No migrations were created, and real-SDK tracking tests were not skipped. Django checks used a temporary test key in the current process; no configuration file was changed. Comments and declaration/variable indexes in changed code were manually reviewed; no documentation checker was introduced.

The real local RTX 3060 dual-model run in `model_tests/outputs/risk-smoke-20261010/` processed 6 frames with 5 targets per frame. IDs remained stable; original detection hashes/boxes and raw depth exactly matched `outputs/bus-run-2`. All five final targets were stationary, all TTC values were null, risk scores were 27/33/30/27/27 (medium), and none generated alerts. For five targets, `risk_ms` ranged from 0.146 to 0.452 ms with a 0.233 ms median. The new stage processes existing CPU scalars only; it performs no GPU inference, CPU/GPU transfer, or model loading. Annotation/encoding/JSON transport overhead is excluded from that timing.

On the same machine, a pure-CPU rules microbenchmark ran 1,000 iterations per group: 100 targets averaged 0.844 ms/frame and 300 targets averaged 4.165 ms/frame. Inputs were synthetic with valid approaching state; there was no model, tracking, or rendering. These are local measurements and do not represent HF, end-to-end phone latency, or sustained FPS. Evidence:
[local-validation.json](benchmarks/2026-10-10/risk/local-validation.json)。
Real handheld video, external HF/PRS operation, and ground-truth accuracy for distance/speed/TTC/risk were not verified; this work was not committed or deployed.

Monocular metric depth has error; depth is measured along the camera axis and is not Euclidean radial distance for every object. Camera motion also changes measured distance; BoT-SORT GMC compensates association only and does not convert speed into world velocity. TTC assumes constant current closing speed and represents extrapolated time to zero depth; it does not prove an object will intersect the user's path. EMA/history regression/hysteresis introduce delay, and the current real-time path uses processing-start time rather than hardware capture time. Risk rules have not been calibrated with real ground-truth scenes. `unknown` due to invalid input does not mean safe. This stage adds no cross-frame risk hysteresis, alert throttling, occlusion prediction, or TTS.

Added in this work: `realtime/risk.py`, `model_tests/test_risk.py`, and `RISK.md`.
Modified in this work: `realtime/detector.py`, `core/detector.py`, `zerogpu/live_worker.py`, `zerogpu/app.py`, `tools/deploy_space.py`, `tools/deploy_phone_live.py`, `model_tests/run_depth.py`, `model_tests/test_tracking.py`, `model_tests/README.md`, `TRACKING.md`, `README.md`, `PHONE-LIVE.md`, `zerogpu/README.md`, `../frontend/live-detection-client.js`, `../frontend/tests/live-detection-client.test.mjs`, and `../frontend/README.md`.
Additional validation evidence: `benchmarks/2026-10-10/risk/local-validation.json`.
Files generated locally but ignored by Git: `model_tests/outputs/risk-smoke-20261010/run.json`, `results.jsonl`, six annotated JPEGs, and the adjacent `risk-smoke-20261010-console.log`.
