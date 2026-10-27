# Traffic-Light Detection and Signal-State Classification

This document describes two pieces of work:

1. **Integrated into the backend**: improve traffic-light recall (a separate confidence threshold plus a second detector pass on an upper-center ROI) and mark the "front light".
2. **Offline experiment, not integrated into the backend**: classify pedestrian signal color from cropped images; the proposed integration interface is included for future use.

All quantitative results come from the public **ImVisible / PTL (Shanghai)** dataset. Team-taken Singapore photos were compressed by chat software and blurred at night; they were used only for troubleshooting, not quantitative conclusions.

## 1. Changes Integrated into the Backend

| Change | Location | Description |
|---|---|---|
| Separate traffic-light confidence threshold | `CLASS_CONF` in `core/detector.py` | `traffic light` uses 0.12; other classes remain at 0.25. Applies even when ROI is disabled. |
| Input limit 1280 | `MAX_INPUT_EDGE` in `core/detector.py` | The server accepts JPEGs whose longest edge is at most 1280. **Phones still send 640** (`frontend/camera.js`, `live-detection-client.js`, and `hf_video_relay.py` are unchanged). |
| Second detector pass on ROI | `roi_crop` in `core/detector.py`; `realtime/detector.py` | Crop the center 40% horizontally and upper 55% vertically at original resolution, letterbox to 640, then run inference again with the same CUDA Graph (batch remains 1; two sequential calls). **Keep only traffic-light boxes from this pass.** |
| Merge | `merge_boxes` in `core/detector.py` | Apply class-aware NMS (IoU 0.5) only among traffic-light boxes; preserve all other classes and their order unchanged. |
| Front-light marker | `mark_front_light` in `core/detector.py` | Among vertical traffic-light boxes (height > width), mark the one nearest the frame's horizontal center as `front_light: true` and the rest `false`; do not remove boxes. |
| Offline evaluation flags | `model_tests/run_depth.py` | `--roi` and `--max-edge` (default 640; unchanged behavior). |

Metadata adds `class_confidence_thresholds`; when ROI is enabled it also includes `roi` and `roi_ms`. `inference_ms` and `forward_event_ms` sum both inference calls.

Usage (on a GPU machine, from `backend/`):

```bash
python -X utf8 -m model_tests.run_depth --input <image-directory> --output <new-output-directory> --roi --max-edge 1280 --label roi
```

### Detection Results (739 Images in the ImVisible Test Set, Measured Per Image)

ImVisible does not annotate signal boxes. "Detected" therefore means that at least one traffic-light box was found in an image containing a primary signal. Detecting a vehicle signal also counts, so results are optimistic.

| Configuration | Recall on images with signals | False positives on images without signals |
|---|---|---|
| Traffic-light threshold 0.12 | 0.84 | 6/54 = 0.11 |
| Threshold 0.12 + ROI | **0.93** | 9/54 = 0.17 |

Latency (Colab T4, mean over 57 static images): detection 23 → 53 ms; end-to-end 79 → 114 ms with ROI enabled.

> The opt-in walking-aid model `yolo26n-walk` (`PRS_DETECTION_MODEL=yolo26n-walk`) detects
> pedestrian-signal colour itself (green threshold 0.364) and does not use the crop classifier below;
> see [WALK-MODEL.md](WALK-MODEL.md). The default COCO model is unchanged.

## 2. Pedestrian-Signal Color Classifier (Offline Experiment)

### Comparison of Three Methods (ImVisible Test Set, Per Image)

| Method | Result | Conclusion |
|---|---|---|
| HSV color rules (baseline) | Accuracy 0.55–0.62; red classified as green 3–14/226; about 27% classified as "uncertain" | Too coarse |
| Whole-image classifier (MobileNetV3-Large, 576×432) | Four-class accuracy 0.951; at τ=0.5, red classified as green 2/226; green recall 0.97 | **Rejected**: error inspection found that a red signal was classified as "green" whenever someone was crossing (shortcut learning). |
| **Signal-crop classifier (MobileNetV3-Small, 96×96)** | At τ=0.95, red classified as green **4/226 (1.8%)**; green classified as "green" 0.61 | **Selected**: it looks only at the signal and avoids "guessing the light from the person". |

### Signal-Crop Classifier Method

1. Use the existing detector (threshold 0.12 + ROI) to find traffic-light boxes in ImVisible training and validation images.
2. Select the signal: choose the box above and nearest to the far end of the crosswalk centerline (ImVisible annotates the centerline).
3. Label automatically using the image's primary signal state (red/green/dark). Discard samples whose HSV result strongly contradicts that label; these likely contain vehicle signals. This yields 2,788 training and 694 validation crops without manual box annotation.
4. Fine-tune ImageNet-pretrained MobileNetV3-Small for 25 epochs, use class weighting, and **do not apply hue augmentation**.
5. Safety threshold τ: output "green" only when green probability ≥ τ; otherwise output "red" when red has the highest probability; output "unknown" in all other cases.

### Threshold Trade-Off (ImVisible Test Set)

| τ | 0.5 | 0.7 | 0.8 | 0.9 | **0.95** |
|---|---|---|---|---|---|
| Red classified as green | 25/226 (11%) | 19/226 (8.4%) | 15/226 (6.6%) | 6/226 (2.7%) | **4/226 (1.8%)** |
| Green classified as "green" | 0.74 | 0.69 | 0.67 | 0.64 | **0.61** |

Recommended τ = 0.95 when there is no multi-frame confirmation.

An additional v2 experiment retrained using only "clean samples whose HSV label agreed". The validation set appeared perfect (0/169 red signals classified as green), but it had been filtered with the same rule, making it easier (circular validation). The model became overconfident and performed worse on the test set (8/226). **Rejected.**

### Latency (Kaggle Tesla T4, Median / p95 over 200 Runs, ms)

| Signals per frame | Crop + preprocessing (CPU) | Inference (GPU) | Total FP32 | Total FP16 |
|---|---|---|---|---|
| 1 | 0.45 / 0.54 | 5.26 / 6.15 | **5.7 / 6.7** | 7.5 / 8.6 |
| 3 | 1.35 / 1.76 | 5.42 / 7.43 | **6.8 / 9.3** | 8.4 / 9.0 |
| 5 | 2.19 / 2.53 | 5.49 / 6.18 | **7.7 / 8.6** | 9.7 / 13.2 |

End-to-end latency is about 5–7% slower. GPU time changes little with the number of signals and is dominated by kernel launch overhead; FP32 is sufficient. To reduce it further, the existing YOLO CUDA Graph pattern could be considered (not measured).

## 3. Integration Interface Contract

Follow the integration pattern of `DepthEstimator` in `realtime/depth.py`; this is an optional standalone model:

```
class LightStateClassifier:
    __init__(weights_path, expected_sha256)   # Load once at startup and verify the hash.
    activate()                                # Call once after GPU allocation.
    predict(image, boxes) -> boxes            # image: original RGB; boxes: detected boxes.
```

- Process only boxes with `class_name == "traffic light"`: expand each box by 25% on all sides, letterbox to 96×96, and apply ImageNet normalization.
- Add `light_state` (`red` / `green` / `unknown`) and `light_prob` to those boxes.
- Add `light_model` and `light_ms` to metadata.
- Rule: `p_green ≥ τ (0.95)` → `green`; otherwise, if `argmax == red`, return `red`; otherwise return `unknown`.
- Weights: `models/light_crop.pt` (6.2 MB). Although `.gitignore` normally ignores `models/*.pt`, this file is small and was approved by the team for an explicit `git add -f`. Input, classes, threshold, and SHA-256 are in `models/light_crop.json`; validate the hash at load time.
- Before the classifier is trained, a stub that always returns `unknown` can be integrated without affecting other modules.

## 4. Limitations (Must Be Stated in Reports)

1. **Signal selection on ImVisible uses the manually annotated crosswalk centerline**, which is unavailable at runtime; color-classification results are therefore an optimistic upper bound. Runtime currently uses `front_light` (the vertical box nearest the image centerline), whose selection accuracy is substantially lower.
2. Detection is evaluated per image because signal-box annotations are unavailable; this is optimistic.
3. Results use a single frame, with no multi-frame video confirmation.
4. Validation uses Shanghai data only; pedestrian signal appearance varies by city.
5. Latency was measured on Kaggle/Colab T4; remeasure on the deployment GPU.

The system is a safety reminder that says "signal ahead" or "wait at the red signal"; it is **not a basis for deciding whether to cross**.

## 5. Reproduction Scripts

All scripts are in `tools/traffic_light/`. They are offline experiment scripts run on Colab/Kaggle (using `/content` paths), not runtime code.

| Script | Purpose |
|---|---|
| `imvisible_eval.py` | Download ImVisible, run detection, and measure per-image recall and the HSV color baseline. |
| `train_light_cls.py` / `light_cls_check.py` | Train/check errors for the whole-image classifier (deprecated comparison approach). |
| `train_light_crop.py` | Crop classifier: generate crops, train, calibrate τ, and test. |
| `train_light_crop_v2.py` | Compare the τ trade-off curve with clean-data retraining. |
| `light_crop_bench.py` | Measure classifier latency. |
| `eval_detection.py` / `eval_front_light.py` | Evaluate detection on team-taken photos (near-range mode `--near`, and front pedestrian-signal mode). |

Data: ImVisible annotations come from [samuelyu2002/ImVisible](https://github.com/samuelyu2002/ImVisible). Images are the 768×576 versions from its Google Drive (the repository is MIT-licensed; image licensing is not separately specified).
