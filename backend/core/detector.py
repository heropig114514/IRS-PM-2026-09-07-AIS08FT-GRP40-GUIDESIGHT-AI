"""Responsibilities: Bounded JPEG decoding and unchanged YOLO26 FP32 image transforms/rendering.
Implementation: Unchanged transforms/JPEG80; depth/motion labels, concise high/critical risk labels;
optional explicit diagnostic text renderer, with direct Pillow drawing as the default.
Decode through Pillow's JPEG factory so SDK patches cannot probe/install unrelated image plugins.
Related Modules: realtime.detector/depth/tracking/risk; zerogpu.live_worker; realtime.protocol.
Declaration Index: DetectionError; DetectionError.__init__; decode_frame; letterbox; decode_boxes;
label_font; annotate.
Variable Index: INPUT_EDGE —640; CONFIDENCE_THRESHOLD —0.25; JPEG_QUALITY —80;
MAX_FRAME_BYTES —1MiB. No CPU model, allocator, singleton or hidden fallback is implemented here.
ttc/ttc_label — concise high/critical annotation text; no scoring rules live in rendering.
"""

import io
import math
from functools import lru_cache

INPUT_EDGE = 640
CONFIDENCE_THRESHOLD = 0.25
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
    Logic: bound bytes, use pinned Pillow JPEG factory, check format/dimensions, then force loading.
    The SDK globally patches Image.open with an optional-plugin probe; JPEG-only service decoding
    must not invoke that generic format discovery or its installation side effects.
    Constraints: maximum edge 640 matches existing capture; no EXIF location parsing or disk writes.
    """
    from PIL import Image, JpegImagePlugin

    if not 0 < len(payload) <= MAX_FRAME_BYTES:
        raise DetectionError("frame_size", "图像为空或超过 1 MiB。", 413)
    try:
        with JpegImagePlugin.jpeg_factory(io.BytesIO(payload)) as image:
            if image.format != "JPEG" or not 0 < min(image.size) <= max(image.size) <= INPUT_EDGE:
                raise DetectionError("invalid_frame", "仅接受最大边不超过 640 的 JPEG。", 400)
            image.load()
            return image.convert("RGB")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise DetectionError("invalid_frame", "JPEG 图像损坏或无法解码。", 400) from error


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


def decode_boxes(predictions, image_size, transform, names):
    """Functionality: Convert verified e2e rows to clipped original-image detections.
    Inputs: predictions [1,300,6], image_size (width,height), transform scale/left/top, names.
    Outputs: list of xyxy/score/class_id/label dictionaries; invalid model output raises 502.
    Logic: validate all numbers/classes, filter score below 0.25, invert letterbox and clip padding.
    Constraints: no external NMS; an empty list is a valid no-detection result.
    """
    import numpy as np

    if predictions.shape != (1, 300, 6) or not np.isfinite(predictions).all():
        raise DetectionError("invalid_prediction", "模型输出格式无效。", 502)
    width, height = image_size
    scale, left, top = transform
    rows = predictions[0]
    scores, classes = rows[:, 4], rows[:, 5]
    if np.any((scores < 0) | (scores > 1)) or np.any(
        (classes < 0) | (classes >= len(names)) | (classes != np.trunc(classes))
    ):
        raise DetectionError("invalid_prediction", "模型类别或置信度无效。", 502)
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


@lru_cache(maxsize=1)
def label_font():
    """Functionality: Reuse the bundled label font without mutating it. Inputs: None.
    Outputs: Pillow font; Logic: load size 14 once.
    Constraints: no files downloaded or font mutation.
    """
    from PIL import ImageFont

    return ImageFont.load_default(size=14)


def annotate(image, boxes, *, text_renderer=None):
    """Functionality: Draw boxes, optional depth/motion and meaningful risk into JPEG.
    Inputs: RGB image, validated boxes and optional explicit diagnostic text renderer.
    Outputs: encoded JPEG bytes or 502 size error.
    Logic: preserve previous labels when ID/depth unavailable; otherwise add ID/motion and
    finite signed m/s for a known motion state (unknown omits speed);
    high/critical labels replace confidence/speed with concise TTC/level; other labels stay normal;
    draw two-pixel outlines and readable backed labels, encode at explicit quality 80.
    Constraints: input remains in memory; English font is Pillow bundled, no external font download.
    Default calls Pillow directly; experimental renderers must preserve text/coordinates and errors.
    """
    from PIL import ImageDraw

    image = image.copy()
    draw = ImageDraw.Draw(image)
    font = label_font()
    for box in boxes:
        x1, y1, x2, y2 = box["xyxy"]
        draw.rectangle((x1, y1, x2, y2), outline=(0, 255, 140), width=2)
        label = f"{box['label']} {box['score']:.2f}"
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
        if text_renderer is None:
            draw.text((label_x + 3, label_y + 3 - bounds[1]), label, font=font, fill="white")
        else:
            text_renderer(draw, (label_x + 3, label_y + 3 - bounds[1]), label,
                          font=font, fill="white")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=JPEG_QUALITY)
    payload = output.getvalue()
    if len(payload) > MAX_FRAME_BYTES:
        raise DetectionError("result_size", "标注图像超过 1 MiB。", 502)
    return payload
