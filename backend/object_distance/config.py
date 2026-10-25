"""Responsibilities: Validate reproducible, low-memory scheme E experiment settings.
Implementation: Frozen dataclass, explicit JSON/CLI overrides, backend-relative configured paths.
Related Modules: object_distance.prepare/data/model/train/evaluate/predict.
Declaration Index: ExperimentConfig; ExperimentConfig.__post_init__; read_config; digest;
write_json; seed_runtime.
Variable Index: ROOT — backend directory; EXPECTED_VERSION — inspected Ultralytics ABI;
TARGET — KITTI reference-point camera-axis z, not nearest obstacle surface range.
"""

import hashlib
import json
import math
import random
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "8.4.171"
TARGET = "kitti_camera_reference_z_m"


@dataclass(frozen=True)
class ExperimentConfig:
    """Functionality: Describe one isolated E experiment and its low-memory defaults.
    Logic: defaults freeze all YOLO parameters; partial feature tuning requires explicit settings.
    Constraints: checkpoint loading never silently substitutes weights, classes or architecture.
    """

    class_agnostic: bool = False
    distance_target: str = TARGET
    detection_weights: str = "models/yolo26n.pt"
    processed: str = "model_tests/datasets/kitti/processed-80-20-seed42"
    freeze_backbone: bool = True
    unfreeze_last_layers: int = 0
    learning_rate: float = 0.001
    batch_size: int = 2
    epochs: int = 10
    image_size: int = 640
    device: str = "cuda:0"
    seed: int = 42
    amp: bool = True
    workers: int = 0
    roi_size: int = 3
    hidden_dim: int = 128
    initial_distance_m: float = 20.0
    confidence: float = 0.25
    match_iou: float = 0.5
    supported_classes: tuple = ("car", "person")
    distance_bins_m: tuple = (0, 10, 20, 40, 80)
    cpu_threads: int = 1

    def __post_init__(self):
        """Functionality: Fail before expensive model/data work on invalid parameters.
        Inputs: dataclass fields. Outputs: normalized immutable config or ValueError.
        Logic: positive sizes/rates, exact boolean/count types and ordered bins.
        Constraints: no automatic batch reduction, image resizing policy change or device fallback.
        """
        if self.distance_target not in {TARGET, "sanpo_visible_surface_median_m"}:
            raise ValueError("Unsupported distance target")
        for name in ("batch_size", "epochs", "image_size", "roi_size", "hidden_dim", "cpu_threads"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("unfreeze_last_layers", "workers", "seed"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name in ("freeze_backbone", "amp"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for name in ("learning_rate", "initial_distance_m", "confidence", "match_iou"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.image_size % 32 or self.confidence > 1 or self.match_iou > 1:
            raise ValueError("Use image_size divisible by32 and probability thresholds <=1")
        if not self.class_agnostic and tuple(self.supported_classes) != ("car", "person"):
            raise ValueError("First-version distance classes must be car, person in that order")
        bins = tuple(self.distance_bins_m)
        if (
            not bins
            or bins[0] != 0
            or any(not math.isfinite(v) for v in bins)
            or any(b <= a for a, b in zip(bins, bins[1:], strict=False))
        ):
            raise ValueError("Distance bins must increase from zero")
        object.__setattr__(self, "supported_classes", tuple(self.supported_classes))
        object.__setattr__(self, "distance_bins_m", bins)


def read_config(path=None, **overrides):
    """Functionality: Resolve explicit experiment configuration once.
    Inputs: optional JSON path, non-None CLI overrides. Outputs: ExperimentConfig.
    Logic: defaults < JSON < CLI; resolve weight/data paths against backend.
    Constraints: unknown JSON keys fail; production distance configuration is never modified.
    """
    values = {} if path is None else json.loads(Path(path).read_text(encoding="utf-8"))
    config = replace(
        ExperimentConfig(**values), **{k: v for k, v in overrides.items() if v is not None}
    )
    return replace(
        config,
        detection_weights=str((ROOT / config.detection_weights).resolve()),
        processed=str((ROOT / config.processed).resolve()),
    )


def digest(path):
    """Functionality: Identify local weights/manifests without loading their whole file.
    Inputs: existing path. Outputs: SHA256. Logic: streamed1MiB chunks.
    Constraints: no downloads, retries or substitutions.
    """
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_json(path, value):
    """Functionality: Write a finite structured experiment artifact.
    Inputs: owned path and JSON-compatible value. Outputs: UTF8 JSON file.
    Logic: strict NaN rejection. Constraints: callers own output directory/overwrite policy.
    """
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def seed_runtime(config):
    """Functionality: Seed experiment RNGs and fix CPU/CUDA compute settings.
    Inputs: ExperimentConfig. Outputs: None. Logic: seed Python/NumPy/Torch, disable TF32/benchmark.
    Constraints: CUDA ROI backward may be nondeterministic during optional partial fine-tuning;
    seeds and split identities are recorded instead of promising bitwise cross-device equality.
    """
    import numpy as np
    import torch

    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.cpu_threads)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
