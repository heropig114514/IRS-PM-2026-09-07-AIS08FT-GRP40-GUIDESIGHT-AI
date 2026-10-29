"""Responsibilities: Verify predictor reuse never silently accepts configuration drift or retries.
Implementation: Controlled SDK-like ownership with actual callable delegation and explicit errors.
Related Modules: tools.depth_predictor_probe; core.test_inference_benchmark.
Declaration Index: make_owner; PredictorProbeTests; PredictorProbeTests.test_delegation;
PredictorProbeTests.test_drift_rejected; PredictorProbeTests.test_initialization_and_failure.
Variable Index: None. Doubles verify adapter boundaries, not pretrained CUDA behavior.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from tools.depth_predictor_probe import CALL_OPTIONS, CachedDepthPredictor


def make_owner():
    """Functionality: Provide a minimal warmed native predictor with observable calls.
    Inputs: None. Outputs: depth owner with stable model/backend identities.
    Logic: explicit fixed args mirror the current contract. Constraints: no model or GPU proof.
    """
    predictor = MagicMock(done_warmup=True)
    predictor.args = SimpleNamespace(imgsz=768, device="cuda", quantize=None, verbose=False,
                                     save=False, batch=1, rect=True, embed=None, classes=[0, 1])
    predictor.model = SimpleNamespace(format="pt", fp16=False)
    predictor.device = SimpleNamespace(type="cuda")
    return SimpleNamespace(predictor=predictor, task="depth", model=object(), overrides={})


class PredictorProbeTests(SimpleTestCase):
    """Functionality: Guard ownership, fixed arguments and single-attempt error behavior.
    Logic: verify observable delegation and independent mutations at the reuse boundary.
    Constraints: real depth/image/shape equivalence is checked by the separate CUDA diagnostic.
    """

    def test_delegation(self):
        """Functionality: Preserve result/source identity through the full official callable.
        Inputs: two distinct image sentinels. Outputs: original results with exactly two calls.
        Logic: ensure preparation does no inference and source is passed anew each time.
        Constraints: does not bypass predictor internals or cache prediction results.
        """
        owner = make_owner()
        probe = CachedDepthPredictor(owner)
        owner.predictor.assert_not_called()
        for source in (object(), object()):
            self.assertIs(probe(source=source, **CALL_OPTIONS), owner.predictor.return_value)
            owner.predictor.assert_called_with(source=source, stream=False)
        self.assertEqual(owner.predictor.call_count, 2)

    def test_drift_rejected(self):
        """Functionality: Reject changed ownership, nested options or invocation before GPU work.
        Inputs: independent model/backend/predictor, args, overrides and keyword mutations.
        Outputs: ValueError and no invocation. Logic: snapshot catches in-place list changes too.
        Constraints: no reset, retry or fallback to the wrapper when the contract changes.
        """
        for change in ("owner", "backend", "predictor", "args", "overrides", "options"):
            with self.subTest(change=change):
                owner = make_owner()
                original = owner.predictor
                probe = CachedDepthPredictor(owner)
                options = dict(CALL_OPTIONS)
                if change == "owner":
                    owner.model = object()
                elif change == "backend":
                    owner.predictor.model = object()
                elif change == "predictor":
                    owner.predictor = MagicMock()
                elif change == "args":
                    owner.predictor.args.classes.append(2)
                elif change == "overrides":
                    owner.overrides["imgsz"] = 640
                else:
                    options["half"] = True
                with self.assertRaises(ValueError):
                    probe(source=object(), **options)
                original.assert_not_called()

    def test_initialization_and_failure(self):
        """Functionality: Require initialized FP32/CUDA and preserve errors without a second call.
        Inputs: unwarmed or half backend, then a predictor raising a synthetic failure.
        Outputs: initialization rejection and identical propagated error once.
        Logic: exercise failure at preparation and actual inference. Constraints: no swallowing.
        """
        owner = make_owner()
        owner.predictor.done_warmup = False
        with self.assertRaises(ValueError):
            CachedDepthPredictor(owner)
        owner.predictor.done_warmup = True
        owner.predictor.model.fp16 = True
        with self.assertRaises(ValueError):
            CachedDepthPredictor(owner)
        owner.predictor.model.fp16 = False
        failure = RuntimeError("synthetic predictor failure")
        owner.predictor.side_effect = failure
        probe = CachedDepthPredictor(owner)
        with self.assertRaises(RuntimeError) as caught:
            probe(source=object(), **CALL_OPTIONS)
        self.assertIs(caught.exception, failure)
        owner.predictor.assert_called_once()
