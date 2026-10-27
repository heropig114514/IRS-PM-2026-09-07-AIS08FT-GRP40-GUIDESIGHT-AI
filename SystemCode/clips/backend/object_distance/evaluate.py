"""Responsibilities: Separate GT-ROI and end-to-end predicted-ROI camera-z evaluation.
Implementation: Fixed split/hash, one shared forward per frame, explicit IoU correspondence,
cold/warm timing groups, fixed four-band weighted reports and external prediction comparison.
Related Modules: object_distance.data/model/metrics/predict/reporting; A/B/C are not invoked.
Declaration Index: score_frame; evaluate_mode; evaluate_external; main.
Variable Index: logger — bounded evaluation progress; this is not official KITTI detection AP.
"""

import argparse
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

import torch

from object_distance.config import digest, seed_runtime, write_json
from object_distance.data import KittiObjects
from object_distance.metrics import DistanceMetrics, match_objects
from object_distance.model import load_checkpoint
from object_distance.predict import latency_summary, run_frame, synchronize
from object_distance.reporting import distance_report, write_distance_report

logger = logging.getLogger(__name__)


def score_frame(metrics, ground_truth, predictions, mode, match_iou):
    """Functionality: Score every accepted GT object and report correspondence coverage.
    Inputs: metrics collector, GT/predictions, gt/predicted mode and IoU threshold.
    Outputs: matched count and unmatched prediction count. Logic: GT uses aligned index; predicted
    uses class/IoU one-to-one matches, including missed detections in missing-distance denominator.
    Constraints: regression errors are conditional on a valid matched distance, never all-target
    MAE.
    """
    if mode == "gt":
        if len(ground_truth) != len(predictions):
            raise ValueError("GT ROI results must preserve every object slot")
        matches = dict(enumerate(range(len(ground_truth))))
    else:
        matches = match_objects(ground_truth, predictions, match_iou)
    for index, gt in enumerate(ground_truth):
        prediction = predictions[matches[index]]["distance_m"] if index in matches else None
        metrics.add(gt["distance_m"], prediction, gt["class_name"])
    return len(matches), len(predictions) - len(matches)


def evaluate_mode(model, dataset, output, *, mode="predicted", warmup=3):
    """Functionality: Evaluate a full selected split with GT or actual predicted ROIs.
    Inputs: E model/dataset, owned output directory, mode and explicit warmup count.
    Outputs: distance/timing/memory report plus per-image compatible prediction JSONL.
    Logic: separate first-call probe/warmups from scored steady iterations; modes execute as
    separate passes so GT head work never contaminates predicted-system latency.
    Constraints: first-call-for-mode is not necessarily process-cold when both modes run together;
    latency includes RGB decode/preparation but excludes metric matching, output I/O and rendering.
    """
    if mode not in {"gt", "predicted"} or warmup < 0 or not len(dataset):
        raise ValueError("Invalid evaluation mode/warmup or empty dataset")
    model.eval()
    metrics = DistanceMetrics(model.config.distance_bins_m)
    latencies, matched, unmatched = [], 0, 0
    first_call = None
    with (Path(output) / f"{mode}_predictions.jsonl").open("w", encoding="utf-8") as records:
        for iteration in range(-warmup - 1, len(dataset)):
            synchronize(model.config.device)
            started = time.perf_counter()
            index = 0 if iteration < 0 else iteration
            sample = dataset[index]
            options = (
                {"gt_boxes": sample["original_boxes"], "gt_classes": sample["classes"]}
                if mode == "gt"
                else {}
            )
            predictions, latency = run_frame(
                model, sample["image"], sample["transform"], started=started, **options
            )
            if iteration == -warmup - 1:
                first_call = latency
            if iteration < 0:
                if iteration == -1 and torch.device(model.config.device).type == "cuda":
                    torch.cuda.reset_peak_memory_stats(model.config.device)
                continue
            ground_truth = dataset.records[index]["objects"]
            matches, unused = score_frame(
                metrics, ground_truth, predictions, mode, model.config.match_iou
            )
            matched, unmatched = matched + matches, unmatched + unused
            latencies.append(latency)
            if iteration % 250 == 0:
                logger.info(
                    "E evaluation mode=%s images=%d/%d matched=%d",
                    mode,
                    iteration + 1,
                    len(dataset),
                    matched,
                )
            records.write(
                json.dumps(
                    {
                        "image_id": sample["image_id"],
                        "distance_target": dataset.distance_target,
                        "objects": predictions,
                        "timings": latency,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
    report = {
        "schema_version": 1,
        "bbox_evaluation": mode,
        "distance_target": dataset.distance_target,
        "split": dataset.split,
        "split_manifest_sha256": dataset.fingerprint,
        "evaluated_images": len(dataset),
        "metrics": metrics.summary(),
        "distance_report": distance_report(metrics),
        "matched_ground_truth": matched,
        "unmatched_predictions": unmatched,
        "matching": "aligned_GT_slots" if mode == "gt" else "same_class_highest_IoU_first",
        "match_iou": model.config.match_iou,
        "confidence": model.config.confidence,
        "parameters": model.parameter_counts(),
        "config": asdict(model.config),
        "evaluation_batch_size": 1,
        "preprocessing": "original_RGB_bilinear_letterbox114_FP32",
        "torch": str(torch.__version__),
        "first_call_for_mode": first_call,
        "warmup_calls": warmup,
        "steady_latency": latency_summary(latencies),
        "peak_gpu_memory_bytes": torch.cuda.max_memory_allocated(model.config.device)
        if torch.device(model.config.device).type == "cuda"
        else None,
        "detector_sha256": model.weight_sha256,
    }
    write_json(Path(output) / f"{mode}_metrics.json", report)
    write_distance_report(output, mode, report)
    return report


def evaluate_external(dataset, predictions_path, *, match_iou=0.5):
    """Functionality: Score external A/B/C/E canonical predictions under the same GT/matching API.
    Inputs: fixed dataset, JSONL image_id/objects/distance_target and IoU threshold.
    Outputs: comparable errors/coverage without invented inference timing.
    Logic: reject duplicate/foreign IDs and incompatible target semantics; missing image results
    count all corresponding GT objects as missing. Constraints: A/B/C surface/size definitions
    must be calibrated to reference z first; relabeling a file alone does not establish equivalence.
    """
    records = {}
    allowed = {record["image_id"] for record in dataset.records}
    for line in Path(predictions_path).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if (
            record.get("distance_target") != dataset.distance_target
            or record.get("image_id") not in allowed
            or record["image_id"] in records
        ):
            raise ValueError("External predictions have incompatible target/duplicate/foreign IDs")
        records[record["image_id"]] = record["objects"]
    metrics = DistanceMetrics()
    for record in dataset.records:
        score_frame(
            metrics, record["objects"], records.get(record["image_id"], []), "predicted", match_iou
        )
    return {
        "distance_target": dataset.distance_target,
        "split": dataset.split,
        "split_manifest_sha256": dataset.fingerprint,
        "input_sha256": digest(predictions_path),
        "evaluated_images": len(dataset),
        "metrics": metrics.summary(),
        "distance_report": distance_report(metrics),
        "match_iou": match_iou,
    }


def main(argv=None):
    """Functionality: Run explicit GT/predicted evaluation or target-checked external scoring.
    Inputs: checkpoint or external predictions, processed split, limits and new output directory.
    Outputs: JSON reports and prediction files. Logic: preserve each evaluation mode separately.
    Constraints: image limits are reported; test split without labels is never evaluated as GT.
    """
    parser = argparse.ArgumentParser(description="Evaluate E reference-z errors and coverage")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--checkpoint", type=Path)
    inputs.add_argument("--predictions", type=Path)
    parser.add_argument("--processed", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--detection-weights", type=Path)
    parser.add_argument("--device")
    parser.add_argument("--bbox-mode", choices=("gt", "predicted", "both"), default="both")
    parser.add_argument("--split", choices=("train", "val", "test"), default="val")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.output.exists():
        parser.error("Choose a new output directory")
    if args.predictions:
        if args.processed is None:
            parser.error("External scoring requires --processed")
        dataset = KittiObjects(args.processed, args.split, limit=args.limit)
        report = evaluate_external(dataset, args.predictions)
        args.output.mkdir(parents=True)
        write_json(args.output / "external_metrics.json", report)
        write_distance_report(args.output, "external", report)
        return
    model, payload = load_checkpoint(
        args.checkpoint, detection_weights=args.detection_weights, device=args.device
    )
    seed_runtime(model.config)
    dataset = KittiObjects(
        args.processed or model.config.processed,
        args.split,
        model.config.image_size,
        args.limit,
        model.config.distance_target,
        model.config.supported_classes,
    )
    args.output.mkdir(parents=True)
    modes = ("gt", "predicted") if args.bbox_mode == "both" else (args.bbox_mode,)
    for mode in modes:
        report = evaluate_mode(model, dataset, args.output, mode=mode, warmup=args.warmup)
        report.update(
            checkpoint_sha256=digest(args.checkpoint),
            checkpoint_training=payload["extra"],
            image_limit=args.limit,
        )
        write_json(args.output / f"{mode}_metrics.json", report)
        print(
            json.dumps(
                {
                    "mode": mode,
                    **report["metrics"]["overall"],
                    "weighted_mae_m": report["distance_report"]["weighted_mae_m"],
                },
                allow_nan=False,
            )
        )


if __name__ == "__main__":
    main()
