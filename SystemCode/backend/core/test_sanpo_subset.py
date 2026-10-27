"""Responsibilities: Guard SANPO expansion budgets, originals and temporal sampling.
Implementation: Temporary local fixtures and mocked download boundary.
Related Modules: model_tests.sanpo_subset.
Declaration Index: SanpoSubsetTests; SanpoSubsetTests.test_budget_and_existing_file_guards;
    SanpoSubsetTests.test_temporal_sampling_keeps_novel_targets_without_duplicates;
    SanpoSubsetTests.test_official_split_guard.
Variable Index: None.
"""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from model_tests import sanpo_subset as subset


class SanpoSubsetTests(TestCase):
    """Functionality: Test preparation safeguards without fetching external data.
    Logic: Fail before transfer or preserve representative records.
    Constraints: Does not establish geographic truth or sensor accuracy.
    """

    def test_budget_and_existing_file_guards(self):
        """Functionality: Reject excessive downloads and conflicting existing originals.
        Inputs: Temporary root and metadata. Outputs: Assertions.
        Logic: Mock transfer, exercise both preflight failures. Constraints: No network calls.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(subset, "ROOT", root), patch.object(subset, "OUT", root / "out"):
                with patch.object(subset, "download_group") as transfer:
                    with self.assertRaisesRegex(ValueError, "budget"):
                        subset.download([{"path": "huge", "bytes": 10_000_000_001}], "test")
                    existing = root / "existing"
                    existing.write_bytes(b"keep")
                    with self.assertRaisesRegex(ValueError, "differs"):
                        subset.download([{"path": "existing", "bytes": 5}], "test")
                    self.assertEqual(existing.read_bytes(), b"keep")
                    transfer.assert_not_called()

    def test_temporal_sampling_keeps_novel_targets_without_duplicates(self):
        """Functionality: Thin repetitive frames and retain a new nearby target.
        Inputs: One static object and one new object. Outputs: Selected frame identities.
        Logic: Keep 0.8-second base plus novelty at 1.2 seconds. Constraints: No oversampling.
        """
        records = []
        for index in range(5):
            objects = [{"class_name": "pedestrian", "instance_id": 1, "distance_m": 4.0}]
            if index == 3:
                objects.append({"class_name": "vehicle", "instance_id": 2, "distance_m": 8.0})
            records.append(
                {"image_id": index, "objects": objects, "source": {"timestamp_s": index * 0.4}}
            )
        selected = subset.select_training_frames(records)
        self.assertEqual([r["image_id"] for r in selected], [0, 2, 3])
        self.assertEqual(len(selected), len({r["image_id"] for r in selected}))
        records[3]["objects"][1]["distance_m"] = 30.0
        self.assertEqual(
            [r["image_id"] for r in subset.select_training_frames(records)], [0, 2, 4]
        )

    def test_official_split_guard(self):
        """Functionality: Reject official-test reassignment.
        Inputs: Deliberately invalid selections. Outputs: Assertions before download.
        Logic: Patch catalogue and copy boundaries. Constraints: No real files changed.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cat = {"s": {"official_split": "official_test"}}
            selection = {
                "sessions": [{"session_id": "s", "split": "train", "location_group": "park"}]
            }
            with patch.object(subset, "OUT", root):
                subset.write(root / "selection.json", selection)
                subset.write(root / "candidates.json", [])
                with patch.object(subset, "catalogue", return_value=cat):
                    with patch.object(subset, "download") as transfer:
                        with self.assertRaisesRegex(ValueError, "Official split"):
                            subset.build()
                        transfer.assert_not_called()
