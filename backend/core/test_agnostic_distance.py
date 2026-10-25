"""Responsibilities: Verify category-independent regression and gradients.
Implementation: Small deterministic feature tensors through actual ROI/head operations.
Related Modules: object_distance.model/config/predict/data.
Declaration Index: AgnosticTests; AgnosticTests.test_class_values_cannot_change_distance;
AgnosticTests.test_kitti_predicted_boxes_ignore_category_gate.
Variable Index: HAS_VISION identifies optional tensor/ROI dependencies, not GPU availability.
"""

from importlib.util import find_spec
from unittest import TestCase, skipUnless
from unittest.mock import Mock

from PIL import Image

from object_distance.config import ROOT, ExperimentConfig, read_config

HAS_VISION = all(find_spec(name) for name in ("torch", "torchvision"))
if HAS_VISION:
    import torch

    from object_distance.data import prepare_image
    from object_distance.model import DistanceHead, ObjectDistanceModel
    from object_distance.predict import run_frame


@skipUnless(HAS_VISION, "Optional PyTorch and Torchvision ROI operations required")
class AgnosticTests(TestCase):
    """Functionality: Guard removal of class dependency.
    Logic: Identical ROIs/features with radically different category values.
    Constraints: Does not claim real-world regression accuracy.
    """

    def test_class_values_cannot_change_distance(self):
        """Functionality: Prove categories do not affect forward or ROI eligibility.
        Inputs: Synthetic feature maps and boxes. Outputs: equality and gradient assertions.
        Logic: Compare normal IDs with NaN/negative IDs. Constraints: Same geometric ROIs.
        """
        config = ExperimentConfig(class_agnostic=True, device="cpu")
        model = ObjectDistanceModel.__new__(ObjectDistanceModel)
        torch.nn.Module.__init__(model)
        model.config = config
        model.distance_head = DistanceHead((2, 3, 4), config)
        features = [torch.randn(1, c, 8, 8) for c in (2, 3, 4)]
        boxes = torch.tensor([[0, 1, 1, 20, 20], [0, 2, 2, 25, 25]], dtype=torch.float32)
        first = model.regress(features, boxes, torch.tensor([0.0, 1.0]), (32, 32))
        second = model.regress(features, boxes, torch.tensor([float("nan"), -999.0]), (32, 32))
        torch.testing.assert_close(first, second)
        self.assertTrue(torch.isfinite(second).all())
        self.assertEqual(model.distance_head.layers[0].in_features, 9 * 9 + 6)
        second.sum().backward()
        self.assertGreater(model.distance_head.layers[0].weight.grad.abs().sum().item(), 0)

    def test_kitti_predicted_boxes_ignore_category_gate(self):
        """Functionality: Give unseen YOLO categories distances only in the new KITTI mode.
        Inputs: actual configs, synthetic detector rows and shared features. Outputs: canonical
        labels preserved, equal agnostic distances, legacy unsupported distances still null.
        Logic: run production decoding and ROI regression with only detector extraction mocked.
        Constraints: no weights, training, downloads or claims about unseen-category accuracy.
        """
        names = ["person", "bicycle", "car", "bus", "traffic light"]
        prediction = torch.zeros(1, 300, 6)
        for index in range(len(names)):
            prediction[0, index] = torch.tensor([2, 2, 20, 26, 0.9, index])
        features = [torch.ones(1, channels, 8, 8) for channels in (2, 3, 4)]
        tensor, transform = prepare_image(Image.new("RGB", (32, 32)), 32)
        for filename, agnostic in (("config.json", False), ("config.kitti-agnostic.json", True)):
            with self.subTest(config=filename):
                config = read_config(
                    ROOT / "object_distance" / filename, device="cpu", amp=False, image_size=32
                )
                self.assertEqual(config.class_agnostic, agnostic)
                model = ObjectDistanceModel.__new__(ObjectDistanceModel)
                torch.nn.Module.__init__(model)
                model.config, model.names = config, names
                model.distance_head = DistanceHead((2, 3, 4), config)
                model.extract = Mock(return_value=(prediction, features))
                boxes, _ = run_frame(model, tensor, transform)
                self.assertEqual([box["class_name"] for box in boxes], names)
                self.assertEqual(len(boxes), len(names))
                for box in boxes:
                    if agnostic or box["class_name"] in {"car", "person"}:
                        self.assertIsNotNone(box["distance_m"])
                        self.assertGreater(box["distance_m"], 0)
                    else:
                        self.assertIsNone(box["distance_m"])
                if agnostic:
                    torch.testing.assert_close(
                        torch.tensor([box["distance_m"] for box in boxes]),
                        torch.full((len(names),), boxes[0]["distance_m"]),
                    )
