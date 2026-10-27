"""Responsibilities: Verify the no-paid-extension quota gate under known and unknown limits.
Implementation: Synthetic quota JSON, actual90s reservation for the original60s task; no HF calls.
Related Modules: core.hf_quota, tools.check_zerogpu_quota; real snapshots stored separately.
Declaration Index: ZeroQuotaTests; ZeroQuotaTests.test_seconds_and_run_limits;
ZeroQuotaTests.test_unknown_quota_and_duration; ZeroQuotaTests.test_denied_gate_never_dispatches;
ZeroQuotaTests.test_blackwell_reservation_preflight.
Variable Index: None. Synthetic values cannot establish account billing status or actual quota.
"""

import io
from unittest.mock import patch

from django.test import SimpleTestCase

from core.hf_quota import LIVE_GPU_QUOTA_SECONDS, quota_decision
from core.hf_video_relay import read_free_quota
from tools.benchmark_zerogpu_live import start_remote


class ZeroQuotaTests(SimpleTestCase):
    """Functionality: Reject exhausted or unverifiable free quota before a GPU experiment.
    Logic: test independent seconds/run conditions and unknown schema; Constraints: mocked data.
    """

    def test_seconds_and_run_limits(self):
        """Functionality: Require sufficient seconds and at least one free run independently.
        Inputs: synthetic balances/run counts; Outputs: explicit approval or denial reasons.
        Logic: enough time alone cannot authorize a task; Constraints: no network/billing proof.
        """
        self.assertTrue(quota_decision({"current": 60, "runs": {"remaining": 1}}, 60)["allowed"])
        self.assertEqual(quota_decision({"current": 132, "runs": {"remaining": 0}}, 60)["reason"],
                         "free_run_limit_exhausted")
        self.assertEqual(quota_decision({"current": 59, "runs": {"remaining": 1}}, 60)["reason"],
                         "insufficient_free_seconds")

    def test_unknown_quota_and_duration(self):
        """Functionality: Reject missing/non-finite/boolean limits and unsupported reservation size.
        Inputs: incomplete or invalid synthetic quota and duration; Outputs: deny or ValueError.
        Logic: unknown API fields never become free permission; Constraints:90s quota reservation
        ceiling preserves the original60s GPU duration and55s video loop.
        """
        for data in ({}, {"current": 300}, {"current": float("nan"), "runs": {"remaining": 1}},
                     {"current": 300, "runs": {"remaining": True}}):
            self.assertFalse(quota_decision(data, 60)["allowed"])
        for invalid in (91, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                quota_decision({"current": 300, "runs": {"remaining": 1}}, invalid)

    def test_blackwell_reservation_preflight(self):
        """Functionality: Reject the observed77.85s balance before the actual90s GPU reservation.
        Inputs: synthetic quota HTTP body and exact90s boundary; Outputs: denial/allow decisions.
        Logic: real production read_free_quota and strict policy, private HTTP identity retained.
        Constraints: HTTP is mocked; this test does not verify HF or consume quota.
        """
        self.assertEqual(LIVE_GPU_QUOTA_SECONDS, 90)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(
                b'{"current":77.850766,"runs":{"remaining":1}}')) as request:
            decision = read_free_quota("hf_synthetic")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "insufficient_free_seconds")
        self.assertEqual(decision["required_gpu_seconds"], 90)
        self.assertEqual(request.call_args.args[0].get_header("Authorization"),
                         "Bearer hf_synthetic")
        self.assertTrue(quota_decision({"current": 90, "runs": {"remaining": 1}},
                                       LIVE_GPU_QUOTA_SECONDS)["allowed"])

    def test_denied_gate_never_dispatches(self):
        """Functionality: Prevent a Gradio GPU POST when the read-only free run gate denies work.
        Inputs: synthetic quota HTTP body/token and spy exchange; Outputs: original denial/no calls.
        Logic: exercise actual start_remote gate before any dispatch; Constraints: HF fully mocked,
        no billing/quota availability proof; spy confirms no inference request was attempted.
        """
        cases = [(b'{"current":132,"runs":{"remaining":0}}', "free_run_limit_exhausted"),
                 (b'{"current":77.850766,"runs":{"remaining":1}}',
                  "insufficient_free_seconds")]
        for body, reason in cases:
            report = {}
            with self.subTest(reason=reason), patch("urllib.request.urlopen", return_value=
                    io.BytesIO(body)), patch("tools.benchmark_zerogpu_live.exchange") as dispatch:
                with self.assertRaisesRegex(RuntimeError, reason):
                    start_remote("0" * 64, "hf_synthetic", report)
            dispatch.assert_not_called()
            self.assertFalse(report["quota_gate"]["allowed"])
