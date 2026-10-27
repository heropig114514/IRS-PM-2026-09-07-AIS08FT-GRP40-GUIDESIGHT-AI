"""Responsibilities: Test scene-description input boundaries, prompts, upstream transport/error
mapping, rate limits, and key protection.
Implementation: Mock http.client.HTTPSConnection and service functions; use a fake key and synthetic
JPEG bytes; make no network calls.
Related Modules: core.scene and core.vlm; live DashScope integration is verified separately.
Declaration Index: jpeg_b64 — synthetic image; scene — valid request body; completion — synthetic
upstream response;
fake_connection — mock connection;
SceneHttpTests — HTTP adapter; SceneHttpTests.setUp/post/test_config/test_input_boundaries/
test_success_and_errors/test_max_size_request;
VlmServiceTests — service;
VlmServiceTests.setUp/test_validation/test_prompt_has_no_invented_distance/
test_transport_contract/test_status_mapping/test_response_errors/test_transport_errors/
test_rate_and_capacity/test_missing_or_bad_config/test_key_not_in_logs_or_response;
box — synthetic PRS1 detection box; BoxesToObjectsTests — detection-box mapping (same examples as
frontend tests);
BoxesToObjectsTests.test_mapping_and_omission/test_ranking_and_truncation/test_invalid_input;
DescribeHelperTests — high-level helper; DescribeHelperTests.setUp/test_describe_success/
test_describe_errors_propagate/test_describe_sync/test_default_model.
Variable Index: KEY — fake key; ORIGIN/HOST — same-origin request headers; SECRET_QUESTION —
log-leak probe.
"""

import asyncio
import base64
import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from . import vlm

KEY = "fake-dashscope-key-0123"
ORIGIN = {"HTTP_HOST": "127.0.0.1:8766", "HTTP_ORIGIN": "http://127.0.0.1:8766"}
SECRET_QUESTION = "secret-question-probe"


def jpeg_b64(size=64):
    """Functionality: Build base64 for a synthetic JPEG with complete SOI/EOI markers.
    Inputs: size, total byte count. Outputs: Base64 string. Logic: FF D8 + padding + FF D9.
    Constraints: This is not a decodable image; it satisfies only the interface marker check.
    """
    return base64.b64encode(b"\xff\xd8" + b"\x00" * (size - 4) + b"\xff\xd9").decode()


def completion(text="前方有一辆车。", usage=True):
    """Functionality: Build response bytes for an OpenAI-compatible upstream.
    Inputs: Text and whether to include usage. Outputs: JSON bytes.
    Constraints: Synthetic data does not prove real model behavior.
    """
    data = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    if usage:
        data["usage"] = {"prompt_tokens": 321, "completion_tokens": 18}
    return json.dumps(data).encode()


def fake_connection(status=200, body=None):
    """Functionality: Build a mock that can replace HTTPSConnection.
    Inputs: Status code and response bytes. Outputs: (constructor mock, connection mock).
    Logic: response.read respects the amt limit. Constraints: No socket is opened.
    """
    payload = completion() if body is None else body
    connection = MagicMock()
    connection.getresponse.return_value.status = status
    connection.getresponse.return_value.read.side_effect = lambda amt=-1: payload[:amt]
    return MagicMock(return_value=connection), connection


def scene(**extra):
    """Functionality: Build a valid request body before normalization. Outputs: Dictionary.
    Constraints: extra overrides fields."""
    return {"image_jpeg_base64": jpeg_b64(), "language": "zh-CN", **extra}


@patch.dict("os.environ", {"DASHSCOPE_API_KEY": KEY})
class SceneHttpTests(SimpleTestCase):
    """Functionality: Verify HTTP contracts using real routes while replacing describe_scene or the
    HTTPS connection.
    Constraints: No database or external network.
    """

    def setUp(self):
        """Functionality: Clear rate-limit state. Outputs: Empty queue. Constraints: Production
        quota is unchanged."""
        vlm.request_times.clear()

    def post(self, data, **extra):
        """Functionality: Send a same-origin JSON POST. Inputs: Data and header overrides. Outputs:
        Response."""
        options = {"content_type": "application/json", **ORIGIN, **extra}
        return self.client.post("/scene/describe/", data, **options)

    def test_config(self):
        """Functionality: Ensure configuration status does not leak the key. Inputs: Configured and
        missing key.
        Outputs: Assertions for 200/503. Logic: Response contains only
        provider/model/configured/code.
        Constraints: Does not verify real authentication.
        """
        response = self.client.get("/scene/config/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "provider": "dashscope",
                "model": vlm.DEFAULT_MODEL,
                "configured": True,
                "code": "ready",
            },
        )
        self.assertNotContains(response, KEY)
        self.assertEqual(response["Cache-Control"], "no-store")
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            response = self.client.get("/scene/config/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "vlm_not_configured")
        self.assertFalse(response.json()["configured"])
        self.assertEqual(self.client.post("/scene/config/").status_code, 405)

    @patch("core.scene.describe_scene")
    def test_input_boundaries(self, upstream):
        """Functionality: Reject invalid requests before calling upstream. Inputs: Invalid request
        variants. Outputs: Status/code.
        Logic: Validate Origin, Content-Type, size, JSON, image, and fields individually.
        Constraints: Upstream is never called.
        """
        valid = scene()
        no_origin = self.client.post("/scene/describe/", valid, content_type="application/json")
        self.assertEqual(no_origin.status_code, 403)
        self.assertEqual(no_origin.json()["code"], "origin_rejected")
        evil = self.post(valid, HTTP_ORIGIN="https://evil.example")
        self.assertEqual(evil.status_code, 403)
        self.assertEqual(self.post("{}", content_type="text/plain").status_code, 415)
        self.assertEqual(self.client.get("/scene/describe/").status_code, 405)
        large = self.post("x" * (1572864 + 1))
        self.assertEqual((large.status_code, large.json()["code"]), (413, "request_too_large"))
        self.assertEqual(self.post("not json").json()["code"], "invalid_scene_input")
        self.assertEqual(self.post([]).json()["code"], "invalid_scene_input")
        bad_images = [
            {},
            {"image_jpeg_base64": ""},
            {"image_jpeg_base64": 5},
            {"image_jpeg_base64": "***not-base64***"},
            {"image_jpeg_base64": base64.b64encode(b"\x89PNG....").decode()},
            {"image_jpeg_base64": base64.b64encode(b"\xff\xd8abc").decode()},
            {
                "image_jpeg_base64": base64.b64encode(
                    b"\xff\xd8" + b"0" * 1048576 + b"\xff\xd9"
                ).decode()
            },
        ]
        for image in bad_images:
            response = self.post({"language": "zh-CN", **image})
            self.assertEqual(
                (response.status_code, response.json()["code"]), (400, "invalid_image")
            )
        good_object = {"label": "car", "confidence": 0.9, "distance_m": 3.2, "direction": "front"}
        bad_inputs = [
            {"language": "fr"},
            {"language": None},
            {"extra": 1},
            {"question": ""},
            {"question": "   "},
            {"question": "a" * 201},
            {"question": 5},
            {"objects": "car"},
            {"objects": [good_object] * 51},
            {"objects": [{**good_object, "unknown": 1}]},
            {"objects": [{**good_object, "label": ""}]},
            {"objects": [{**good_object, "label": "x" * 41}]},
            {"objects": [{**good_object, "label": "a\nb"}]},
            {"objects": [{**good_object, "confidence": 1.5}]},
            {"objects": [{**good_object, "confidence": True}]},
            {"objects": [{**good_object, "distance_m": 0}]},
            {"objects": [{**good_object, "distance_m": 0.0001}]},
            {"objects": [{**good_object, "distance_m": -1}]},
            {"objects": [{**good_object, "distance_m": "3"}]},
            {"objects": [{**good_object, "direction": "up"}]},
            {"objects": [{**good_object, "risk_level": "extreme"}]},
            {"objects": [{**good_object, "motion_state": "flying"}]},
            {"objects": [{**good_object, "track_id": -1}]},
            {"objects": [{**good_object, "track_id": True}]},
            {"objects": ["car"]},
        ]
        for extra in bad_inputs:
            body = scene()
            if extra.get("language", 1) is None or "language" in extra:
                body["language"] = extra["language"]
            else:
                body.update(extra)
            response = self.post(body)
            self.assertEqual(
                (response.status_code, response.json()["code"]),
                (400, "invalid_scene_input"),
                extra,
            )
        upstream.assert_not_called()

    def test_max_size_request(self):
        """Functionality: Check the maximum-size request contract.
        Inputs: 1 MiB JPEG, 50 objects, and a 200-character question.
        Outputs: The real view returns 200, the body stays under 1.5 MiB, and upstream receives all
        objects.
        Logic: Mock the upstream connection. Constraints: Does not prove a real model accepts this
        size.
        """
        objects = [
            {
                "label": f"object-{index:02d}-" + "x" * 20,
                "confidence": 0.5,
                "distance_m": 1.5,
                "direction": "front_left",
                "risk_level": "medium",
                "motion_state": "approaching",
                "track_id": index,
            }
            for index in range(50)
        ]
        image = base64.b64encode(b"\xff\xd8" + b"\x00" * (1048576 - 4) + b"\xff\xd9").decode()
        payload = json.dumps(
            {
                "image_jpeg_base64": image,
                "language": "en-US",
                "question": "问" * 200,
                "objects": objects,
            }
        )
        self.assertLess(len(payload), 1572864)
        constructor, connection = fake_connection()
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        sent = json.loads(connection.request.call_args.kwargs["body"])
        text = sent["messages"][1]["content"][1]["text"]
        self.assertEqual(len(json.loads(text.split("none provided): ", 1)[1])), 50)

    def test_success_and_errors(self):
        """Functionality: Verify success contract and status mapping for every error code.
        Inputs: Mock HTTPS. Outputs: JSON assertions.
        Logic: Exercise the real view and service through a mocked connection.
        Constraints: The mock does not represent real DashScope behavior.
        """
        constructor, _ = fake_connection()
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            response = self.post(scene(question="前面有什么？", objects=[{"label": "car"}]))
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(data),
            {"text", "model", "language", "elapsed_ms", "usage", "request_id"},
        )
        self.assertEqual(data["text"], "前方有一辆车。")
        self.assertEqual(data["model"], vlm.DEFAULT_MODEL)
        self.assertEqual(data["language"], "zh-CN")
        self.assertIsInstance(data["elapsed_ms"], int)
        self.assertEqual(data["usage"], {"input_tokens": 321, "output_tokens": 18})
        self.assertEqual(len(data["request_id"]), 12)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertNotIn(KEY, response.content.decode())
        cases = [
            (401, "vlm_authentication_failed", 502),
            (403, "vlm_authentication_failed", 502),
            (429, "vlm_quota_exceeded", 503),
            (500, "vlm_upstream_error", 502),
            (302, "vlm_upstream_error", 502),
        ]
        for status, code, expected in cases:
            vlm.request_times.clear()
            constructor, _ = fake_connection(status=status, body=b"{}")
            with patch("core.vlm.http.client.HTTPSConnection", constructor):
                response = self.post(scene())
            self.assertEqual((response.status_code, response.json()["code"]), (expected, code))
            self.assertEqual(len(response.json()["request_id"]), 12)
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            response = self.post(scene())
        self.assertEqual(
            (response.status_code, response.json()["code"]), (503, "vlm_not_configured")
        )


@patch.dict("os.environ", {"DASHSCOPE_API_KEY": KEY})
class VlmServiceTests(SimpleTestCase):
    """Functionality: Verify service validation, prompts, transport, and rate limits.
    Logic: Replace HTTPSConnection while retaining the service implementation.
    Constraints: The fake key is never sent externally; production defaults remain unchanged.
    """

    def setUp(self):
        """Functionality: Clear rate-limit state. Outputs: Empty queue. Constraints: Quota and
        concurrency are unchanged."""
        vlm.request_times.clear()

    def test_validation(self):
        """Functionality: Verify normalized output. Inputs: A valid object containing null fields.
        Outputs: Nulls omitted and numeric values rounded.
        Logic: Strip question whitespace and default objects to an empty list.
        Constraints: Does not decode pixels.
        """
        result = vlm.validate_scene_request(
            scene(
                question="  前面是什么？ ",
                objects=[
                    {
                        "label": " car ",
                        "confidence": 0.876,
                        "distance_m": 2.349,
                        "direction": None,
                        "risk_level": "high",
                        "motion_state": None,
                        "track_id": 7,
                    },
                    {"label": "person", "distance_m": None},
                    {"label": "cat", "distance_m": 0.04},
                ],
            )
        )
        self.assertEqual(result["question"], "前面是什么？")
        self.assertEqual(
            result["objects"],
            [
                {
                    "label": "car",
                    "confidence": 0.876,
                    "distance_m": 2.349,
                    "risk_level": "high",
                    "track_id": 7,
                },
                {"label": "person"},
                {"label": "cat", "distance_m": 0.04},
            ],
        )
        self.assertEqual(vlm.validate_scene_request(scene())["objects"], [])
        self.assertIsNone(vlm.validate_scene_request(scene(question=None))["question"])
        edge = base64.b64encode(b"\xff\xd8" + b"0" * (1048576 - 4) + b"\xff\xd9").decode()
        self.assertEqual(
            vlm.validate_scene_request({"image_jpeg_base64": edge, "language": "en-US"})[
                "language"
            ],
            "en-US",
        )

    def test_prompt_has_no_invented_distance(self):
        """Functionality: Ensure prompts include only supplied distances.
        Inputs: One object with a distance and one without. Outputs: Exactly one distance_m in JSON.
        Logic: The system prompt contains anti-fabrication rules and language instructions.
        Constraints: Checks the prompt only; it cannot prove model compliance.
        """
        normalized = vlm.validate_scene_request(
            scene(objects=[{"label": "car", "distance_m": 4.0}, {"label": "tree"}])
        )
        messages = vlm.build_messages(normalized)
        system = messages[0]["content"]
        user = messages[1]["content"]
        self.assertEqual(user[0]["type"], "image_url")
        self.assertTrue(user[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        text = user[1]["text"]
        self.assertIn("Simplified Chinese", system)
        self.assertIn("never say how far or near it is", system)
        self.assertIn("Movement comes only from motion_state", system)
        self.assertIn("Never estimate distances", system)
        objects = json.loads(text.split("none provided): ", 1)[1])
        self.assertEqual(objects, [{"label": "car", "distance_m": 4.0}, {"label": "tree"}])
        self.assertEqual(text.count("distance_m"), 2)  # one header mention + one object field
        self.assertIn("Describe the scene ahead.", text)
        empty = vlm.build_messages(vlm.validate_scene_request(scene(language="en-US")))
        self.assertIn("English", empty[0]["content"])
        self.assertTrue(empty[1]["content"][1]["text"].endswith("provided): []"))

    def test_transport_contract(self):
        """Functionality: Verify request target, headers, body, and success result using a mock
        connection.
        Outputs: Parameter assertions. Logic: Custom host/model are applied, Bearer appears only in
        headers,
        and max_tokens=200/temperature=0.2/enable_thinking=false.
        Constraints: Does not verify TLS or real authentication.
        """
        constructor, connection = fake_connection()
        env = {"DASHSCOPE_HOST": "dashscope.aliyuncs.com", "VLM_MODEL": "qwen-vl-test"}
        with (
            patch.dict("os.environ", env),
            patch("core.vlm.http.client.HTTPSConnection", constructor),
        ):
            result = vlm.describe_scene(vlm.validate_scene_request(scene()), "rid")
        constructor.assert_called_once_with("dashscope.aliyuncs.com", timeout=20)
        method, path = connection.request.call_args.args
        kwargs = connection.request.call_args.kwargs
        self.assertEqual((method, path), ("POST", "/compatible-mode/v1/chat/completions"))
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertNotIn(KEY, kwargs["body"])
        body = json.loads(kwargs["body"])
        self.assertEqual(body["model"], "qwen-vl-test")
        self.assertEqual((body["max_tokens"], body["temperature"]), (200, 0.2))
        self.assertIs(body["enable_thinking"], False)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(result["model"], "qwen-vl-test")
        connection.close.assert_called_once()
        connection.getresponse.return_value.read.assert_called_once_with(262145)
        self.assertEqual(vlm.vlm_slots._value, 2)

    def test_status_mapping(self):
        """Functionality: Ensure upstream status is not retried. Inputs: 500 response.
        Outputs: One request and a released capacity slot. Logic: Call request once.
        Constraints: HTTP tests cover status-code mapping separately.
        """
        constructor, connection = fake_connection(status=500)
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            with self.assertRaises(vlm.SceneError) as caught:
                vlm.describe_scene(vlm.validate_scene_request(scene()), "rid")
        self.assertEqual(caught.exception.code, "vlm_upstream_error")
        self.assertEqual(connection.request.call_count, 1)
        connection.close.assert_called_once()
        self.assertEqual(vlm.vlm_slots._value, 2)

    def test_response_errors(self):
        """Functionality: Map malformed response bodies. Inputs: Non-JSON, missing structure, empty
        text, or oversized body.
        Outputs: Error code. Logic: Map all cases to 502 and set malformed usage to None.
        Constraints: Do not generate replacement text.
        """
        parsed = scene()
        cases = {
            b"not json": "vlm_invalid_response",
            b"[]": "vlm_invalid_response",
            b'{"choices":[]}': "vlm_invalid_response",
            completion("   "): "vlm_invalid_response",
            json.dumps(
                {"choices": [{"message": {"content": [1]}}]}
            ).encode(): "vlm_invalid_response",
            b"x" * (vlm.MAX_RESPONSE_BYTES + 1): "vlm_response_too_large",
        }
        for body, code in cases.items():
            vlm.request_times.clear()
            constructor, _ = fake_connection(body=body)
            with patch("core.vlm.http.client.HTTPSConnection", constructor):
                with self.assertRaises(vlm.SceneError) as caught:
                    vlm.describe_scene(vlm.validate_scene_request(parsed), "rid")
            self.assertEqual((caught.exception.code, caught.exception.status), (code, 502))
        constructor, _ = fake_connection(body=completion(usage=False))
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            result = vlm.describe_scene(vlm.validate_scene_request(parsed), "rid")
        self.assertEqual(result["usage"], {"input_tokens": None, "output_tokens": None})

    def test_transport_errors(self):
        """Functionality: Map timeout and network errors. Inputs: A connection that raises an
        exception.
        Outputs: 504/502 and a released capacity slot.
        Logic: TimeoutError→vlm_timeout; OSError→vlm_upstream_error. Constraints: No retry.
        """
        for error, code, status in [
            (TimeoutError("slow"), "vlm_timeout", 504),
            (ConnectionResetError("reset"), "vlm_upstream_error", 502),
        ]:
            constructor, connection = fake_connection()
            connection.request.side_effect = error
            with patch("core.vlm.http.client.HTTPSConnection", constructor):
                with self.assertRaises(vlm.SceneError) as caught:
                    vlm.describe_scene(vlm.validate_scene_request(scene()), "rid")
            self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
            self.assertEqual(connection.request.call_count, 1)
            self.assertEqual(vlm.vlm_slots._value, 2)

    def test_rate_and_capacity(self):
        """Functionality: Verify 10 requests per minute and concurrency of 2.
        Inputs: A seeded queue and occupied capacity slots. Outputs: 429 without an upstream call.
        Logic: Rate counters are independent of ORS.
        Constraints: Use a monotonic-clock approximation without changing production constants.
        """
        parsed = vlm.validate_scene_request(scene())
        constructor, _ = fake_connection()
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            for _ in range(10):
                vlm.describe_scene(parsed, "rid")
            with self.assertRaises(vlm.SceneError) as caught:
                vlm.describe_scene(parsed, "rid")
            self.assertEqual(
                (caught.exception.code, caught.exception.status), ("vlm_rate_limit", 429)
            )
            self.assertEqual(constructor.call_count, 10)
            vlm.request_times.clear()
            self.assertTrue(vlm.vlm_slots.acquire(blocking=False))
            self.assertTrue(vlm.vlm_slots.acquire(blocking=False))
            try:
                with self.assertRaises(vlm.SceneError) as caught:
                    vlm.describe_scene(parsed, "rid")
                self.assertEqual(
                    (caught.exception.code, caught.exception.status), ("vlm_capacity", 429)
                )
            finally:
                vlm.vlm_slots.release()
                vlm.vlm_slots.release()
            self.assertEqual(constructor.call_count, 10)
            self.assertEqual(len(vlm.request_times), 0)

    def test_missing_or_bad_config(self):
        """Functionality: Verify missing configuration and invalid host/model values.
        Inputs: Environment. Outputs: 503; upstream remains uncalled.
        Logic: Treat a host containing path, port, or scheme as invalid and do not fall back to
        defaults.
        Constraints: Format validation only.
        """
        parsed = vlm.validate_scene_request(scene())
        constructor, _ = fake_connection()
        bad_envs = [
            {"DASHSCOPE_API_KEY": "  "},
            {"DASHSCOPE_HOST": "https://dashscope.aliyuncs.com"},
            {"DASHSCOPE_HOST": "evil.example/path"},
            {"DASHSCOPE_HOST": "host:8080"},
            {"VLM_MODEL": "bad model!"},
        ]
        for env in bad_envs:
            with (
                patch.dict("os.environ", env),
                patch("core.vlm.http.client.HTTPSConnection", constructor),
            ):
                with self.assertRaises(vlm.SceneError) as caught:
                    vlm.describe_scene(parsed, "rid")
            self.assertEqual(
                (caught.exception.code, caught.exception.status), ("vlm_not_configured", 503)
            )
        constructor.assert_not_called()

    def test_key_not_in_logs_or_response(self):
        """Functionality: Ensure logs omit keys, images, questions, and answers.
        Inputs: Successful and failed calls. Outputs: Log assertions.
        Logic: assertLogs captures core.vlm and core.scene; log records contain IDs, status, token
        counts, and object counts.
        Constraints: Covers the current log statements from these two loggers only.
        """
        constructor, _ = fake_connection()
        with (
            self.assertLogs("core", level="INFO") as logs,
            patch("core.vlm.http.client.HTTPSConnection", constructor),
        ):
            parsed = vlm.validate_scene_request(
                scene(question=SECRET_QUESTION, objects=[{"label": "car"}])
            )
            vlm.describe_scene(parsed, "rid123")
            constructor.return_value.getresponse.return_value.status = 401
            with self.assertRaises(vlm.SceneError):
                vlm.describe_scene(parsed, "rid456")
            constructor.return_value.request.side_effect = ConnectionResetError(KEY)
            with self.assertRaises(vlm.SceneError):
                vlm.describe_scene(parsed, "rid789")
        output = "\n".join(logs.output)
        for secret in (KEY, SECRET_QUESTION, parsed["image"], "前方有一辆车"):
            self.assertNotIn(secret, output)
        self.assertIn("id=rid123", output)
        self.assertIn("input_tokens=321 output_tokens=18 objects=1", output)


def box(**extra):
    """Functionality: Build a synthetic PRS1 detection box with the same shape as the frontend test
    helper.
    Inputs: Field overrides. Outputs: Dictionary. Constraints: Values are synthetic.
    """
    return {"class_id": 0, "label": "person", "score": 0.9, "xyxy": [0, 0, 10, 10],
            "distance_m": 3, "track_id": 7, "motion_state": "approaching",
            "relative_speed_mps": -1, "smoothed_distance_m": 3, "direction": "front",
            "ttc_sec": 2, "risk_score": 80, "risk_level": "high", "alert_priority": 3,
            "alert_required": True, "risk_reasons": ["x"], **extra}


class BoxesToObjectsTests(SimpleTestCase):
    """Functionality: Check rule consistency between boxes_to_objects and frontend mapObjects using
    shared examples.
    Constraints: Pure-function tests with no network access.
    """

    def test_mapping_and_omission(self):
        """Functionality: Verify field mapping and omission of missing fields.
        Inputs: Complete, label/score-only, and invalid boxes. Outputs: Objects matching frontend
        assertions.
        Logic: Map score to confidence and do not use smoothed_distance_m.
        Constraints: Also verify that None, non-dict, invalid enum, and invalid distance values are
        omitted rather than fabricated.
        """
        self.assertEqual(
            vlm.boxes_to_objects([box()])[0],
            {"label": "person", "confidence": 0.9, "distance_m": 3, "direction": "front",
             "risk_level": "high", "motion_state": "approaching", "track_id": 7},
        )
        self.assertEqual(
            vlm.boxes_to_objects([{"label": "car", "score": 0.5}]),
            [{"label": "car", "confidence": 0.5}],
        )
        smoothed_only = {"label": "car", "score": 0.5, "smoothed_distance_m": 2.0}
        self.assertEqual(
            vlm.boxes_to_objects([smoothed_only]), [{"label": "car", "confidence": 0.5}]
        )
        loose = box(distance_m=0, direction="up", risk_level=None, motion_state="x", track_id=-1)
        self.assertEqual(vlm.boxes_to_objects([loose]), [{"label": "person", "confidence": 0.9}])
        self.assertNotIn("distance_m", vlm.boxes_to_objects([box(distance_m=True)])[0])
        self.assertNotIn("track_id", vlm.boxes_to_objects([box(track_id=True)])[0])
        bad = [{"label": "", "score": 0.5}, {"label": "x" * 41, "score": 0.5}, None, "x",
               {"label": "a", "score": 1.5}, {"label": "a"}, {"label": "a", "score": True},
               {"label": "a\x00b", "score": 0.5}, {"label": "  ", "score": 0.5}]
        self.assertEqual(vlm.boxes_to_objects(bad), [])
        for item in vlm.boxes_to_objects([box(), box(label=" bike ", distance_m=0.0001)]):
            vlm.validate_scene_request(
                scene(objects=[item])
            )  # The mapped result must be accepted by the API.

    def test_ranking_and_truncation(self):
        """Functionality: Verify ranking and truncation with 120 mixed-priority, with-distance, and
        distance-free boxes.
        Outputs: urgent, near, far, nodist ordering; 50 items with the nearest first.
        Logic: Stable sorting without mutating the input.
        Constraints: Distance-free items sort last within priority; ties use score and then original
        order.
        """
        ranked = vlm.boxes_to_objects([
            box(label="far", alert_priority=0, distance_m=9),
            box(label="nodist", alert_priority=0, distance_m=None),
            box(label="near", alert_priority=0, distance_m=1),
            box(label="urgent", alert_priority=4, distance_m=20),
        ])
        self.assertEqual([item["label"] for item in ranked], ["urgent", "near", "far", "nodist"])
        ties = vlm.boxes_to_objects([
            box(label="a", distance_m=2, score=0.5), box(label="b", distance_m=2, score=0.9),
            box(label="c", distance_m=2, score=0.9),
        ])
        self.assertEqual([item["label"] for item in ties], ["b", "c", "a"])
        many = [box(label=f"l{i}", distance_m=i + 1, alert_priority=0) for i in range(120)]
        out = vlm.boxes_to_objects(many)
        self.assertEqual((len(out), out[0]["label"], len(many)), (vlm.MAX_OBJECTS, "l0", 120))

    def test_invalid_input(self):
        """Functionality: Treat non-list inputs as empty. Inputs: None, dict, or str. Outputs:
        []."""
        for value in (None, {"label": "a"}, "abc", 5):
            self.assertEqual(vlm.boxes_to_objects(value), [])
        self.assertEqual(vlm.boxes_to_objects(()), [])


@patch.dict("os.environ", {"DASHSCOPE_API_KEY": KEY})
class DescribeHelperTests(SimpleTestCase):
    """Functionality: Verify the describe/describe_sync high-level helpers using mocked http.client.
    Constraints: No network; SceneError must propagate unchanged without retries.
    """

    def setUp(self):
        """Functionality: Clear rate-limit state. Outputs: Empty queue."""
        vlm.request_times.clear()

    def test_default_model(self):
        """Functionality: Verify the default model is qwen3-vl-plus. Constraints: VLM_MODEL from the
        environment takes precedence."""
        self.assertEqual(vlm.DEFAULT_MODEL, "qwen3-vl-plus")

    def test_describe_success(self):
        """Functionality: Verify success-path fields and upstream request body.
        Inputs: JPEG bytes, boxes, and a question. Outputs: HTTP-shaped result containing
        request_id.
        Logic: Base64 image and mapped objects are passed into the prompt.
        Constraints: request_id is 12 hexadecimal characters and matches an explicitly supplied
        value.
        """
        constructor, connection = fake_connection()
        jpeg = base64.b64decode(jpeg_b64())
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            result = asyncio.run(vlm.describe(jpeg, [box()], "前面有什么？", "en-US"))
            again = asyncio.run(vlm.describe(jpeg, None, request_id="fixed"))
        self.assertEqual(
            set(result), {"text", "model", "language", "elapsed_ms", "usage", "request_id"}
        )
        self.assertRegex(result["request_id"], r"^[0-9a-f]{12}$")
        self.assertEqual(again["request_id"], "fixed")
        self.assertEqual((result["text"], result["language"]), ("前方有一辆车。", "en-US"))
        self.assertEqual(result["usage"], {"input_tokens": 321, "output_tokens": 18})
        body = json.loads(connection.request.call_args_list[0].kwargs["body"])
        user = body["messages"][1]["content"]
        self.assertIn(jpeg_b64(), user[0]["image_url"]["url"])
        self.assertIn("前面有什么？", user[1]["text"])
        self.assertIn('"label":"person"', user[1]["text"])
        empty = json.loads(connection.request.call_args_list[1].kwargs["body"])
        self.assertTrue(empty["messages"][1]["content"][1]["text"].endswith("provided): []"))

    def test_describe_errors_propagate(self):
        """Functionality: Propagate SceneError unchanged without retrying.
        Inputs: Invalid image/question, upstream 500, or missing key. Outputs: Corresponding code.
        Logic: Validation failures do not trigger network calls; upstream failures make one request.
        Constraints: Do not swallow errors or degrade behavior.
        """
        jpeg = base64.b64decode(jpeg_b64())
        constructor, connection = fake_connection(status=500)
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            for args, code in [
                ((b"not a jpeg",), "invalid_image"),
                (("text",), "invalid_image"),
                ((jpeg, None, "  "), "invalid_scene_input"),
                ((jpeg, None, None, "fr-FR"), "invalid_scene_input"),
            ]:
                with self.assertRaises(vlm.SceneError) as caught:
                    asyncio.run(vlm.describe(*args))
                self.assertEqual(caught.exception.code, code)
            constructor.assert_not_called()
            with self.assertRaises(vlm.SceneError) as caught:
                asyncio.run(vlm.describe(jpeg))
            self.assertEqual(
                (caught.exception.code, caught.exception.status), ("vlm_upstream_error", 502)
            )
            self.assertEqual(connection.request.call_count, 1)
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            with self.assertRaises(vlm.SceneError) as caught:
                asyncio.run(vlm.describe(jpeg))
            self.assertEqual(caught.exception.code, "vlm_not_configured")

    def test_describe_sync(self):
        """Functionality: Verify synchronous results and prohibit calls from within an event loop.
        Inputs: A normal thread call and a call from a coroutine. Outputs: Successful result or
        RuntimeError.
        Constraints: RuntimeError occurs before any network activity.
        """
        constructor, _ = fake_connection()
        jpeg = base64.b64decode(jpeg_b64())
        with patch("core.vlm.http.client.HTTPSConnection", constructor):
            result = vlm.describe_sync(jpeg, [box()], language="zh-CN")
            self.assertEqual(result["model"], vlm.DEFAULT_MODEL)
            self.assertIn("request_id", result)

            async def inside_loop():
                return vlm.describe_sync(jpeg)

            with self.assertRaises(RuntimeError):
                asyncio.run(inside_loop())
        self.assertEqual(constructor.call_count, 1)
