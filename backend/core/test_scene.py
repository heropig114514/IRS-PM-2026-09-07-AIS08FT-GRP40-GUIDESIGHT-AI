"""Responsibilities: 场景描述输入边界、提示词、上游传输/错误映射、限流与密钥保护测试。
Implementation: 仅 mock http.client.HTTPSConnection 与服务函数；假 key、合成 JPEG 字节，不联网。
Related Modules: core.scene、core.vlm；真实 DashScope 联调另行验证。
Declaration Index: jpeg_b64 — 合成图像；scene — 合法请求体；completion — 合成上游响应；
fake_connection — 模拟连接；
SceneHttpTests — HTTP 适配；SceneHttpTests.setUp/post/test_config/test_input_boundaries/
test_success_and_errors/test_max_size_request；
VlmServiceTests — 服务；VlmServiceTests.setUp/test_validation/test_prompt_has_no_invented_distance/
test_transport_contract/test_status_mapping/test_response_errors/test_transport_errors/
test_rate_and_capacity/test_missing_or_bad_config/test_key_not_in_logs_or_response；
box — 合成 PRS1 检测框；BoxesToObjectsTests — 检测框映射（样例同前端测试）；
BoxesToObjectsTests.test_mapping_and_omission/test_ranking_and_truncation/test_invalid_input；
DescribeHelperTests — 高层封装；DescribeHelperTests.setUp/test_describe_success/
test_describe_errors_propagate/test_describe_sync/test_default_model。
Variable Index: KEY — 假密钥；ORIGIN/HOST — 同源请求头；SECRET_QUESTION — 日志泄漏探针。
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
    """Functionality: 构造 SOI/EOI 完整的合成 JPEG base64。
    Inputs: size 总字节数；Outputs: base64 字符串；Logic: FF D8 + 填充 + FF D9。
    Constraints: 不是可解码图片，只满足接口的标记检查。
    """
    return base64.b64encode(b"\xff\xd8" + b"\x00" * (size - 4) + b"\xff\xd9").decode()


def completion(text="前方有一辆车。", usage=True):
    """Functionality: 构造 OpenAI 兼容上游响应字节。
    Inputs: 文本、是否带 usage；Outputs: JSON bytes；Constraints: 合成数据不证明真实模型行为。
    """
    data = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    if usage:
        data["usage"] = {"prompt_tokens": 321, "completion_tokens": 18}
    return json.dumps(data).encode()


def fake_connection(status=200, body=None):
    """Functionality: 构造可替换 HTTPSConnection 的 mock。
    Inputs: 状态码和响应字节；Outputs: (构造器 mock, 连接 mock)。
    Logic: response.read 尊重 amt 上限；Constraints: 不建立 socket。
    """
    payload = completion() if body is None else body
    connection = MagicMock()
    connection.getresponse.return_value.status = status
    connection.getresponse.return_value.read.side_effect = lambda amt=-1: payload[:amt]
    return MagicMock(return_value=connection), connection


def scene(**extra):
    """Functionality: 构造规范化前的合法请求体；Outputs: 字典；Constraints: extra 覆盖字段。"""
    return {"image_jpeg_base64": jpeg_b64(), "language": "zh-CN", **extra}


@patch.dict("os.environ", {"DASHSCOPE_API_KEY": KEY})
class SceneHttpTests(SimpleTestCase):
    """Functionality: HTTP 契约；Logic: 真实路由，替换 describe_scene 或 HTTPS 连接。
    Constraints: 无数据库、外网。
    """

    def setUp(self):
        """Functionality: 清空限流状态；Outputs: 空队列；Constraints: 不改变生产额度。"""
        vlm.request_times.clear()

    def post(self, data, **extra):
        """Functionality: 以同源 JSON 发 POST；Inputs: 数据与头覆盖；Outputs: 响应。"""
        options = {"content_type": "application/json", **ORIGIN, **extra}
        return self.client.post("/scene/describe/", data, **options)

    def test_config(self):
        """Functionality: 配置状态不泄漏 key；Inputs: 有/无 key；Outputs: 200/503 断言。
        Logic: 响应只含 provider/model/configured/code；Constraints: 不验证真实认证。
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
        """Functionality: 在调用上游前拒绝非法请求；Inputs: 各类坏请求；Outputs: 状态/code。
        Logic: Origin、Content-Type、大小、JSON、图像、字段逐项校验；Constraints: 上游从未被调用。
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
        """Functionality: 契约上限请求回归；Inputs: 1 MiB JPEG、50 对象、200 字问题。
        Outputs: 经真实视图返回 200，请求体在 1.5 MiB 内且上游收到全部对象。
        Logic: mock 上游连接；Constraints: 不证明真实模型接受该规模。
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
        """Functionality: 成功契约与全部错误码到状态映射；Inputs: mock HTTPS；Outputs: JSON 断言。
        Logic: 经真实视图→服务→mock 连接；Constraints: mock 不代表真实 DashScope 行为。
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
    """Functionality: 服务校验、提示词、传输、限流；Logic: 替换 HTTPSConnection，保留服务逻辑。
    Constraints: 假 key 不发往外部；生产默认边界不修改。
    """

    def setUp(self):
        """Functionality: 清空限流状态；Outputs: 空队列；Constraints: 不改变额度和并发。"""
        vlm.request_times.clear()

    def test_validation(self):
        """Functionality: 规范化输出；Inputs: 合法对象含 null 字段；Outputs: 省略 null、舍入数值。
        Logic: question strip，objects 缺省为空；Constraints: 不解码像素。
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
        """Functionality: 提示词只包含提供的距离；Inputs: 一个有距离、一个无距离的对象。
        Outputs: JSON 中仅一个 distance_m；Logic: 系统提示含禁止编造规则与语言指令。
        Constraints: 只检查提示词，不能证明模型遵守。
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
        self.assertIn("do not state any number of meters", system)
        self.assertIn("Never estimate distances", system)
        objects = json.loads(text.split("none provided): ", 1)[1])
        self.assertEqual(objects, [{"label": "car", "distance_m": 4.0}, {"label": "tree"}])
        self.assertEqual(text.count("distance_m"), 2)  # one header mention + one object field
        self.assertIn("Describe the scene ahead.", text)
        empty = vlm.build_messages(vlm.validate_scene_request(scene(language="en-US")))
        self.assertIn("English", empty[0]["content"])
        self.assertTrue(empty[1]["content"][1]["text"].endswith("provided): []"))

    def test_transport_contract(self):
        """Functionality: 请求目标、头、体与成功结果；Inputs: mock 连接；Outputs: 参数断言。
        Logic: 自定义 host/model 生效，Bearer 仅在头，参数 max_tokens=200/temperature=0.2。
        Constraints: 不验证 TLS 或真实认证。
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
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(result["model"], "qwen-vl-test")
        connection.close.assert_called_once()
        connection.getresponse.return_value.read.assert_called_once_with(262145)
        self.assertEqual(vlm.vlm_slots._value, 2)

    def test_status_mapping(self):
        """Functionality: 上游状态不被重试；Inputs: 500；Outputs: 仅一次请求且槽位释放。
        Logic: 单次 request 调用；Constraints: 状态码映射另见 HTTP 测试。
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
        """Functionality: 响应体异常映射；Inputs: 非 JSON/结构缺失/空文本/超限；Outputs: code。
        Logic: 全部 502；usage 异常降为 None；Constraints: 不生成替代文本。
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
        """Functionality: 超时与网络错误映射；Inputs: 抛异常的连接；Outputs: 504/502 且释放槽位。
        Logic: TimeoutError→vlm_timeout，OSError→vlm_upstream_error；Constraints: 不重试。
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
        """Functionality: 每分钟 10 次与 2 并发；Inputs: 预置队列/占满槽位；
        Outputs: 429 且不调用上游。
        Logic: 限流计数独立于 ORS；Constraints: 使用单调时钟近似，不修改生产常量。
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
        """Functionality: 配置缺失/非法 host 与 model；Inputs: 环境；Outputs: 503，上游未调用。
        Logic: host 含路径/端口/协议视为非法，不回退默认；Constraints: 仅格式校验。
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
        """Functionality: 日志不含密钥/图像/问题/回答；Inputs: 成功与失败调用；Outputs: 日志断言。
        Logic: assertLogs 收集 core.vlm 与 core.scene；日志含 id、状态、token、对象数。
        Constraints: 只覆盖这两个 logger 的当前日志语句。
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
    """Functionality: 构造合成 PRS1 检测框（与前端测试 box() 同形）。
    Inputs: 覆盖字段；Outputs: 字典；Constraints: 数值为合成数据。
    """
    return {"class_id": 0, "label": "person", "score": 0.9, "xyxy": [0, 0, 10, 10],
            "distance_m": 3, "track_id": 7, "motion_state": "approaching",
            "relative_speed_mps": -1, "smoothed_distance_m": 3, "direction": "front",
            "ttc_sec": 2, "risk_score": 80, "risk_level": "high", "alert_priority": 3,
            "alert_required": True, "risk_reasons": ["x"], **extra}


class BoxesToObjectsTests(SimpleTestCase):
    """Functionality: boxes_to_objects 与前端 mapObjects 的规则一致性；Logic: 同一批样例。
    Constraints: 纯函数测试，无网络。
    """

    def test_mapping_and_omission(self):
        """Functionality: 字段映射与缺失省略；Inputs: 完整框、仅 label/score 框、非法框。
        Outputs: 与前端断言相同的对象；Logic: score->confidence，不用 smoothed_distance_m。
        Constraints: 另验证 None/非 dict/非法枚举/无效距离被省略而非编造。
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
            vlm.validate_scene_request(scene(objects=[item]))  # 映射结果必被接口接受

    def test_ranking_and_truncation(self):
        """Functionality: 排序与截断；Inputs: 优先级/距离/无距离混合，120 个框。
        Outputs: urgent,near,far,nodist；50 个且首个最近；Logic: 稳定排序，输入不被修改。
        Constraints: 同优先级无距离者最后，平局按 score 再原序。
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
        """Functionality: 非列表输入视为空；Inputs: None/dict/str；Outputs: []。"""
        for value in (None, {"label": "a"}, "abc", 5):
            self.assertEqual(vlm.boxes_to_objects(value), [])
        self.assertEqual(vlm.boxes_to_objects(()), [])


@patch.dict("os.environ", {"DASHSCOPE_API_KEY": KEY})
class DescribeHelperTests(SimpleTestCase):
    """Functionality: describe/describe_sync 高层封装；Logic: mock http.client。
    Constraints: 不联网；SceneError 必须原样透传，不重试。
    """

    def setUp(self):
        """Functionality: 清空限流状态；Outputs: 空队列。"""
        vlm.request_times.clear()

    def test_default_model(self):
        """Functionality: 默认模型为 qwen3.7-flash；Constraints: 环境 VLM_MODEL 优先。"""
        self.assertEqual(vlm.DEFAULT_MODEL, "qwen3.7-flash")

    def test_describe_success(self):
        """Functionality: 成功路径字段与上游请求体；Inputs: 字节 JPEG + boxes + 问题。
        Outputs: 含 request_id 的 HTTP 同形结果；Logic: 图像 base64、objects 经映射进入提示词。
        Constraints: request_id 12 位十六进制，且与显式传入的一致。
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
        """Functionality: SceneError 原样透传且不重试；Inputs: 坏图像/坏问题/上游 500/无 key。
        Outputs: 对应 code；Logic: 校验失败不触发网络，上游失败仅一次 request。
        Constraints: 不吞异常、不降级。
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
        """Functionality: 同步版本结果一致且禁止在事件循环内调用。
        Inputs: 普通线程调用与协程内调用；Outputs: 成功结果 / RuntimeError。
        Constraints: RuntimeError 先于任何网络活动。
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
