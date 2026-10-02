"""Responsibilities: Verified YOLO26n ONNX loading, bounded JPEG decoding and box rendering.
Implementation: One CPU thread, RGB letterbox, e2e xyxy/score/class output; no NMS or downloads.
Related Modules: core.vision; models/yolo26n.json pins the exported model and COCO class order.
Declaration Index: DetectionError/DetectionError.__init__ — safe errors;
decode_frame — JPEG boundary;
letterbox — tensor transform; decode_boxes — original-image boxes; annotate — JPEG rendering;
Detector/Detector.__init__/Detector.predict — verified session and single-frame inference;
get_detector — process-local initialization with persistent failure state until restart.
Variable Index: INPUT_EDGE/CONFIDENCE_THRESHOLD/JPEG_QUALITY — explicit inference/render settings;
MAX_FRAME_BYTES — existing 1 MiB boundary; logger — non-media diagnostics;
detector/initialization_error/initialization_lock — single-instance lifecycle.
"""

import hashlib
import io
import json
import logging
import os
import time
from pathlib import Path
from threading import Lock

INPUT_EDGE = 640
CONFIDENCE_THRESHOLD = 0.25
JPEG_QUALITY = 80
MAX_FRAME_BYTES = 1048576
logger = logging.getLogger(__name__)
detector = None
initialization_error = None
initialization_lock = Lock()


class DetectionError(Exception):
    """Functionality: Carry safe diagnostic errors without media or dependency exception text.
    Logic: HTTP adapter consumes code/detail/status; Constraints: no implicit fallback detector.
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
    Constraints: maximum edge 640 matches existing capture; no EXIF location parsing or disk writes.
    """
    from PIL import Image, UnidentifiedImageError

    if not 0 < len(payload) <= MAX_FRAME_BYTES:
        raise DetectionError("frame_size", "图像为空或超过 1 MiB。", 413)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "JPEG" or not 0 < min(image.size) <= max(image.size) <= INPUT_EDGE:
                raise DetectionError("invalid_frame", "仅接受最大边不超过 640 的 JPEG。", 400)
            image.load()
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise DetectionError("invalid_frame", "JPEG 图像损坏或无法解码。", 400) from error


def letterbox(image):
    """Functionality: Build a fixed FP32 RGB NCHW input and its inverse coordinate transform.
    Inputs: bounded RGB image; Outputs: tensor, scale, left/top padding.
    Logic: isotropic resize to 640, centered 114 padding, divide by 255; retain rounded padding.
    Constraints: Pillow bilinear interpolation is explicit; no cropping, stretching or RGB/BGR swap.
    """
    import numpy as np
    from PIL import Image

    scale = INPUT_EDGE / max(image.size)
    resized = (round(image.width * scale), round(image.height * scale))
    left, top = (INPUT_EDGE - resized[0]) // 2, (INPUT_EDGE - resized[1]) // 2
    canvas = Image.new("RGB", (INPUT_EDGE, INPUT_EDGE), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), scale, left, top


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
    boxes = []
    for x1, y1, x2, y2, score, class_id in predictions[0]:
        if not 0 <= score <= 1 or class_id != int(class_id) or not 0 <= class_id < len(names):
            raise DetectionError("invalid_prediction", "模型类别或置信度无效。", 502)
        if score < CONFIDENCE_THRESHOLD:
            continue
        coordinates = [
            max(0, min(width, (float(x1) - left) / scale)),
            max(0, min(height, (float(y1) - top) / scale)),
            max(0, min(width, (float(x2) - left) / scale)),
            max(0, min(height, (float(y2) - top) / scale)),
        ]
        if coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
            continue
        boxes.append(
            {
                "xyxy": coordinates,
                "score": float(score),
                "class_id": int(class_id),
                "label": names[int(class_id)],
            }
        )
    return boxes


def annotate(image, boxes):
    """Functionality: Burn boxes, English COCO names and confidence into returned JPEG pixels.
    Inputs: RGB image and validated boxes; Outputs: encoded JPEG bytes or 502 size error.
    Logic: draw two-pixel outlines and readable backed labels, encode at explicit quality 80.
    Constraints: input remains in memory; English font is Pillow bundled, no external font download.
    """
    from PIL import ImageDraw, ImageFont

    image = image.copy()
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)
    for box in boxes:
        x1, y1, x2, y2 = box["xyxy"]
        draw.rectangle((x1, y1, x2, y2), outline=(0, 255, 140), width=2)
        label = f"{box['label']} {box['score']:.2f}"
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
        raise DetectionError("result_size", "标注图像超过 1 MiB。", 502)
    return payload


class Detector:
    """Functionality: Hold a verified, immutable CPU session for the selected YOLO26n artifact.
    Logic: verify hash, shape and provider before inference; Constraints: caller serializes calls.
    """

    def __init__(self, model_path):
        """Functionality: Verify and load a local model; Inputs: self, absolute model_path.
        Outputs: session/names/input_name state, one non-sensitive initialization log.
        Logic: enforce hash, 80 classes and static shapes; restrict both thread pools to one.
        Constraints: never download a model, use GPU, quantize or change the export head.
        """
        import onnxruntime as ort

        manifest = json.loads(
            (Path(__file__).parents[1] / "models/yolo26n.json").read_text(encoding="utf-8")
        )
        path = Path(model_path)
        if (
            not path.is_absolute()
            or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["onnx_sha256"]
        ):
            raise ValueError("Model artifact does not match the pinned manifest")
        self.names = manifest["names"]
        if len(self.names) != 80 or manifest["model"] != "yolo26n":
            raise ValueError("Unexpected class manifest")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].shape != [1, 3, 640, 640]
            or inputs[0].type != "tensor(float)"
            or len(outputs) != 1
            or outputs[0].shape != [1, 300, 6]
            or self.session.get_providers() != ["CPUExecutionProvider"]
        ):
            raise ValueError("Unexpected ONNX session contract")
        self.input_name = inputs[0].name
        logger.info("YOLO initialized model=yolo26n classes=80 threads=1 provider=CPU")

    def predict(self, image):
        """Functionality: Infer and render one RGB frame; Inputs: self session and bounded image.
        Outputs: JPEG, boxes, inference_ms; Logic: preprocess, time ONNX, invert boxes, render.
        Constraints: timing includes ONNX only; no image/log persistence, retries or fallback.
        """
        tensor, scale, left, top = letterbox(image)
        started = time.monotonic()
        predictions = self.session.run(None, {self.input_name: tensor})[0]
        inference_ms = (time.monotonic() - started) * 1000
        boxes = decode_boxes(predictions, image.size, (scale, left, top), self.names)
        return annotate(image, boxes), boxes, inference_ms


def get_detector():
    """Functionality: Initialize once from the explicit YOLO_MODEL_PATH environment.
    Inputs: process state/environment and pinned manifest; Outputs: Detector or safe 503.
    Logic: lock initialization; cache a failed load until restart and log exception type only.
    Constraints: absent config is explicit; manual requests never swap models or retry loading.
    """
    global detector, initialization_error
    with initialization_lock:
        if detector is not None:
            return detector
        if initialization_error is not None:
            raise initialization_error
        path = os.environ.get("YOLO_MODEL_PATH", "").strip()
        if not path:
            raise DetectionError("model_not_configured", "服务端尚未配置 YOLO 模型。", 503)
        try:
            detector = Detector(path)
            return detector
        except Exception as error:
            logger.error(
                "YOLO initialization failed exception_type=%s; check artifact/hash/dependencies",
                type(error).__name__,
            )
            initialization_error = DetectionError(
                "model_unavailable", "YOLO 模型加载失败，请检查服务端模型和依赖。", 503
            )
            raise initialization_error from error
