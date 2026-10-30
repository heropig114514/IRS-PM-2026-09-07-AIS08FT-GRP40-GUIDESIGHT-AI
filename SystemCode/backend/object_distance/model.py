"""Responsibilities: Reuse one YOLO26 forward for unchanged detections and metric ROI regression.
Implementation: Pinned Detect-input hook, multiscale ROI Align, two-layer MLP and strict
checkpoints.
Related Modules: object_distance.config/data/train/evaluate/predict; production runtime is
untouched.
Declaration Index: architecture_signature; DistanceHead; DistanceHead.__init__;
DistanceHead.forward;
ObjectDistanceModel; ObjectDistanceModel.__init__; ObjectDistanceModel.train;
ObjectDistanceModel.capture_features; ObjectDistanceModel.extract; ObjectDistanceModel.regress;
ObjectDistanceModel.forward; ObjectDistanceModel.parameter_counts; save_checkpoint; load_checkpoint.
Variable Index: logger — safe model/feature identities; contract — inspected YOLO26n graph/shape
ABI;
config — explicit detector weight identity; cached_features — current-call tensors only.
"""

import hashlib
import json
import logging
from contextlib import nullcontext
from dataclasses import asdict, replace
from pathlib import Path

import torch
import torch.nn.functional as functional
from torch import nn
from torchvision.ops import roi_align

from object_distance.config import EXPECTED_VERSION, ROOT, ExperimentConfig, digest

logger = logging.getLogger(__name__)


def architecture_signature(model):
    """Functionality: Identify graph routing/module types and every parameter/buffer shape.
    Inputs: loaded DetectionModel. Outputs: SHA256. Logic: canonical JSON without weight values.
    Constraints: weight changes are permitted only when graph/class/shape ABI remains compatible.
    """
    structure = {
        "layers": [(type(m).__name__, m.f) for m in model.model],
        "state": [(key, list(value.shape)) for key, value in model.state_dict().items()],
    }
    return hashlib.sha256(json.dumps(structure, separators=(",", ":")).encode()).hexdigest()


class DistanceHead(nn.Module):
    """Functionality: Regress positive camera-z meters from multiscale object features.
    Logic: flattened ROI features + six normalized box coordinates/sizes.
    Class indicators exist only for legacy checkpoints; agnostic mode never reads class values.
    Linear/ReLU/Linear/FP32 Softplus. Constraints: no secondary detector or depth model.
    """

    def __init__(self, channels, config):
        """Functionality: Construct a lightweight independent trainable head.
        Inputs: inspected feature channel widths and experiment config. Outputs: initialized MLP.
        Logic: positive configurable initial-distance bias gives finite meter-scale starting output.
        Constraints: all widths, ROI size and initial bias are recorded in checkpoints.
        """
        super().__init__()
        width = (
            sum(channels) * config.roi_size**2
            + 6
            + (0 if config.class_agnostic else len(config.supported_classes))
        )
        self.layers = nn.Sequential(
            nn.Linear(width, config.hidden_dim), nn.ReLU(), nn.Linear(config.hidden_dim, 1)
        )
        nn.init.constant_(self.layers[-1].bias, config.initial_distance_m)

    def forward(self, values):
        """Functionality: Produce one strictly positive scalar for each valid ROI.
        Inputs: NxD concatenated features. Outputs: N FP32 meters.
        Logic: numerically stable FP32 Softplus plus small positive epsilon.
        Constraints: nonfinite training/inference outputs are rejected by callers, not substituted.
        """
        return functional.softplus(self.layers(values).float().squeeze(-1)) + 1e-4


class ObjectDistanceModel(nn.Module):
    """Functionality: Preserve YOLO's detection head while sharing its actual multiscale inputs.
    Logic: capture a single detector forward's Detect inputs using a pre-hook; regress ROIs after
    it.
    Constraints: pinned YOLO26n ABI/COCO order; serial calls; independent from GraphRunner and
    Depth.
    """

    def __init__(self, config):
        """Functionality: Load explicit local detector weights and validate their architecture.
        Inputs: ExperimentConfig. Outputs: detector, independent distance head and freeze policy.
        Logic: use current local Ultralytics SDK only; enforce graph signature before execution;
        derive feature sources from actual Detect.f and configured inspected channels.
        Constraints: no automatic download or partial loading; incompatible teammate weights fail.
        Detection modules stay eval with immutable BN statistics even during partial feature tuning.
        """
        super().__init__()
        import ultralytics
        from ultralytics import YOLO

        path = Path(config.detection_weights)
        if not path.is_file() or path.suffix != ".pt":
            raise ValueError("Provide an existing local YOLO26n detection .pt checkpoint")
        if ultralytics.__version__ != EXPECTED_VERSION:
            raise ValueError(f"Scheme E requires inspected Ultralytics {EXPECTED_VERSION}")
        if torch.device(config.device).type == "cuda" and not torch.cuda.is_available():
            raise ValueError(
                "Requested CUDA device is unavailable; select CPU explicitly if needed"
            )
        self.config, self.weight_sha256 = config, digest(path)
        self.detector = YOLO(str(path), task="detect").model.float().eval()
        manifest = json.loads((ROOT / "models/yolo26n.json").read_text(encoding="utf-8"))
        contract = json.loads(Path(__file__).with_name("yolo26n_contract.json").read_text())
        head = self.detector.model[-1]
        self.names = [self.detector.names[i] for i in range(len(self.detector.names))]
        self.signature = architecture_signature(self.detector)
        if (
            type(self.detector).__name__ != "DetectionModel"
            or type(head).__name__ != "Detect"
            or self.names != manifest["names"]
            or self.signature != contract["signature"]
            or head.nc != 80
            or head.max_det != 300
            or head.reg_max != 1
            or head.stride.tolist() != contract["feature_strides"]
            or getattr(head, "one2one_cv2", None) is None
        ):
            raise ValueError(
                "Incompatible detector architecture/classes; require matching YOLO26n ABI"
            )
        # This same explicit e2e selection is used by the original native loader.
        self.detector.end2end = True
        head.export, head.format, head.dynamic = True, "onnx", False
        self.channels = tuple(contract["feature_channels"])
        self.feature_sources = tuple(head.f)
        self.cached_features = None
        self.frozen = config.freeze_backbone and config.unfreeze_last_layers == 0
        if config.unfreeze_last_layers > len(self.detector.model) - 1:
            raise ValueError("unfreeze_last_layers exceeds feature graph length")
        for parameter in self.detector.parameters():
            parameter.requires_grad_(False)
        feature_layers = list(self.detector.model)[:-1]
        selected = (
            feature_layers[-config.unfreeze_last_layers :]
            if config.unfreeze_last_layers
            else feature_layers
            if not config.freeze_backbone
            else []
        )
        for module in selected:
            for parameter in module.parameters():
                parameter.requires_grad_(True)
        self.distance_head = DistanceHead(self.channels, config)
        self.to(config.device)
        logger.info(
            "E initialized sources=%s channels=%s frozen=%s sha256=%s",
            self.feature_sources,
            self.channels,
            self.frozen,
            self.weight_sha256,
        )

    def train(self, mode=True):
        """Functionality: Train only selected parameters without changing detection/BN behavior.
        Inputs: training bool. Outputs: self. Logic: force detector eval after recursive train call.
        Constraints: optional feature fine-tuning uses distance loss only and may change detections;
        default fully frozen detector retains all original parameter/buffer values.
        """
        super().train(mode)
        self.detector.eval()
        return self

    def capture_features(self, module, args):
        """Functionality: Capture actual tensors passed into this model's Detect head.
        Inputs: pre-forward hook module/arguments. Outputs: current-call feature tuple side effect.
        Logic: retain tensors without detach so optional feature tuning remains differentiable.
        Constraints: does not run or duplicate any layer; hook is removed after each extract call.
        """
        self.cached_features = tuple(args[0])

    def extract(self, images):
        """Functionality: Run one shared backbone/neck/detection forward and return its ROI
        features.
        Inputs: BCHW image tensor. Outputs: Bx300x6 e2e predictions and feature tuple.
        Logic: scoped pre-hook, no_grad only when all YOLO parameters are frozen, strict shapes.
        Constraints: no inference_mode tensors during head training; no full second YOLO execution.
        """
        self.cached_features = None
        hook = self.detector.model[-1].register_forward_pre_hook(self.capture_features)
        try:
            with torch.no_grad() if self.frozen else nullcontext():
                prediction = self.detector(images)
            features = self.cached_features
        finally:
            hook.remove()
            self.cached_features = None
        if not isinstance(prediction, torch.Tensor) or prediction.shape != (
            images.shape[0],
            300,
            6,
        ):
            raise ValueError("Expected YOLO26 e2e [batch,300,6] detection output")
        if features is None or tuple(f.shape[1] for f in features) != self.channels:
            raise ValueError("Unexpected Detect-input feature shapes")
        return prediction, features

    def regress(self, features, rois, classes, input_size):
        """Functionality: Predict ROI-aligned distances while leaving invalid/unsupported ROIs
        absent.
        Inputs: three feature maps, Nx5 [batch,xyxy] ROIs, N head class IDs, input(H,W).
        Outputs: N float distances with internal NaN sentinels for unavailable slots.
        Logic: validate coordinates and sample aligned ROI features at measured spatial scale.
        Agnostic heads concatenate only ROI features and bbox geometry; legacy heads add classes.
        Constraints: sentinels become JSON null at the boundary; valid predictions must be finite.
        """
        if rois.ndim != 2 or rois.shape[1] != 5 or classes.shape != (len(rois),):
            raise ValueError("Expected Nx5 ROIs and N distance-class IDs")
        height, width = input_size
        rois = rois.float()
        valid = (
            torch.isfinite(rois).all(1)
            & (rois[:, 0] >= 0)
            & (rois[:, 0] < features[0].shape[0])
            & (rois[:, 0] == rois[:, 0].trunc())
            & (rois[:, 1] >= 0)
            & (rois[:, 2] >= 0)
            & (rois[:, 3] <= width)
            & (rois[:, 4] <= height)
            & (rois[:, 3] > rois[:, 1])
            & (rois[:, 4] > rois[:, 2])
        )
        if not self.config.class_agnostic:
            valid &= (classes >= 0) & torch.isfinite(classes)
            valid &= (classes == classes.trunc()) & (classes < len(self.config.supported_classes))
        result = features[0].new_full((len(rois),), float("nan"), dtype=torch.float32)
        if not valid.any():
            return result
        selected = rois[valid]
        pooled = []
        for feature in features:
            scale = feature.shape[-1] / width
            if abs(scale - feature.shape[-2] / height) > 1e-6:
                raise ValueError("Feature x/y spatial scales differ")
            pooled.append(
                roi_align(
                    feature.float(),
                    selected,
                    self.config.roi_size,
                    spatial_scale=scale,
                    sampling_ratio=2,
                    aligned=True,
                ).flatten(1)
            )
        coordinates = selected[:, 1:] / selected.new_tensor([width, height, width, height])
        geometry = torch.cat((coordinates, coordinates[:, 2:] - coordinates[:, :2]), 1)
        parts = [*pooled, geometry]
        if not self.config.class_agnostic:
            parts.append(
                functional.one_hot(
                    classes[valid].long(), len(self.config.supported_classes)
                ).float()
            )
        values = torch.cat(parts, 1)
        result[valid] = self.distance_head(values)
        return result

    def forward(self, images, rois, classes):
        """Functionality: Execute one detection/GT-ROI distance forward for training or validation.
        Inputs: images, ROIs and head class IDs. Outputs: unchanged detection tensor and distances.
        Logic: extract once, regress against the same features. Constraints: no Depth model.
        """
        prediction, features = self.extract(images)
        return prediction, self.regress(features, rois, classes, images.shape[-2:])

    def parameter_counts(self):
        """Functionality: Report detector/head/total/trainable parameter counts separately.
        Inputs: current parameters. Outputs: scalar dictionary. Logic: count numel once.
        Constraints: excludes activation/optimizer memory, which is measured separately.
        """
        return {
            "detector": sum(p.numel() for p in self.detector.parameters()),
            "distance_head": sum(p.numel() for p in self.distance_head.parameters()),
            "total": sum(p.numel() for p in self.parameters()),
            "trainable": sum(p.numel() for p in self.parameters() if p.requires_grad),
        }


def save_checkpoint(path, model, *, epoch=0, optimizer=None, extra=None):
    """Functionality: Save complete trained model state bound to its originating detector bytes.
    Inputs: owned path, model, epoch, optional optimizer and scalar run metadata. Outputs: .pt.
    Logic: CPU tensor state dictionaries; include detector state for optional feature fine-tuning.
    Constraints: generated checkpoints belong only in Git-ignored experiment/output directories.
    """
    torch.save(
        {
            "schema_version": 1,
            "distance_target": model.config.distance_target,
            "config": asdict(model.config),
            "detector_sha256": model.weight_sha256,
            "architecture": model.signature,
            "feature_sources": model.feature_sources,
            "epoch": epoch,
            "model": {k: v.detach().cpu() for k, v in model.state_dict().items()},
            "optimizer": optimizer.state_dict() if optimizer is not None else None,
            "extra": extra or {},
        },
        path,
    )


def load_checkpoint(path, *, detection_weights=None, device=None):
    """Functionality: Restore a strict E model without silently reusing a head on new weights.
    Inputs: checkpoint and optional relocated identical detector path/device. Outputs:
    model,payload.
    Logic: weights-only tensor loading; validate target/schema/base SHA and strict full state.
    Constraints: teammate detector changes require retraining; architecture/class mismatch errors
    are explicit, not partial state loading. Only use operator-trusted local detector checkpoints.
    """
    payload = torch.load(path, map_location="cpu", weights_only=True)
    config = ExperimentConfig(**payload["config"])
    if (
        payload.get("schema_version") != 1
        or payload.get("distance_target") != config.distance_target
    ):
        raise ValueError("Incompatible E checkpoint schema or target definition")
    config = replace(
        config,
        detection_weights=str(detection_weights or config.detection_weights),
        device=device or config.device,
    )
    if digest(config.detection_weights) != payload["detector_sha256"]:
        raise ValueError("Detector weights changed; retrain Distance Head for this checkpoint")
    model = ObjectDistanceModel(config)
    if model.signature != payload["architecture"] or model.feature_sources != tuple(
        payload["feature_sources"]
    ):
        raise ValueError("Checkpoint feature graph differs from detector")
    model.load_state_dict(payload["model"], strict=True)
    return model, payload
