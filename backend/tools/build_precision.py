"""Responsibilities: Build explicit FP16 and INT8 test artifacts, never production replacements.
Implementation: Convert pinned FP32 ONNX; QDQ per-channel MinMax uses COCO8 train only.
Related Modules: core.detector; benchmark_acceleration.py; models/experiments (ignored binaries).
Declaration Index: Calibration; Calibration.__init__; Calibration.get_next; main.
Variable Index: None; CLI paths, calibration split/format and model hashes recorded in output JSON.
"""

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

import onnx
from onnxruntime.quantization import (
    CalibrationDataReader,
    CalibrationMethod,
    QuantFormat,
    QuantType,
    quantize_static,
)
from PIL import Image


class Calibration(CalibrationDataReader):
    """Functionality: Supply public calibration data. Logic: each train JPEG exactly once.
    Constraints: COCO8's four train images are a smoke test, not production calibration.
    """

    def __init__(self, folder, input_name):
        """Functionality: Prepare fixed inputs. Inputs: self, train folder and model input_name.
        Outputs: samples iterator; Logic: existing 640/JPEG80/RGB/letterbox pipeline.
        Constraints: never include evaluation images or change camera preprocessing.
        """
        from core.detector import decode_frame, letterbox

        samples = []
        for path in sorted(folder.glob("*.jpg")):
            with Image.open(path) as image:
                image = image.convert("RGB")
                scale = min(1, 640 / max(image.size))
                image = image.resize(
                    (round(image.width * scale), round(image.height * scale)),
                    Image.Resampling.BILINEAR,
                )
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=80)
            samples.append({input_name: letterbox(decode_frame(buffer.getvalue()))[0]})
        if len(samples) != 4:
            raise ValueError("Expected exactly four COCO8 train images")
        self.samples = iter(samples)

    def get_next(self):
        """Functionality: Advance calibration. Inputs: self.samples iterator; Outputs: dict or None.
        Logic: return each fixed tensor once; Constraints: no dataset replay or implicit retry.
        """
        return next(self.samples, None)


def main():
    """Functionality: Create precision experiments with provenance.
    Inputs: CLI model/dataset/output.
    Outputs: FP16/INT8 artifacts and manifest; Logic: fixed conversion and calibration parameters.
    Constraints: not deployed, no training/download, no claimed mAP/quality equivalence.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from onnxconverter_common import float16

    args.output.mkdir(exist_ok=True)
    model = onnx.load(str(args.model))
    fp16 = args.output / "yolo26n-fp16.onnx"
    # Integer class indices are cast explicitly to FP32 by the exported head.
    # Preserve these Cast nodes and let the converter insert typed boundary casts;
    # otherwise its inferred float16 annotation conflicts with their fixed FLOAT attribute.
    blocked_casts = [node.name for node in model.graph.node if node.op_type == "Cast"]
    converted = float16.convert_float_to_float16(
        model, keep_io_types=True, node_block_list=blocked_casts
    )
    onnx.checker.check_model(converted, full_check=True)
    onnx.save(converted, str(fp16))
    int8 = args.output / "yolo26n-int8.onnx"
    quantize_static(
        str(args.model),
        str(int8),
        Calibration(args.dataset / "images/train", model.graph.input[0].name),
        quant_format=QuantFormat.QDQ,
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
        calibrate_method=CalibrationMethod.MinMax,
        op_types_to_quantize=["Conv", "MatMul"],
    )
    manifest = {
        "source_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "fp16": {
            "sha256": hashlib.sha256(fp16.read_bytes()).hexdigest(),
            "keep_io_types": True,
            "blocked_cast_nodes": blocked_casts,
        },
        "int8": {
            "sha256": hashlib.sha256(int8.read_bytes()).hexdigest(),
            "format": "QDQ",
            "activation": "QInt8",
            "weights": "QInt8",
            "per_channel": True,
            "calibration": "MinMax",
            "train_images": 4,
            "operators": ["Conv", "MatMul"],
        },
        "deployment": "experimental only; no quality acceptance on COCO8",
    }
    (args.output / "precision.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
