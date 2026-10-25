"""Responsibilities: Observe original detector top-k outputs before external confidence filtering.
Implementation: Independent native/E child process with unchanged original inference components.
Related Modules: unified_evaluation.diagnostics/adapters; core.detector; object_distance.predict.
Declaration Index: capture; main.
Variable Index: None. Raw rows are existing e2e top300 proposals, not pre-head dense anchors.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from object_distance.config import digest, write_json

from .adapters import EAdapter, NativeAdapter
from .dataset import safe_path
from .performance import synchronize


def capture(options, output, engine):
    """Functionality: Replay selected RGB frames and export unfiltered existing e2e outputs.
    Inputs: immutable source run/options,owned output file,native/e engine.
    Outputs: identity JSONL.
    Logic: original model/letterbox/executor; independently restore every raw candidate to pixels,
    and also call the unchanged thresholded decoder for exact comparison with saved predictions.
    Constraints: no threshold changes,random weights,depth inference,training or source overwrites.
    """
    import torch

    from core.detector import CONFIDENCE_THRESHOLD, decode_boxes, letterbox
    from object_distance.data import prepare_image
    from object_distance.predict import decode_detections
    from zerogpu.runtime import GraphRunner

    output = Path(output)
    if output.exists():
        raise ValueError("Capture output must be new")
    settings = {**options["configuration"], "method": "b" if engine == "native" else "e"}
    adapter = NativeAdapter(settings) if engine == "native" else EAdapter(settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for frame in options["frames"]:
            with Image.open(safe_path(options["raw_directory"], frame["image_path"])) as original:
                image = original.copy()
            with torch.no_grad():
                if engine == "native":
                    tensor, scale, left, top = letterbox(image, output=adapter.tensor)
                    if adapter.runner is None:
                        adapter.runner = GraphRunner(adapter.model, tensor, {"dtype": "float32"})
                    prediction, _ = adapter.runner(tensor)
                    rows = prediction[0].astype(np.float64)
                    coordinates = (rows[:, :4] - [left, top, left, top]) / scale
                    np.clip(coordinates, 0, [image.width, image.height] * 2, out=coordinates)
                    kept = decode_boxes(prediction, image.size, (scale, left, top), adapter.names,
                                        class_conf={})
                    names = adapter.names
                    transform = {"scale_x": scale, "scale_y": scale, "left": left, "top": top}
                    confidence = CONFIDENCE_THRESHOLD
                else:
                    tensor, inverse = prepare_image(image, adapter.model.config.image_size)
                    with torch.autocast("cuda", enabled=adapter.model.config.amp):
                        prediction, _ = adapter.model.extract(tensor[None].to(adapter.device))
                    rows = prediction[0].detach().float().cpu().numpy().astype(np.float64)
                    coordinates = inverse.to_original(torch.from_numpy(rows[:, :4]).float()).numpy()
                    kept = decode_detections(
                        prediction[0], inverse, adapter.model.names, adapter.model.config.confidence
                    )
                    names = adapter.model.names
                    transform = {
                        "scale_x": inverse.scale_x,
                        "scale_y": inverse.scale_y,
                        "left": inverse.left,
                        "top": inverse.top,
                    }
                    confidence = adapter.model.config.confidence
            synchronize(adapter.device)
            proposals = []
            for index, (row, bbox) in enumerate(zip(rows, coordinates, strict=True)):
                proposals.append(
                    {
                        "raw_index": index,
                        "class_id": int(row[5]),
                        "class_name": names[int(row[5])],
                        "confidence": float(row[4]),
                        "bbox_input": row[:4].tolist(),
                        "bbox": bbox.tolist(),
                        "valid_bbox": bool(bbox[2] > bbox[0] and bbox[3] > bbox[1]),
                    }
                )
            canonical = [
                {
                    "class_name": box.get("class_name", box["label"]),
                    "confidence": box.get("confidence", box["score"]),
                    "bbox": list(box.get("bbox", box["xyxy"])),
                }
                for box in kept
            ]
            record = {
                "frame_id": frame["frame_id"],
                "engine": engine,
                "confidence_threshold": confidence,
                "raw_rows": proposals,
                "postfilter_predictions": canonical,
                "transform": transform,
                "image_size": list(image.size),
                "identity": adapter.identity,
            }
            stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()
    return {"engine": engine, "frames": len(options["frames"]), "output_sha256": digest(output)}


def main(argv=None):
    """Functionality: Run one explicitly selected capture engine in an isolated GPU process.
    Inputs: options JSON,engine,owned output. Outputs: raw replay file and small identity JSON.
    Logic: serial parent scheduling; no package installation or weight downloads.
    Constraints: detector settings originate solely from the original run manifest.
    """
    parser = argparse.ArgumentParser(description="Read-only detector proposal capture")
    parser.add_argument("--options", type=Path, required=True)
    parser.add_argument("--engine", choices=("native", "e"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    options = json.loads(args.options.read_text(encoding="utf-8"))
    result = capture(options, args.output, args.engine)
    write_json(args.output.with_suffix(".summary.json"), result)


if __name__ == "__main__":
    main()
