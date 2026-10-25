"""Responsibilities: Guard evaluation-only profiling wrappers and original call behavior.
Implementation: Fake nested tracker/depth operations and deterministic method-call assertions.
Related Modules: unified_evaluation.stage_profile; sanpo_comparison.
Declaration Index: StageProfileTests; StageProfileTests.test_preserves_nested_calls;
StageProfileTests.test_preserves_nested_calls.update;
StageProfileTests.test_preserves_nested_calls.predict;
StageProfileTests.test_propagates_failure.
Variable Index: None.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from model_tests.unified_evaluation.stage_profile import StageProfile


class StageProfileTests(SimpleTestCase):
    """Functionality: Verify measurements delegate exactly once and retain predictions/state.
    Logic: fake nested update calls and error injection. Constraints: no GPU timing claims.
    """

    def test_preserves_nested_calls(self):
        """Functionality: Keep original outputs while measuring nested association/GMC calls.
        Inputs: stateful mock adapter. Outputs: same boxes/flag and finite profiling fields.
        Logic: execute dependencies through wrapped methods. Constraints: never duplicate inference.
        """
        depth = SimpleNamespace(predict=Mock(return_value="depth"))
        tracker = SimpleNamespace(gmc=SimpleNamespace(apply=Mock(return_value="warp")))
        for name in (
            "_first_association",
            "_post_first_association",
            "_second_association",
            "_unconfirmed_association",
        ):
            setattr(tracker, name, Mock())

        def update():
            """Functionality: Exercise nested timing. Inputs: None. Outputs: unchanged marker.
            Logic: call original GMC and matching. Constraints: no actual tracking algorithms.
            """
            tracker.gmc.apply()
            tracker._first_association()
            return "tracked"

        tracker.update = update
        boxes = [{"distance_m": 3}]

        def predict():
            """Functionality: Exercise fake inference. Inputs: None. Outputs: exact boxes marker.
            Logic: invoke depth/tracker. Constraints: prescribed totals only test arithmetic safety.
            """
            self.assertEqual(depth.predict(), "depth")
            self.assertEqual(tracker.update(), "tracked")
            return (
                boxes,
                {
                    "end_to_end_ms": 100,
                    "prepare_ms": 10,
                    "detection_ms": 20,
                    "tracking_ms": 30,
                    "distance_ms": 40,
                },
                True,
            )

        adapter = SimpleNamespace(
            identity={},
            depth=depth,
            tracker=SimpleNamespace(tracker=tracker),
            device="cpu",
            reset=Mock(),
            predict=predict,
        )
        profile = StageProfile(adapter)
        with patch("model_tests.unified_evaluation.stage_profile.synchronize") as synchronize:
            actual, timing, flag = profile.predict()
            self.assertEqual(synchronize.call_count, 2)
        self.assertIs(actual, boxes)
        self.assertTrue(flag)
        self.assertGreaterEqual(timing["tracking_update_ms"], timing["tracking_gmc_ms"])
        self.assertEqual(timing["other_ms"], 0)
        depth.predict()
        profile.reset()
        adapter.reset.assert_called_once()
        self.assertTrue(profile.identity["stage_profile"])

    def test_propagates_failure(self):
        """Functionality: Keep dependency failures explicit. Inputs: raising method.
        Outputs: original RuntimeError. Logic: no catch/fallback. Constraints: never report success.
        """
        adapter = SimpleNamespace(identity={}, predict=Mock(side_effect=RuntimeError("fixture")))
        profile = StageProfile(adapter)
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            profile.predict()
