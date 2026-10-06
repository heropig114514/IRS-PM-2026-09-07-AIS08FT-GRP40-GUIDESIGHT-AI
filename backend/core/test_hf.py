"""Responsibilities: Verify the sole private live allocation HTTP/SSE transport.
Implementation: Mock only TLS/socket responses; exercise the real bounded parser and failures.
Related Modules: hf_services; hf_video_relay; external deployment evidence remains separate.
Declaration Index: HFTransportTests; HFTransportTests.test_private_completion;
HFTransportTests.test_quota_and_bad_events; HFTransportTests.test_bounds_and_auth;
HFTransportTests.test_provider_reason_and_redaction.
Variable Index: None. Synthetic credentials never access HF or spend quota.
"""

import time
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from . import hf_services
from .detector import DetectionError


class HFTransportTests(SimpleTestCase):
    """Functionality: Verify allocation parsing, private identity and explicit failure semantics.
    Logic: real parser with synthetic HTTP records; Constraints: no network/GPU verification.
    """

    def test_private_completion(self):
        """Functionality: Preserve split completion and heartbeat handling with private auth.
        Inputs: synthetic SSE chunks and token; Outputs: decoded summary/one request/closed socket.
        Logic: cross chunk boundaries through the real exchange parser.
        Constraints: mocked TLS does not verify real HF admission.
        """
        response = Mock(status=200)
        response.getheader.return_value = "text/event-stream; charset=utf-8"
        response.read1.side_effect = [
            b"event: heartbeat\ndata: null\n\nevent: com",
            b'plete\ndata: [{"frames":',
            b"3}]\n\n",
        ]
        with patch.object(hf_services.http.client, "HTTPSConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value = response
            result = hf_services.exchange(
                "GET", "/test", None, time.monotonic() + 1, True, token="hf_synthetic"
            )
        self.assertEqual(result, [{"frames": 3}])
        self.assertEqual(
            connection.request.call_args.args[3]["Authorization"], "Bearer hf_synthetic"
        )
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_quota_and_bad_events(self):
        """Functionality: Reject quota, malformed, obsolete and incomplete event streams.
        Inputs: synthetic failures; Outputs: explicit exception and one request per case.
        Logic: each response passes the real parser; Constraints: no retry or live quota spending.
        """
        cases = [
            b'event: error\ndata: {"title":"ZeroGPU quota exceeded"}\n\n',
            b"event: generating\ndata: []\n\n",
            b"data: []\n\n",
            b"",
        ]
        for index, record in enumerate(cases):
            with (
                self.subTest(index=index),
                patch.object(hf_services.http.client, "HTTPSConnection") as factory,
            ):
                connection = factory.return_value
                response = connection.getresponse.return_value
                response.status = 200
                response.getheader.return_value = "text/event-stream"
                response.read1.side_effect = [record, b""]
                with self.assertRaises(DetectionError if index == 0 else ValueError) as caught:
                    hf_services.exchange(
                        "GET", "/test", None, time.monotonic() + 1, True, token="hf_synthetic"
                    )
                if index == 0:
                    self.assertEqual(caught.exception.code, "hf_quota_exceeded")
                connection.request.assert_called_once()
                connection.close.assert_called_once()

    def test_provider_reason_and_redaction(self):
        """Functionality: Distinguish admission reasons and preserve only safe quota numbers.
        Inputs: pinned HF error shapes plus generic/secret-bearing synthetic messages.
        Outputs: correct codes, numeric public detail, redacted reason-only logs.
        Logic: exercise actual classifier; a quota mention alone must not imply exhausted credit.
        Constraints: no network/GPU use; real90s/78s rejection is separate deployment evidence.
        """
        cases = [
            ({"title": "ZeroGPU quota exceeded", "error":
              "You have exceeded your free ZeroGPU quota (90s requested vs. 78s left)."},
             "hf_quota_exceeded"),
            ({"error": "You have exceeded your ZeroGPU runs limit."}, "hf_runs_exceeded"),
            ({"error": "You have too many ZeroGPU credits allocated to running tasks."},
             "hf_pending_credits"),
            ({"title": "ZeroGPU illegal duration", "error":
              "The requested GPU duration (90s) is larger than the maximum allowed"},
             "hf_illegal_duration"),
            ({"title": "ZeroGPU quota exceeded", "error":
              "Space app has reached its GPU limit."}, "hf_space_quota_exceeded"),
            ({"error": "No GPU was available after 60s"}, "hf_busy"),
            ({"error": "GPU task aborted"}, "hf_worker_failed"),
            ({"error": "Live GPU session failed; check Space logs and quota. "
              "hf_synthetic_secret https://private.invalid/" + "a" * 64}, "hf_inference_failed"),
            (None, "hf_inference_failed"),
        ]
        for payload, expected in cases:
            with (
                self.subTest(code=expected),
                self.assertLogs(hf_services.logger, "WARNING") as logs,
            ):
                error = hf_services.provider_error(payload)
            self.assertEqual(error.code, expected)
            self.assertEqual(error.status, 503)
            for secret in ("hf_synthetic_secret", "private.invalid", "a" * 64):
                self.assertNotIn(secret, error.detail + " ".join(logs.output))
            if expected == "hf_quota_exceeded":
                self.assertIn("90", error.detail)
                self.assertIn("78", error.detail)
                self.assertIn("requested_seconds=90 remaining_seconds=78", logs.output[0])

    def test_bounds_and_auth(self):
        """Functionality: Bound bytes/deadline and reject anonymous identity before network use.
        Inputs: excess body, expired time, missing/whitespace token; Outputs: explicit failures.
        Logic: actual chunks/exchange boundaries; Constraints: no sleeps, network or GPU calls.
        """
        response = Mock()
        response.read1.return_value = b"1234"
        with self.assertRaises(ValueError):
            list(hf_services.chunks(response, None, time.monotonic() + 1, 3))
        with self.assertRaises(TimeoutError):
            list(hf_services.chunks(response, None, time.monotonic() - 1, 3))
        with patch.object(hf_services.http.client, "HTTPSConnection") as factory:
            for token in ("", None, "hf_invalid token"):
                with self.assertRaises(DetectionError):
                    hf_services.exchange("POST", "/test", b"{}", time.monotonic() + 1, token=token)
            factory.assert_not_called()
