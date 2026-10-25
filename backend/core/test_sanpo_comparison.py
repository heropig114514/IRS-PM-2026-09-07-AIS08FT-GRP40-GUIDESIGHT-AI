"""Responsibilities: Verify continuous SANPO evaluation and manual/depth sampling separation.
Implementation: Temporary RGB/manifests and a stateful fake distance adapter.
Related Modules: unified_evaluation.sanpo_comparison; object_distance.reporting.
Declaration Index: SanpoComparisonTests; SanpoComparisonTests.fixture;
SanpoComparisonTests.test_continuity_and_annotation_phases;
SanpoComparisonTests.test_continuous_scoring; SanpoComparisonTests.test_continuous_scoring.reset;
SanpoComparisonTests.test_continuous_scoring.predict;
SanpoComparisonTests.test_missing_rgb_and_machine_targets.
SanpoComparisonTests.test_explicit_diagnostic_split.
Variable Index: HAS_TORCH identifies the optional evaluation dataset loader dependency.
"""

import json
import tempfile
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock

from django.test import SimpleTestCase
from PIL import Image

from object_distance.config import digest, write_json
from object_distance.sanpo_instances import TARGET

HAS_TORCH = find_spec("torch") is not None
if HAS_TORCH:
    from model_tests.unified_evaluation.sanpo_comparison import (
        evaluate_pass,
        load_sequence,
        write_reports,
    )


@skipUnless(HAS_TORCH, "Optional PyTorch evaluation dataset loader required")
class SanpoComparisonTests(SimpleTestCase):
    """Functionality: Verify official-session boundaries, sampling and inherited-distance scoring.
    Logic: thirteen frames with annotations every six and depth every five.
    Constraints: synthetic predictions test protocol correctness, not trained model accuracy.
    """

    def fixture(self, root):
        """Functionality: Create a complete tiny continuous Test sequence with immutable targets.
        Inputs: owned directory. Outputs: paths/options matching production schema.
        Logic: one Test session with three manual targets and ten unscored machine frames.
        Constraints: independent objects include an unmapped class to guard all-GT coverage.
        """
        (root / "splits").mkdir()
        (root / "splits/test-sessions.txt").write_text("session\n")
        processed = root / "processed"
        processed.mkdir()
        frames, targets = [], []
        camera = {"fx": 100, "fy": 100, "cx": 10, "cy": 5}
        for i in range(13):
            rgb = f"{i}.png"
            Image.new("RGB", (20, 10)).save(root / rgb)
            frames.append(
                {
                    "session_id": "session",
                    "frame_index": i,
                    "camera": "camera_head/left",
                    "timestamp_nominal_s": i / 15,
                    "rgb": rgb,
                    "calibration": camera,
                    "annotation_type": "HUMAN_ANNOTATED" if i % 6 == 0 else "MACHINE_ANNOTATED",
                    "shapes": {"rgb_shape": [10, 20, 3]},
                }
            )
            if i % 6 == 0:
                targets.append(
                    {
                        "image_id": f"session/{i:06d}",
                        "image_path": rgb,
                        "image_size": [20, 10],
                        "objects": [
                            {"class_name": name, "bbox": [1, 1, 10, 9], "distance_m": 5}
                            for name in ("pedestrian", "opening-door")
                        ],
                    }
                )
        source = root / "frames.jsonl"
        source.write_text("".join(json.dumps(r) + "\n" for r in frames))
        path = processed / "test.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in targets))
        write_json(
            processed / "manifest.json",
            {"distance_target": TARGET, "splits": {"test": {"manifest_sha256": digest(path)}}},
        )
        return {
            "root": str(root),
            "frames": str(source),
            "processed": str(processed),
            "depth_interval": 5,
            "limit": None,
            "warmup": 2,
            "method": "c",
            "match_iou": 0.5,
        }

    def test_continuity_and_annotation_phases(self):
        """Functionality: Distinguish depth/non-depth scoring phases and reject source-frame gaps.
        Inputs: interval5/3 and a deliberate frame gap. Outputs: phase audit and explicit failure.
        Logic: 0,6,12 span phases0,1,2 at interval5 but all phase0 at interval3.
        Constraints: never silently reinterpret sparse manual frames as continuous video.
        """
        with tempfile.TemporaryDirectory() as temporary:
            options = self.fixture(Path(temporary))
            _, _, audit = load_sequence(options)
            self.assertEqual(audit["manual_frame_schedule_phases"], {0: 1, 1: 1, 2: 1})
            self.assertFalse(audit["manual_frames_only_on_depth_phase"])
            self.assertTrue(
                load_sequence({**options, "depth_interval": 3})[2][
                    "manual_frames_only_on_depth_phase"
                ]
            )
            path = Path(options["frames"])
            rows = path.read_text().splitlines()
            path.write_text("\n".join(rows[:2] + rows[3:]))
            with self.assertRaisesRegex(ValueError, "Continuous"):
                load_sequence(options)

    def test_continuous_scoring(self):
        """Functionality: Preserve intermediate inference and score only manual target frames.
        Inputs: fake C and complete sequence. Outputs:13 inference frames,3 scored frames,6 GT.
        Logic: reset warmup state then keep tracker phase across every annotated/unannotated frame.
        Constraints: unsupported classes remain in coverage and non-depth predictions remain scored.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = self.fixture(root)
            sources, targets, audit = load_sequence(options)
            state = SimpleNamespace(index=0)

            def reset():
                """Functionality: Clear fake temporal position. Inputs: None. Outputs: None.
                Logic: reset counter. Constraints: must not run at annotation boundaries.
                """
                state.index = 0

            def predict(image, frame, *, bbox_mode, timestamp):
                """Functionality: Simulate one tracked person and a periodic depth call.
                Inputs: source frame. Outputs: canonical objects/timings/depth flag.
                Logic: emit person every frame, depth at multiples of5. Constraints: no GT lookup.
                """
                depth = state.index % 5 == 0
                state.index += 1
                return (
                    [{"class_name": "person", "bbox": [1, 1, 10, 9], "distance_m": 6}],
                    {
                        "detection_ms": 1,
                        "distance_ms": 2 if depth else 0.1,
                        "end_to_end_ms": 3 if depth else 1.1,
                    },
                    depth,
                )

            adapter = SimpleNamespace(reset=Mock(side_effect=reset), predict=predict, identity={})
            output = root / "out"
            output.mkdir()
            report = evaluate_pass(adapter, sources, targets, audit, output, options)
            self.assertEqual(adapter.reset.call_count, 2)
            self.assertEqual(report["temporal"]["depth_calls"], 3)
            self.assertEqual(report["temporal"]["scored_depth_frames"], 1)
            self.assertEqual(report["temporal"]["scored_non_depth_frames"], 2)
            self.assertEqual(report["metrics"]["overall"]["ground_truth_objects"], 6)
            self.assertEqual(report["metrics"]["overall"]["valid_predictions"], 3)
            self.assertEqual(report["metrics"]["overall"]["mae_m"], 1)
            write_reports(
                output,
                {
                    "title": "SANPO test",
                    "status": "complete",
                    "limitations": [],
                    "results": {"predicted": {"c": report}},
                    "paired": {},
                },
            )
            self.assertIn("SANPO test", (output / "comparison.md").read_text())
            self.assertNotIn(
                "KITTI A/B/C/E validation comparison", (output / "comparison.md").read_text()
            )
            self.assertTrue((output / "temporal.csv").is_file())

    def test_missing_rgb_and_machine_targets(self):
        """Functionality: Refuse incomplete RGB or machine labels disguised as manual targets.
        Inputs: modified source metadata then missing RGB. Outputs: explicit validation failures.
        Logic: validation precedes model loading. Constraints: no downloads or silent filtering.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = self.fixture(root)
            path = Path(options["frames"])
            original = path.read_text()
            rows = [json.loads(s) for s in original.splitlines()]
            rows[0]["annotation_type"] = "MACHINE_ANNOTATED"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            with self.assertRaisesRegex(ValueError, "Machine"):
                load_sequence(options)
            path.write_text(original)
            (root / "1.png").unlink()
            with self.assertRaisesRegex(ValueError, "Missing"):
                load_sequence(options)

    def test_explicit_diagnostic_split(self):
        """Functionality: Allow diagnostic Train sessions only with explicit scope and provenance.
        Inputs: synthetic Train session. Outputs: reject default, accept named diagnostic mode.
        Logic: validate original split labels. Constraints: never relabel Train as Test.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = self.fixture(root)
            (root / "splits/test-sessions.txt").write_text("")
            (root / "splits/train-sessions.txt").write_text("session\n")
            path = Path(options["frames"])
            rows = [json.loads(s) for s in path.read_text().splitlines()]
            path.write_text(
                "".join(json.dumps({**r, "source_split": "train"}) + "\n" for r in rows)
            )
            with self.assertRaises(ValueError):
                load_sequence(options)
            audit = load_sequence({**options, "allow_diagnostic_sessions": True})[2]
            self.assertEqual(audit["session_source_splits"], {"session": "train"})
