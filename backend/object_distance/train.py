"""Responsibilities: Train scheme E under explicit low-memory and reproducibility conditions.
Implementation: Frozen eval-mode YOLO, GT ROIs, Smooth L1, AMP, counted steps and held-out GT
errors.
Related Modules: object_distance.config/data/model/metrics; checkpoint artifacts stay Git-ignored.
Declaration Index: detector_state_digest; validate_head; train; main.
Variable Index: logger — loss/validation/progress diagnostics; checkpoint/result paths are
run-owned.
"""

import argparse
import csv
import hashlib
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

import torch
import torch.nn.functional as functional
from torch.utils.data import DataLoader

from object_distance.config import ROOT, digest, read_config, seed_runtime, write_json
from object_distance.data import KittiObjects, collate_objects
from object_distance.metrics import DistanceMetrics
from object_distance.model import ObjectDistanceModel, save_checkpoint

logger = logging.getLogger(__name__)


def detector_state_digest(model):
    """Functionality: Audit frozen detector parameters and buffers across actual training.
    Inputs: E model. Outputs: SHA256 of all detector tensor values.
    Logic: ordered key/CPU tensor bytes. Constraints: excludes plain inference anchor caches.
    """
    hasher = hashlib.sha256()
    for name, tensor in model.detector.state_dict().items():
        hasher.update(name.encode())
        hasher.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return hasher.hexdigest()


@torch.no_grad()
def validate_head(model, loader):
    """Functionality: Evaluate distance regression on held-out GT boxes, without claiming detection
    AP.
    Inputs: E model and deterministic validation loader. Outputs: overall and subgroup metrics.
    Logic: one shared forward per batch, count every valid GT object including missing predictions.
    Constraints: no parameter/BN updates; predicted-box evaluation is a separate tool/mode.
    """
    model.eval()
    metrics = DistanceMetrics(model.config.distance_bins_m)
    for batch in loader:
        with torch.autocast(
            torch.device(model.config.device).type,
            enabled=model.config.amp and torch.device(model.config.device).type == "cuda",
        ):
            _, prediction = model(
                batch["images"].to(model.config.device),
                batch["rois"].to(model.config.device),
                batch["classes"].to(model.config.device),
            )
        for target, value, category in zip(
            batch["distances"].tolist(),
            prediction.cpu().tolist(),
            batch["classes"].tolist(),
            strict=True,
        ):
            metrics.add(target, value, model.config.supported_classes[category])
    return metrics.summary()


def train(
    config, output, *, train_limit=None, val_limit=None, max_steps=None, init_checkpoint=None
):
    """Functionality: Train only selected E parameters and preserve a reviewable complete run.
    Inputs: config, new run directory, explicit smoke limits/step cap and optional compatible init.
    Outputs: run report, per-step JSONL/epoch CSV, best.pt and last.pt.
    Logic: seed before initialization, shuffle train with a dedicated generator; GT validation
    selects best MAE; optimizer excludes every frozen detector/head parameter.
    Constraints: no hidden OOM retry/batch/seed changes; exceptions retain a failed run record.
    Optional feature tuning is distance-supervised only, so original detection quality must be
    re-evaluated; default frozen YOLO is checked byte-for-byte by tensor fingerprint.
    """
    output = Path(output).resolve()
    if output.exists() or (max_steps is not None and max_steps < 1):
        raise ValueError("Choose a new output and positive max_steps")
    seed_runtime(config)
    training = KittiObjects(
        config.processed,
        "train",
        config.image_size,
        train_limit,
        config.distance_target,
        config.supported_classes,
    )
    validation = KittiObjects(
        config.processed,
        "val",
        config.image_size,
        val_limit,
        config.distance_target,
        config.supported_classes,
    )
    if not any(record["objects"] for record in training.records):
        raise ValueError("Selected training subset contains no supported targets")
    generator = torch.Generator().manual_seed(config.seed)
    train_loader = DataLoader(
        training,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=config.workers,
        collate_fn=collate_objects,
        generator=generator,
    )
    val_loader = DataLoader(
        validation,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.workers,
        collate_fn=collate_objects,
    )
    model = ObjectDistanceModel(config)
    if init_checkpoint is not None:
        payload = torch.load(init_checkpoint, map_location="cpu", weights_only=True)
        if (
            payload.get("distance_target") != config.distance_target
            or payload.get("detector_sha256") != model.weight_sha256
            or payload.get("architecture") != model.signature
        ):
            raise ValueError(
                "Initial checkpoint target/detector ABI differs; retrain from new weights"
            )
        model.load_state_dict(payload["model"], strict=True)
    initial_digest = detector_state_digest(model)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=config.learning_rate,
        weight_decay=0.01,
    )
    scaler = torch.amp.GradScaler(
        "cuda", enabled=config.amp and torch.device(config.device).type == "cuda"
    )
    output.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "status": "running",
        "distance_target": config.distance_target,
        "config": asdict(config),
        "parameters": model.parameter_counts(),
        "detector_sha256": model.weight_sha256,
        "feature_sources": model.feature_sources,
        "feature_channels": model.channels,
        "architecture": model.signature,
        "dataset": {
            "train_images": len(training),
            "val_images": len(validation),
            "train_objects": sum(len(r["objects"]) for r in training.records),
            "val_objects": sum(len(r["objects"]) for r in validation.records),
            "train_manifest_sha256": training.fingerprint,
            "val_manifest_sha256": validation.fingerprint,
        },
        "subset_limits": {"train": train_limit, "val": val_limit, "max_steps": max_steps},
        "optimizer": {"type": "AdamW", "weight_decay": 0.01},
        "loss": {"type": "SmoothL1Loss", "beta_m": 1.0},
        "initial_checkpoint_sha256": digest(init_checkpoint) if init_checkpoint else None,
        "torch": str(torch.__version__),
        "device": config.device,
        "steps": 0,
        "detector_initial_state_sha256": initial_digest,
        "scope": "GT bbox supervised regression; not full-system or nearest-surface accuracy",
    }
    if torch.device(config.device).type == "cuda":
        report["gpu"] = torch.cuda.get_device_name(config.device)
        torch.cuda.reset_peak_memory_stats(config.device)
    write_json(output / "run.json", report)
    started, best, step, epoch = time.perf_counter(), float("inf"), 0, 0
    optimizer_updates = 0
    try:
        initial_metrics = validate_head(model, val_loader)
        report["initial_gt_validation"] = initial_metrics
        logger.info("Initial validation MAE_m=%s", initial_metrics["overall"]["mae_m"])
        with (
            (output / "history.jsonl").open("w", encoding="utf-8") as history,
            (output / "loss.csv").open("w", newline="", encoding="utf-8") as csv_file,
        ):
            writer = csv.DictWriter(
                csv_file, fieldnames=["epoch", "steps", "train_loss_m", "val_mae_m", "val_rmse_m"]
            )
            writer.writeheader()
            for epoch in range(1, config.epochs + 1):
                model.train()
                loss_sum, objects = 0.0, 0
                for batch in train_loader:
                    if max_steps is not None and step >= max_steps:
                        break
                    if not len(batch["distances"]):
                        continue
                    optimizer.zero_grad(set_to_none=True)
                    with torch.autocast(
                        torch.device(config.device).type, enabled=scaler.is_enabled()
                    ):
                        _, prediction = model(
                            batch["images"].to(config.device),
                            batch["rois"].to(config.device),
                            batch["classes"].to(config.device),
                        )
                        loss = functional.smooth_l1_loss(
                            prediction.float(), batch["distances"].to(config.device)
                        )
                    if not torch.isfinite(loss):
                        raise ValueError(
                            "Nonfinite training loss; no parameter substitution attempted"
                        )
                    scaler.scale(loss).backward()
                    if not any(
                        p.grad is not None
                        and torch.isfinite(p.grad).all()
                        and p.grad.abs().sum() > 0
                        for p in model.distance_head.parameters()
                    ):
                        raise ValueError("Distance Head has no finite nonzero gradient")
                    previous_scale = scaler.get_scale()
                    scaler.step(optimizer)
                    scaler.update()
                    updated = scaler.get_scale() >= previous_scale
                    optimizer_updates += int(updated)
                    step += 1
                    count, value = len(prediction), float(loss.detach())
                    loss_sum, objects = loss_sum + value * count, objects + count
                    history.write(
                        json.dumps(
                            {
                                "event": "step",
                                "step": step,
                                "epoch": epoch,
                                "loss_m": value,
                                "objects": count,
                                "optimizer_updated": updated,
                                "amp_scale": scaler.get_scale(),
                            }
                        )
                        + "\n"
                    )
                    history.flush()
                if objects == 0:
                    break
                metrics = validate_head(model, val_loader)
                mae = metrics["overall"]["mae_m"]
                if mae is None:
                    raise ValueError("Validation has no valid distance predictions")
                record = {
                    "epoch": epoch,
                    "steps": step,
                    "train_loss_m": loss_sum / objects,
                    "val_mae_m": mae,
                    "val_rmse_m": metrics["overall"]["rmse_m"],
                }
                writer.writerow(record)
                csv_file.flush()
                history.write(json.dumps({"event": "epoch", **record}) + "\n")
                history.flush()
                logger.info(
                    "Epoch=%d steps=%d loss=%.4f val_MAE_m=%.4f",
                    epoch,
                    step,
                    record["train_loss_m"],
                    mae,
                )
                save_checkpoint(
                    output / "last.pt",
                    model,
                    epoch=epoch,
                    optimizer=optimizer,
                    extra={"validation": metrics, "steps": step},
                )
                if mae < best:
                    best = mae
                    save_checkpoint(
                        output / "best.pt",
                        model,
                        epoch=epoch,
                        optimizer=optimizer,
                        extra={"validation": metrics, "steps": step},
                    )
                report.update(
                    steps=step,
                    epochs_completed=epoch,
                    best_gt_val_mae_m=best,
                    final_gt_validation=metrics,
                    optimizer_updates=optimizer_updates,
                    amp_overflow_skipped_updates=step - optimizer_updates,
                )
                write_json(output / "run.json", report)
                if max_steps is not None and step >= max_steps:
                    break
        if optimizer_updates == 0:
            raise ValueError("No optimization step was executed")
        final_digest = detector_state_digest(model)
        report.update(
            detector_final_state_sha256=final_digest,
            detector_state_unchanged=initial_digest == final_digest,
        )
        if model.frozen and initial_digest != final_digest:
            raise ValueError("Frozen detector state unexpectedly changed")
        report.update(
            status="complete",
            elapsed_s=time.perf_counter() - started,
            peak_gpu_memory_bytes=torch.cuda.max_memory_allocated(config.device)
            if torch.device(config.device).type == "cuda"
            else None,
        )
    except (Exception, KeyboardInterrupt) as error:
        report.update(
            status="failed",
            steps=step,
            optimizer_updates=optimizer_updates,
            error_type=type(error).__name__,
        )
        raise
    finally:
        write_json(output / "run.json", report)
    return report


def main(argv=None):
    """Functionality: Expose explicitly configurable Windows-safe training from the terminal.
    Inputs: JSON/CLI settings, output and optional smoke limits. Outputs: structured training
    report.
    Logic: CLI overrides only provided values. Constraints: workers default0; no auto OOM retries.
    """
    parser = argparse.ArgumentParser(description="Train E from GT boxes and KITTI camera-z labels")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--init-checkpoint", type=Path)
    parser.add_argument("--detection-weights")
    parser.add_argument("--processed")
    parser.add_argument("--device")
    for name in ("batch-size", "epochs", "image-size", "seed", "workers", "unfreeze-last-layers"):
        parser.add_argument(f"--{name}", type=int)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--freeze-backbone", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=None)
    for name in ("train-limit", "val-limit", "max-steps"):
        parser.add_argument(f"--{name}", type=int)
    args = parser.parse_args(argv)
    excluded = {"config", "output", "train_limit", "val_limit", "max_steps", "init_checkpoint"}
    config = read_config(args.config, **{k: v for k, v in vars(args).items() if k not in excluded})
    logging.basicConfig(level=logging.INFO)
    report = train(
        config,
        ROOT / args.output,
        train_limit=args.train_limit,
        val_limit=args.val_limit,
        max_steps=args.max_steps,
        init_checkpoint=args.init_checkpoint,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "steps": report["steps"],
                "best_gt_val_mae_m": report["best_gt_val_mae_m"],
            }
        )
    )


if __name__ == "__main__":
    main()
