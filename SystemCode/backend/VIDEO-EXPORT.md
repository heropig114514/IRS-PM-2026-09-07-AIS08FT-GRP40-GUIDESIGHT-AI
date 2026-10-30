# Complete video export

`python -m tools.export_video` processes every decoded frame of a local video through the native
CUDA detection, distance, BoT-SORT and rules-v2 pipeline. It produces an H.264 MP4 with class,
distance and risk overlays and copies all original audio packet payloads. It does not synthesize
speech or use the live service's session time limit. This is a backend CLI, not an upload API.

## Run

From `SystemCode/backend`, use the existing CUDA inference environment and install
`requirements-video.txt`. Provision the weights described in [DEPTH.md](DEPTH.md) and
[WALK-MODEL.md](WALK-MODEL.md); there are no implicit model downloads or CPU fallbacks.

```powershell
python -m pip install -r requirements-video.txt
python -m tools.export_video --input "D:\videos\input.mp4" --output "model_tests/outputs/demo-01"
```

In this migrated Windows workspace the existing interpreter is
`..\..\backend\.venv\Scripts\python.exe`. Use that executable instead of `python` if the inference
environment is not activated. The output directory must be new; the input is never modified.

Options:

| Option | Behavior |
| --- | --- |
| `--profile stable` | Default explicit `video-stable-v6` candidate/motion/risk refinement |
| `--profile baseline` | Original perception pipeline, without the new temporal refinement |
| `--show-ids` | Include debug track IDs; IDs remain in JSON regardless of this flag |
| `--max-edge 640` | Cap inference and output display dimensions; allowed 64–1280 |
| `--model yolo26n-walk` | Default model unless `PRS_DETECTION_MODEL` selects another admitted profile |

Distance/depth environment settings remain supported and are frozen in the manifest. A baseline
comparison must use the same source, weights, input size, distance mode and precision. The current
box-attached label layout is used for both profiles; baseline means original perception values,
not the old overlapping label renderer. No frontend or live-service default is changed.

## Outputs and timing

- `annotated.mp4`: completed video, with the original encoded audio tracks.
- `results.jsonl`: one record per source frame, exact timestamp/duration, raw depth, filtered
  display depth, track ID, current motion, published risk and immediate pre-hysteresis risk.
- `run.json`: running/completed/failed/cancelled status, processed-frame count, source and model
  hashes, selected policies, timing, output hash and decoded-output/audio verification.

The exporter uses source presentation time rather than processing time for speed/TTC. Variable
frame intervals, right-angle rotation and non-square source pixel aspect are normalized explicitly.
Odd output dimensions are padded by one pixel for H.264. Original audio timestamps are shifted by
the same video origin. Unsupported input or MP4 audio codecs fail explicitly, rather than silently
dropping audio. Existing subtitle/data tracks are outside this export contract.

Partial files have `.partial` names and are not published as `annotated.mp4`. After failure choose
a fresh directory for another attempt; there is no resume/overwrite mode. Unsupported timestamps,
changed geometry, decoding failures, missing models and corrupt output fail with an explicit status.

## Stable profile

The immutable policy is recorded in `run.json.settings.refinement`. It changes postprocessing,
not model weights or inference precision. It is optional and local to each video-export detector.

1. **Candidate confirmation and deduplication.** Before BoT-SORT, suppress only same-class pairs
   with IoU at least 0.85, keeping the highest score. Require short visual/confidence persistence
   for uncertain candidates. A confidence of 0.9 admits any new class immediately. For recall,
   pedestrian, vehicle, stair, bollard and cone detections admit scores of at least 0.5 immediately;
   the exact priority-class list is in the manifest. Otherwise ordinary
   confirmation uses at least three observations over 0.1 seconds with median confidence 0.5;
   weaker persistent evidence uses at least eight observations over 0.4 seconds and median 0.35.
   Traffic and merged pedestrian lights bypass temporal confirmation at their original decoder
   thresholds (currently 0.12 and 0.05 respectively, supplied by the active detector). This avoids
   discarding intermittent small signals already accepted by the model's operating threshold.
   The manifest records `threshold_confirmed_classes`; class thresholds and light-color rules
   are unchanged. Same-class deduplication still applies. Missing detections never synthesize a
   light box or hold its previous color; low-confidence model false positives remain possible.
   For other classes, confirmation lasts
   0.3 seconds after its last qualifying observation. An actual measured distance at most 3 m
   bypasses confirmation. No semantic class is blacklisted: consistent confident animals remain
   detectable. Every raw candidate, duplicate and unconfirmed depth-fused candidate stays in the
   frame's `refinement_audit`, including scores, boxes and suppression stage.

   **Display-only people/vehicles:** person, car, bus, truck, motorcycle, bicycle and scooter
   detections can also display immediately at score 0.35, or after two same-class IoU >=0.4
   observations spanning at least 0.03 s at the original decoder threshold (normally 0.25).
   Association evidence survives gaps of at most 0.2 s; absent frames never draw historical boxes.
   Original stricter confirmation remains in `alert_detection_confirmed`. Additional distant
   boxes are marked `display_only` and cannot generate alerts, even if their estimated risk is
   HIGH. Existing actual-depth <=3 m bypass and all original risk/vehicle gates remain effective.
   Other object classes retain their previous confirmation policy. The manifest records these
   display thresholds separately; increased display recall can also retain weak false detections.
2. **Depth/motion.** Keep `distance_m` as the raw measurement. Gate isolated jumps relative to the
   recent median using `max(1 m, 15%) + 10 m/s * elapsed time`. Three consistent changed readings
   over at least 0.07 s rebase the history, avoiding a fictitious velocity across a discontinuity.
   Close readings bypass the gate immediately. A one-second, at-most-64-sample Theil-Sen fit uses
   pairs separated by at least 0.2 s and requires six samples spanning at least 0.5 s. A residual
   noise check uses median residuals. Unsupported noisy slopes become `motion_quality=uncertain`,
   with no speed and unknown motion,
   rather than stationary. Low-noise flat depth remains measured stationary; direction uses a
   0.5 m/s dead band. Display distance
   uses a time-based EMA with a 0.2 s time constant. Missing depth displays N/A; a rejected jump
   displays the previous filtered distance with `?`; raw risk is unknown. The bounded risk display
   hold described below may retain a clearly marked prior level, never a fabricated low risk.
   Gaps longer than 0.6 s reset depth evidence. Original EMA/motion values remain in
   `unfiltered_motion` for diagnosis.
3. **Risk smoothing.** Run the unchanged rules-v2 formula on refined motion, preserving every
   immediate result in `instant_risk`. Smooth only valid scores using source-time EMA constants
   0.12 s on rises and 0.35 s on falls. Apply a five-point band margin: for example, enter HIGH at
   filtered score >=55, leave it below 45. Confirm rising/lowering evidence for 0.2/0.45 valid
   seconds. Confirmation follows the direction of change, so a consistently decreasing score
   does not restart its timer merely by crossing another lower band. Brief gaps pause evidence;
   each recovered valid frame contributes only its current frame interval, never the entire
   missing interval. This prevents both false confirmation during missing data and indefinitely
   sticky risk under alternating valid/missing observations.

   After a measured approach, require 0.3 s of continuous measured stationary/receding evidence
   (`motion_release_s`) before allowing score decay. Uncertain motion shares the 0.3 s timeout:
   briefly pause decay, then resume distance-only rules if velocity remains unreliable. Motion
   stays unknown, not stationary; `motion_uncertain` records this limitation. This window is not
   the total risk downgrade latency; the existing EMA and 0.45 s level confirmation still apply.
   Renewed approach resets the window. Alternating uncertain/stationary observations cannot
   restart the timeout indefinitely; missing/absent observations do not count. Urgent evidence
   still escalates immediately.

   A same-ID/class invalid observation can retain its last level for at most 0.3 s since the last
   valid score. It is explicitly rendered as `[HOLD]`, with `risk_data_status=held`, `risk_age_s`,
   frozen `smoothed_risk_score` and unmodified current `instant_risk`; alerts and priorities are
   always off on held frames. Repeated invalid observations do not extend this grace period.
   Expired/no-history inputs stay UNKNOWN with no invented score. New IDs, changed classes, long
   gaps and tracker resets cannot inherit the previous object's risk. Absent boxes are not drawn.

   Current raw HIGH/CRITICAL for an object within 3 m, or a reliable measured approach with TTC
   <=1.5 s, establishes an immediate risk floor. It bypasses a delayed rise and also cancels a
   pending downgrade below that floor. Close distance alone does not bypass every ordinary
   fluctuation. Current vehicle/other raw alert gates still apply; stable color cannot independently
   create an alert. `risk_score`/`smoothed_risk_score` describe the filtered signal, while reasons
   include explicit smoothing/hysteresis markers and `instant_risk` retains raw score/reasons.
4. **Labels.** Hide IDs by default and attach each label just above its own box's top-left,
   clamping at image edges. Try four local offsets to reduce overlap, preferring the smallest
   movement on ties. Movement from the clamped anchor is limited to 28 pixels and the connector
   to the box's top-left to 30 pixels in inference coordinates. Accept remaining overlap when
   nearby space runs out; never search distant rows or image edges. Track order determines
   allocation independently of risk; urgent labels are painted last. Show filtered meters rather
   than raw per-frame jitter. All boxes remain drawn; an exceptionally tiny image may not fit
   the essential label text, which still remains in JSONL. The layout version is `box-local-v2`.

## Validation and limits

`python -m unittest video_export.tests video_export.test_refinement video_export.test_risk_smoothing`
checks real media/audio
roundtrips, >55-second processing, pixel aspect/rotation, failure handling, deduplication, class
confirmation, near-hazard bypass, depth spikes, persistent discontinuities, motion, risk gates,
actual detector hook order and label layout. Run the backend checks in `AGENTS.md` as well.
Risk tests additionally cover marked short holds, expiry, source-time weighting, missing intervals,
identity isolation, same-frame urgent floors, current alert suppression and downgrade recovery.
For the synthetic 28fps step tests, nonurgent 15->65 reaches HIGH within 0.5 s, and sustained
65->15 reaches LOW within 1.5 s of valid input. With every other frame missing after a score of 90,
the tested release
bound is 3 source seconds. These include filter and confirmation latency; they are not externally
validated safety thresholds. Shorter waiting times are not the sole acceptance criterion.

Compare completed runs with the same source, weights and base inference settings:

```powershell
python -m tools.compare_video_exports --before model_tests/outputs/baseline --after model_tests/outputs/stable --output model_tests/outputs/comparison-01
```

This writes `comparison.json` and paired decoded frames. Counts describe object observations,
not distinct people: report temporal changes per 1,000 eligible adjacent same-ID/class frame pairs
as well as raw counts, because confirmation and deduplication change the denominators. Near-box
retention matches unique baseline boxes at <=3m by same class and IoU >=0.5 in the same frame;
it is not annotated real-object recall. Raw detector hashes and source timestamps verify that the
comparison did not quietly change network input/outputs. A different raw hash is reported, not
treated as equivalent evidence. Per-run findings belong with the output artifacts.

These are heuristic stability changes, not proof of distance accuracy or collision prediction.
Candidate confirmation trades short detection delay and possible weak-object recall loss for fewer
transient false positives; deduplication can merge genuinely overlapping same-class objects.
Depth smoothing and risk confirmation introduce lag except for the explicit urgent paths. Retain
the baseline and inspect both accepted and suppressed candidates, especially nearby people,
vehicles and signals. Real absolute distances require ground truth; a single demo does not validate
all lighting, camera motion, animal detection or operational safety. The original audio is copied
from the source and is not generated from or synchronized semantically with newly computed risk.
