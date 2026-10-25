"""Responsibilities: Load fixed KITTI manifests with exact letterbox/ROI coordinate transforms.
Implementation: Original RGB, bilinear resize,114 padding, effective x/y resize scales and FP32/255.
Related Modules: object_distance.prepare/model/train/evaluate; production transforms stay unchanged.
Declaration Index: FrameTransform; FrameTransform.to_input; FrameTransform.to_original;
prepare_image; KittiObjects; KittiObjects.__init__; KittiObjects.__len__; KittiObjects.__getitem__;
collate_objects.
Variable Index: None. Raw RGB inputs have no extra JPEG encoding in this experiment.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from object_distance.config import TARGET, digest


@dataclass(frozen=True)
class FrameTransform:
    """Functionality: Map original xyxy boxes to/from actual rounded letterbox pixels.
    Logic: distinct effective x/y scales account for integer resize rounding.
    Constraints: mapped boxes describe original pixels; no EXIF rotation or crop is applied.
    """

    scale_x: float
    scale_y: float
    left: int
    top: int
    original_size: tuple

    def to_input(self, boxes):
        """Functionality: Map boxes to padded model input coordinates.
        Inputs: Nx4 floating tensor. Outputs: new Nx4 tensor. Logic: affine x/y scale/translation.
        Constraints: no in-place mutation or assumed feature-stride layer numbers.
        """
        return boxes * boxes.new_tensor([self.scale_x, self.scale_y] * 2) + boxes.new_tensor(
            [self.left, self.top] * 2
        )

    def to_original(self, boxes):
        """Functionality: Restore and clip model xyxy coordinates to original RGB pixels.
        Inputs: Nx4 tensor. Outputs: clipped new tensor. Logic: inverse affine per axis.
        Constraints: empty/reversed results are rejected by the caller, never swapped.
        """
        result = (boxes - boxes.new_tensor([self.left, self.top] * 2)) / boxes.new_tensor(
            [self.scale_x, self.scale_y] * 2
        )
        result[:, 0::2].clamp_(0, self.original_size[0])
        result[:, 1::2].clamp_(0, self.original_size[1])
        return result


def prepare_image(image, edge=640):
    """Functionality: Letterbox any RGB experiment image without the production JPEG size limit.
    Inputs: PIL image and square edge divisible by32. Outputs: CHW FP32 tensor and transform.
    Logic: same bilinear/114/FP32 normalization as A, retain exact rounded per-axis scales.
    Constraints: experiment input is original RGB; fair comparisons must explicitly match encoding.
    """
    if image.mode != "RGB" or min(image.size) <= 0 or edge <= 0 or edge % 32:
        raise ValueError("Expected RGB image and positive edge divisible by32")
    scale = edge / max(image.size)
    resized = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    left, top = (edge - resized[0]) // 2, (edge - resized[1]) // 2
    canvas = Image.new("RGB", (edge, edge), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    array = np.asarray(canvas).transpose(2, 0, 1).copy().astype(np.float32) / np.float32(255)
    return torch.from_numpy(array), FrameTransform(
        resized[0] / image.width, resized[1] / image.height, left, top, image.size
    )


class KittiObjects(Dataset):
    """Functionality: Load an immutable verified split and its object-level camera-z targets.
    Logic: retain empty frames; variable object counts are packed by collate_objects.
    Constraints: limits are explicit ordered smoke subsets, never described as full evaluation.
    """

    def __init__(
        self,
        processed,
        split,
        image_size=640,
        limit=None,
        distance_target=TARGET,
        class_names=("car", "person"),
    ):
        """Functionality: Validate split hash/target before reading any training pixels.
        Inputs: processed directory, train/val, edge and optional positive image limit.
        Outputs: dataset with owned records and provenance. Logic: require converted manifest hash.
        Constraints: dataset paths cannot escape the KITTI root or silently change target units.
        """
        self.processed = Path(processed).resolve()
        self.root, self.image_size = self.processed.parent, image_size
        manifest = json.loads((self.processed / "manifest.json").read_text(encoding="utf-8"))
        path = self.processed / f"{split}.jsonl"
        if (
            manifest["distance_target"] != distance_target
            or digest(path) != manifest["splits"][split]["manifest_sha256"]
        ):
            raise ValueError("Dataset target or split fingerprint mismatch")
        self.class_names = tuple(class_names)
        self.distance_target = distance_target
        self.records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if limit is not None:
            if type(limit) is not int or limit < 1:
                raise ValueError("Dataset limit must be positive")
            self.records = self.records[:limit]
        self.fingerprint, self.split = digest(path), split
        for record in self.records:
            image_path = (self.root / record["image_path"]).resolve()
            if not image_path.is_relative_to(self.root):
                raise ValueError("Image path escapes KITTI root")

    def __len__(self):
        """Functionality: Count selected images. Inputs: None. Outputs: int.
        Logic: count records. Constraints: counts images, not objects or repeated epochs.
        """
        return len(self.records)

    def __getitem__(self, index):
        """Functionality: Decode one RGB frame and align all accepted GT boxes.
        Inputs: record index. Outputs: tensor and original/input boxes/classes/distances/identity.
        Logic: load only current image; map car/person to distance-head IDs0/1.
        Constraints: empty targets use0x4 boxes, no fabricated objects or distance labels.
        """
        record = self.records[index]
        with Image.open(self.root / record["image_path"]) as image:
            if list(image.size) != record["image_size"] or image.mode != "RGB":
                raise ValueError("Raw image changed since conversion")
            tensor, transform = prepare_image(image, self.image_size)
        boxes = torch.tensor(
            [obj["bbox"] for obj in record["objects"]], dtype=torch.float32
        ).reshape(-1, 4)
        return {
            "image": tensor,
            "boxes": transform.to_input(boxes),
            "original_boxes": boxes,
            "classes": torch.tensor(
                [self.class_names.index(obj["class_name"]) for obj in record["objects"]],
                dtype=torch.long,
            ),
            "distances": torch.tensor(
                [obj["distance_m"] for obj in record["objects"]], dtype=torch.float32
            ),
            "image_id": record["image_id"],
            "transform": transform,
        }


def collate_objects(samples):
    """Functionality: Pack variable object counts for one shared-backbone batch.
    Inputs: list of dataset samples. Outputs: images, Nx5 [batch,x1,y1,x2,y2] ROIs/classes/targets.
    Logic: concatenate empty-safe boxes in image order. Constraints: no ROI sampling or duplicates.
    """
    rois = [
        torch.cat((torch.full((len(sample["boxes"]), 1), index), sample["boxes"]), 1)
        for index, sample in enumerate(samples)
    ]
    return {
        "images": torch.stack([s["image"] for s in samples]),
        "rois": torch.cat(rois),
        "classes": torch.cat([s["classes"] for s in samples]),
        "distances": torch.cat([s["distances"] for s in samples]),
        "samples": samples,
    }
