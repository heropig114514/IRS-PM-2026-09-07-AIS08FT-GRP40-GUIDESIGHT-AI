"""Responsibilities: Build a pinned YOLO26n CPU artifact and its provenance manifest.
Implementation: Download official weights once, export fixed FP32/e2e ONNX, record hashes/classes.
Related Modules: core.detector, models/yolo26n.json; use a separate build environment.
Declaration Index: download — one checked transfer; main — export and manifest generation.
Variable Index: SOURCE_URL — official checkpoint; BUILD_VERSION — required Ultralytics release.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

SOURCE_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"
BUILD_VERSION = "8.4.171"


def download(url, path):
    """Functionality: Save a public build artifact only when absent.
    Inputs: fixed url and output Path; Outputs: local bytes, exceptions terminate the build.
    Logic: one HTTPS transfer; Constraints: no silent retry or alternative weights.
    """
    if not path.exists():
        with urlopen(url, timeout=60) as response:
            path.write_bytes(response.read())


def main():
    """Functionality: Export official pretrained COCO weights, without training or quantization.
    Inputs: --output directory, fixed build dependencies; Outputs: PT/ONNX/JSON files.
    Logic: enforce version, CPU FP32 640 input, e2e head, 300 predictions and opset 17.
    Constraints: model binary stays outside Git; export is not accuracy or camera validation.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = output / ".build-config"
    config.mkdir(exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config)
    os.environ["YOLO_AUTOINSTALL"] = "False"

    import onnx
    import onnxruntime
    import torch
    import ultralytics
    from ultralytics import YOLO

    if ultralytics.__version__ != BUILD_VERSION:
        raise RuntimeError("Use the pinned Ultralytics build version")
    weight = output / "yolo26n.pt"
    download(SOURCE_URL, weight)
    model = YOLO(str(weight))
    exported = Path(
        model.export(
            format="onnx",
            imgsz=640,
            batch=1,
            dynamic=False,
            simplify=False,
            opset=17,
            nms=False,
            max_det=300,
            device="cpu",
            quantize=32,
        )
    )
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        str(exported), options, providers=["CPUExecutionProvider"]
    )
    if session.get_inputs()[0].shape != [1, 3, 640, 640]:
        raise RuntimeError("Unexpected model input")
    if session.get_outputs()[0].shape != [1, 300, 6]:
        raise RuntimeError("Unexpected e2e model output")
    manifest = {
        "model": "yolo26n",
        "task": "detect",
        "dataset": "COCO",
        "license": "AGPL-3.0",
        "source_url": SOURCE_URL,
        "source_sha256": hashlib.sha256(weight.read_bytes()).hexdigest(),
        "onnx_sha256": hashlib.sha256(exported.read_bytes()).hexdigest(),
        "build": {
            "ultralytics": ultralytics.__version__,
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "opset": 17,
            "nms": False,
            "precision": "FP32",
        },
        "input_shape": [1, 3, 640, 640],
        "output_shape": [1, 300, 6],
        "names": [model.names[index] for index in range(80)],
    }
    (output / "yolo26n.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"onnx_bytes": exported.stat().st_size, "sha256": manifest["onnx_sha256"]}))


if __name__ == "__main__":
    main()
