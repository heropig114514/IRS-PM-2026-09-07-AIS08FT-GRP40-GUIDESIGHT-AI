"""Responsibilities: Bounded JPEG decoding and unchanged YOLO26 FP32 image transforms/rendering.
Implementation: Unchanged transforms/JPEG80; depth/motion labels, concise high/critical risk labels.
Related Modules: realtime.detector/depth/tracking/risk; zerogpu.live_worker; realtime.protocol.
Declaration Index: DetectionError; DetectionError.__init__; decode_frame; letterbox; decode_boxes;
roi_crop; merge_boxes; box_iou; pedestrian_lights; mark_front_light; label_font; annotate.
Variable Index: INPUT_EDGE —640 model tensor edge; MAX_INPUT_EDGE —1280 accepted frame edge;
CONFIDENCE_THRESHOLD —0.25 default; CLASS_CONF —per-class override (traffic light 0.12); the walk
model adds its colour-class thresholds per detector (core.detection_model.light_rule);
ROI_X_FRACTION/ROI_Y_FRACTION —centre-top crop for the extra traffic-light pass;
MERGE_IOU —0.5; TRAFFIC_LIGHT —class label; PEDESTRIAN_LIGHT —merged walk-model light label;
LIGHT_LABELS —every signal label admitted from the ROI pass and to front-light marking;
LIGHT_GROUP_IOU —0.3 overlap that joins colour boxes of one lamp;
JPEG_QUALITY —80; MAX_FRAME_BYTES —1MiB.
No CPU model, allocator, singleton or hidden fallback is implemented here.
ttc/ttc_label — concise high/critical annotation text; no scoring rules live in rendering.
"""

import io
import math
from functools import lru_cache

INPUT_EDGE = 640
MAX_INPUT_EDGE = 1280
CONFIDENCE_THRESHOLD = 0.25
CLASS_CONF = {"traffic light": 0.12}
TRAFFIC_LIGHT = "traffic light"
PEDESTRIAN_LIGHT = "pedestrian light"
LIGHT_LABELS = frozenset({TRAFFIC_LIGHT, PEDESTRIAN_LIGHT, "ped_red", "ped_green", "ped_off"})
LIGHT_GROUP_IOU = 0.3
# Extra ROI pass: horizontal middle 40% (0.3-0.7 of width), vertical top 55% (0-0.55 of height).
ROI_X_FRACTION = (0.3, 0.7)
ROI_Y_FRACTION = (0.0, 0.55)
MERGE_IOU = 0.5
JPEG_QUALITY = 80
MAX_FRAME_BYTES = 1048576


class DetectionError(Exception):
    """Functionality: Carry safe diagnostic errors without media or dependency exception text.
    Logic: GPU/relay adapters consume code/detail/status; Constraints: no implicit fallback
    detector.
    """

    def __init__(self, code, detail, status):
        """Functionality: Initialize a public error; Inputs: self/code/detail/status.
        Outputs: exception state; Logic: fixed detail; Constraints: no logging or body side effect.
        """
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def decode_frame(payload):
    """Functionality: Decode only a bounded, complete camera JPEG into RGB.
    Inputs: payload bytes; Outputs: independent PIL RGB image or 400/413 DetectionError.
    Logic: check byte size, JPEG format and dimensions before decompression, then force loading.
    Constraints: maximum edge MAX_INPUT_EDGE (1280; the full-frame pass still downsizes to 640);
    no EXIF location parsing or disk writes.
    """
    from PIL import Image, UnidentifiedImageError

    if not 0 < len(payload) <= MAX_FRAME_BYTES:
        raise DetectionError("frame_size", "The image is empty or exceeds 1 MiB.", 413)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            longest = max(image.size)
            if image.format != "JPEG" or not 0 < min(image.size) <= longest <= MAX_INPUT_EDGE:
                raise DetectionError(
                    "invalid_frame",
                    "Only JPEG images with a maximum edge of 1,280 pixels are accepted.",
                    400,
                )
            image.load()
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise DetectionError(
            "invalid_frame", "The JPEG image is corrupt or could not be decoded.", 400
        ) from error


def letterbox(image, output=None):
    """Functionality: Build a fixed FP32 RGB NCHW input and its inverse coordinate transform.
    Inputs: bounded RGB image, optional contiguous FP32 output [1,3,640,640].
    Outputs: tensor, scale, left/top padding; supplied tensor is overwritten completely.
    Logic: same resize/padding, direct FP32 divide into NCHW storage; avoid intermediate copies.
    Constraints: Pillow bilinear interpolation is explicit; no cropping, stretching or RGB/BGR swap.
    """
    import numpy as np
    from PIL import Image

    if output is None:
        output = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
    if (
        output.shape != (1, 3, INPUT_EDGE, INPUT_EDGE)
        or output.dtype != np.float32
        or not output.flags.c_contiguous
    ):
        raise ValueError("Input destination must be contiguous FP32 [1,3,640,640]")
    scale = INPUT_EDGE / max(image.size)
    resized = (round(image.width * scale), round(image.height * scale))
    left, top = (INPUT_EDGE - resized[0]) // 2, (INPUT_EDGE - resized[1]) // 2
    canvas = Image.new("RGB", (INPUT_EDGE, INPUT_EDGE), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    np.divide(
        np.asarray(canvas).transpose(2, 0, 1)[None], np.float32(255), out=output, dtype=np.float32
    )
    return output, scale, left, top


def decode_boxes(predictions, image_size, transform, names, *, class_conf=None):
    """Functionality: Convert verified e2e rows to clipped original-image detections.
    Inputs: predictions [1,300,6], image_size, transform, names and optional class overrides.
    Outputs: list of xyxy/score/class_id/label dictionaries; invalid model output raises 502.
    Logic: validate all numbers/classes, filter score below the per-class threshold
    (CLASS_CONF by default), invert letterbox and clip padding. Explicit {} retains uniform 0.25.
    Constraints: existing callers keep production thresholds; historical experiments opt out
    explicitly without mutating global policy. No external NMS; empty detections are valid.
    """
    import numpy as np

    if predictions.shape != (1, 300, 6) or not np.isfinite(predictions).all():
        raise DetectionError("invalid_prediction", "The model output format is invalid.", 502)
    width, height = image_size
    scale, left, top = transform
    rows = predictions[0]
    scores, classes = rows[:, 4], rows[:, 5]
    if np.any((scores < 0) | (scores > 1)) or np.any(
        (classes < 0) | (classes >= len(names)) | (classes != np.trunc(classes))
    ):
        raise DetectionError("invalid_prediction", "The model class or confidence is invalid.", 502)
    overrides = CLASS_CONF if class_conf is None else class_conf
    if overrides:
        thresholds = np.asarray([overrides.get(name, CONFIDENCE_THRESHOLD) for name in names],
                                dtype=np.float32)
        selected = rows[scores >= thresholds[classes.astype(np.int64)]]
    else:
        selected = rows[scores >= CONFIDENCE_THRESHOLD]
    # Float64 matches the previous Python-float inverse, including clipping/order.
    restored = selected[:, :4].astype(np.float64)
    restored -= [left, top, left, top]
    restored /= scale
    np.clip(restored, 0, [width, height, width, height], out=restored)
    boxes = []
    for row, coordinate in zip(selected, restored, strict=True):
        coordinates = coordinate.tolist()
        if coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
            continue
        boxes.append(
            {
                "xyxy": coordinates,
                "score": float(row[4]),
                "class_id": int(row[5]),
                "label": names[int(row[5])],
            }
        )
    return boxes


def roi_crop(image):
    """Functionality: Cut the centre-top region where the pedestrian signal ahead appears.
    Inputs: RGB image. Outputs: (crop, x_offset, y_offset) in image pixels.
    Logic: ROI_X_FRACTION/ROI_Y_FRACTION of width/height, rounded; crop stays native resolution.
    Constraints: a degenerate (empty) crop returns None so the caller skips the ROI pass.
    """
    x1, x2 = (round(image.width * value) for value in ROI_X_FRACTION)
    y1, y2 = (round(image.height * value) for value in ROI_Y_FRACTION)
    if x2 <= x1 or y2 <= y1:
        return None
    return image.crop((x1, y1, x2, y2)), x1, y1


def merge_boxes(full_boxes, roi_boxes, iou=MERGE_IOU):
    """Functionality: Merge full-frame boxes with ROI signal boxes.
    Inputs: full-frame boxes and ROI boxes (already in full-frame coordinates), NMS IoU.
    Outputs: new list; only LIGHT_LABELS boxes from roi_boxes are admitted.
    Logic: class-aware NMS runs on signal boxes only; other full-frame boxes are returned untouched
    in order, followed by the surviving signals (descending score).
    Constraints: needs torch/torchvision (GPU host); empty ROI returns full_boxes unchanged.
    """
    roi_lights = [box for box in roi_boxes if box["label"] in LIGHT_LABELS]
    if not roi_lights:
        return full_boxes
    import torch
    from torchvision.ops import batched_nms

    others = [box for box in full_boxes if box["label"] not in LIGHT_LABELS]
    lights = [*(box for box in full_boxes if box["label"] in LIGHT_LABELS), *roi_lights]
    kept = batched_nms(
        torch.tensor([box["xyxy"] for box in lights], dtype=torch.float32),
        torch.tensor([box["score"] for box in lights], dtype=torch.float32),
        torch.tensor([box["class_id"] for box in lights], dtype=torch.int64), iou)
    return [*others, *(lights[index] for index in kept.tolist())]


def box_iou(a, b):
    """Functionality: Intersection over union of two xyxy boxes.
    Inputs: two [x1, y1, x2, y2] lists. Outputs: float in [0, 1].
    Logic: clipped intersection area over union area. Constraints: zero-area pairs give 0.
    """
    inter = (max(0.0, min(a[2], b[2]) - max(a[0], b[0])) *
             max(0.0, min(a[3], b[3]) - max(a[1], b[1])))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def pedestrian_lights(boxes, rule):
    """Functionality: Turn walk-model colour boxes into one neutral light box with a safe state.
    Inputs: decoded boxes and core.detection_model.light_rule output (None = original model).
    Outputs: new list; colour boxes are replaced by `pedestrian light` boxes carrying light_state
    ("green" / "red" / "unknown") and light_scores {red, green, off}; other boxes are untouched.
    Logic: colour boxes (by descending score) join the first group overlapping >= LIGHT_GROUP_IOU;
    per group: green only if max green score >= green_threshold and > max red score, else red if
    any red box, else unknown (the rule calibrated on ImVisible, see WALK-MODEL.md). The kept
    geometry/score is the group's highest-score box; class_id is the rule's stable red id.
    Constraints: class names never carry the colour downstream, so no consumer can announce green
    without the threshold; no rule leaves boxes unchanged.
    """
    if rule is None:
        return boxes
    red, green, off = rule["classes"]
    colour = sorted((box for box in boxes if box["label"] in rule["classes"]),
                    key=lambda box: box["score"], reverse=True)
    groups = []
    for box in colour:
        for group in groups:
            if box_iou(group[0]["xyxy"], box["xyxy"]) >= LIGHT_GROUP_IOU:
                group.append(box)
                break
        else:
            groups.append([box])
    merged = []
    for group in groups:
        scores = {name: max((box["score"] for box in group if box["label"] == name), default=0.0)
                  for name in (red, green, off)}
        state = ("green" if scores[green] >= rule["green_threshold"] and scores[green] > scores[red]
                 else "red" if scores[red] > 0 else "unknown")
        merged.append({**group[0], "label": PEDESTRIAN_LIGHT, "class_id": rule["class_id"],
                       "light_state": state,
                       "light_scores": {"red": scores[red], "green": scores[green],
                                        "off": scores[off]}})
    return [*(box for box in boxes if box["label"] not in rule["classes"]), *merged]


def mark_front_light(boxes, image_size):
    """Functionality: Flag the pedestrian signal straight ahead among signal boxes.
    Inputs: boxes (xyxy in frame pixels), (width,height). Outputs: new list; LIGHT_LABELS boxes get
    front_light True for the chosen one and False for the rest; other classes are untouched.
    Logic: candidates are vertical (h > w); walk-model `pedestrian light` boxes are preferred over
    generic traffic lights when any exists; choose the smallest |box centre x - frame centre x|.
    Constraints: nothing is deleted; with no vertical candidate every light is False.
    """
    def vertical(box):
        x1, y1, x2, y2 = box["xyxy"]
        return box["label"] in LIGHT_LABELS and (y2 - y1) > (x2 - x1)

    candidates = [index for index, box in enumerate(boxes) if vertical(box)]
    pedestrian = [index for index in candidates if boxes[index]["label"] == PEDESTRIAN_LIGHT]
    candidates = pedestrian or candidates
    chosen = min(candidates, key=lambda index: abs(
        (boxes[index]["xyxy"][0] + boxes[index]["xyxy"][2]) / 2 - image_size[0] / 2),
        default=None)
    return [{**box, "front_light": index == chosen} if box["label"] in LIGHT_LABELS else box
            for index, box in enumerate(boxes)]


@lru_cache(maxsize=1)
def label_font():
    """Functionality: Reuse the bundled label font without mutating it. Inputs: None.
    Outputs: Pillow font; Logic: load size 14 once.
    Constraints: no files downloaded or font mutation.
    """
    from PIL import ImageFont

    return ImageFont.load_default(size=14)


def annotate(image, boxes):
    """Functionality: Draw boxes, optional depth/motion and meaningful risk into JPEG.
    Inputs: RGB image and validated boxes; Outputs: encoded JPEG bytes or 502 size error.
    Logic: preserve previous labels when ID/depth unavailable (pedestrian lights add their state);
    otherwise add ID/motion and
    finite signed m/s for a known motion state (unknown omits speed);
    high/critical labels replace confidence/speed with concise TTC/level; other labels stay normal;
    draw two-pixel outlines and readable backed labels, encode at explicit quality 80.
    Constraints: input remains in memory; English font is Pillow bundled, no external font download.
    """
    from PIL import ImageDraw

    image = image.copy()
    draw = ImageDraw.Draw(image)
    font = label_font()
    for box in boxes:
        x1, y1, x2, y2 = box["xyxy"]
        draw.rectangle((x1, y1, x2, y2), outline=(0, 255, 140), width=2)
        label = f"{box['label']} {box['score']:.2f}"
        if "light_state" in box:
            label = f"{box['label']} {box['light_state']} {box['score']:.2f}"
        if "distance_m" in box:
            distance = box["distance_m"]
            label += f" | {distance:.1f} m" if distance is not None else " | N/A"
            if type(box.get("track_id")) is int and box["track_id"] > 0 and distance is not None:
                state, speed = box.get("motion_state", "unknown"), box.get("relative_speed_mps")
                label = f"ID {box['track_id']} | {label} | {state}"
                if state != "unknown" and isinstance(speed, int | float) and math.isfinite(speed):
                    label += f" | {speed:+.2f} m/s"
                if box.get("risk_level") in ("high", "critical"):
                    ttc = box.get("ttc_sec")
                    ttc_label = f"{ttc:.1f}s" if ttc is not None else "N/A"
                    label = (f"ID {box['track_id']} | {box['label']} | {distance:.1f}m | "
                             f"{state} | TTC {ttc_label} | {box['risk_level'].upper()}")
        bounds = draw.textbbox((0, 0), label, font=font)
        text_width, text_height = bounds[2] - bounds[0] + 6, bounds[3] - bounds[1] + 6
        label_x = max(0, min(x1, image.width - text_width))
        label_y = max(0, y1 - text_height)
        draw.rectangle(
            (label_x, label_y, label_x + text_width, label_y + text_height), fill=(15, 40, 35)
        )
        draw.text((label_x + 3, label_y + 3 - bounds[1]), label, font=font, fill="white")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=JPEG_QUALITY)
    payload = output.getvalue()
    if len(payload) > MAX_FRAME_BYTES:
        raise DetectionError("result_size", "The annotated image exceeds 1 MiB.", 502)
    return payload
