"""Responsibilities: Check SANPO conversion and distance-target isolation.
Implementation: Independent minimal session manifests and annotation observations.
Related Modules: object_distance.prepare_sanpo/data/config.
Declaration Index: SanpoTrainingTests; SanpoTrainingTests.test_conversion_and_target_guard.
Variable Index: HAS_TORCH identifies the optional dataset loader dependency.
"""

import json
import tempfile
from importlib.util import find_spec
from pathlib import Path
from unittest import TestCase, skipUnless

from object_distance.prepare_sanpo import TARGET_NAME, convert

HAS_TORCH = find_spec("torch") is not None
if HAS_TORCH:
    from object_distance.data import KittiObjects


@skipUnless(HAS_TORCH, "Optional PyTorch dataset loader required")
class SanpoTrainingTests(TestCase):
    """Functionality: Test usable manifests and reject mislabeled targets.
    Logic: Separate sessions, manual/machine observations, explicit target check.
    Constraints: Fixtures do not establish actual dataset accuracy.
    """

    def test_conversion_and_target_guard(self):
        """Functionality: Verify converter and loader contract. Inputs: Temporary fixture.
        Outputs: Assertions. Logic: Preserve splits and select manual pedestrian targets.
        Constraints: No model training or network calls.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "manifests").mkdir()
            status = root / "reports/validation-status.json"
            status.write_text(json.dumps({"stage": "waiting_for_download"}))
            with self.assertRaises(ValueError):
                convert(root, root / "processed")
            status.write_text(json.dumps({"stage": "verified"}))
            observations = []
            for split in ("train", "validation", "test"):
                frame = {
                    "session_id": split,
                    "frame_index": 0,
                    "annotation_type": "HUMAN_ANNOTATED",
                    "rgb": "raw/frame.png",
                    "shapes": {"rgb_shape": [1242, 2208, 3]},
                    "calibration": {},
                }
                (root / f"manifests/{split}-frames.jsonl").write_text(json.dumps(frame))
                observations.append(
                    {
                        **frame,
                        "class_id": 12,
                        "distance_available": True,
                        "bbox_xyxy_rgb": [1, 2, 30, 40],
                        "median_surface_depth_m": 3.5,
                        "instance_id": 1,
                    }
                )
                observations.append({**observations[-1], "class_id": 21})
            (root / "reports/focus-object-observations.jsonl").write_text(
                "\n".join(json.dumps(row) for row in observations)
            )
            report = convert(root, root / "processed")
            self.assertEqual(report["splits"]["train"]["objects"], 1)
            with self.assertRaises(ValueError):
                KittiObjects(root / "processed", "train")
            dataset = KittiObjects(root / "processed", "train", distance_target=TARGET_NAME)
            self.assertEqual(dataset.records[0]["objects"][0]["distance_m"], 3.5)
            self.assertEqual(dataset.records[0]["image_size"], [2208, 1242])
