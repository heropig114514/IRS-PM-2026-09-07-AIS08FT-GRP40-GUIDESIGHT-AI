"""Responsibilities: 地图页面、同源输入边界和 ORS 传输/容量/契约测试。
Implementation: 页面读取真实文件；上游 HTTP 仅用 mock，密钥是假值；不调用 ORS 或设备位置。
Related Modules: core.maps/services；真实上游另做联调。
Declaration Index: route_fixture — 合成上游；MapsTests — HTTP；
MapsTests.test_page — 策略、双语及导航资源接入；
MapsTests.test_config — 凭据边界；MapsTests.test_route_input — 输入；
MapsTests.test_route_result — 路线适配；
ServiceTests — 服务；ServiceTests.setUp — 重置限流；ServiceTests.test_coordinates — 坐标；
ServiceTests.test_normalize — 上游格式；ServiceTests.test_success — 传输契约；
ServiceTests.test_errors — 错误映射；ServiceTests.test_timeout_and_size — 资源边界；
ServiceTests.test_rate_and_capacity — 限流和并发；ServiceTests.test_missing_key — 配置缺失。
Variable Index: None；fixture 位置为合成测试点，不模拟当前设备。
"""

import copy
import json
import re
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from . import services


def route_fixture():
    """Functionality: 构造最小有效 ORS GeoJSON。
    Inputs: None；Outputs: 独立字典；Logic: 两点、出发/到达、真实字段结构。
    Constraints: 合成数据不证明道路存在、GPS 或公网调用成功。
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "geometry": {"type": "LineString", "coordinates": [[103.7, 1.3], [103.8, 1.4]]},
                "properties": {
                    "summary": {"distance": 100, "duration": 60},
                    "segments": [
                        {
                            "steps": [
                                {
                                    "instruction": "Depart",
                                    "type": 11,
                                    "distance": 100,
                                    "duration": 60,
                                    "way_points": [0, 1],
                                },
                                {
                                    "instruction": "Arrive",
                                    "type": 10,
                                    "distance": 0,
                                    "duration": 0,
                                    "way_points": [1, 1],
                                },
                            ]
                        }
                    ],
                },
            }
        ],
        "metadata": {"query": {"coordinates": "must not be forwarded"}},
    }


class MapsTests(SimpleTestCase):
    """Functionality: HTTP 页面和接口契约；Logic: 实际路由与文件，替换上游调用。
    Constraints: 无数据库、外网和浏览器权限。
    """

    def test_page(self):
        """Functionality: CSP/nonce/双语与导航资源；Inputs: 真实页面；Outputs: 策略与接入断言。
        Logic: HTML/CSP nonce 一致，语言选择器及地图/导航模块存在，Google 域和凭据已移除。
        Constraints: 不执行浏览器 CSP 或证明动态翻译；浏览器工具独立覆盖真实 DOM。
        """
        first = self.client.get("/maps/")
        second = self.client.get("/maps/")
        nonce = re.search(r'nonce="([^"]+)"', first.content.decode())[1]
        self.assertIn(f"'nonce-{nonce}'", first["Content-Security-Policy"])
        self.assertNotIn(nonce, second.content.decode())
        self.assertNotContains(first, "__CSP_NONCE__")
        self.assertNotIn("google", first["Content-Security-Policy"])
        self.assertNotIn("unpkg", self.client.get("/live/")["Content-Security-Policy"])
        self.assertIn("geolocation=(self)", first["Permissions-Policy"])
        self.assertContains(first, 'id="language"')
        self.assertContains(first, 'value="system"')
        self.assertContains(first, 'value="en"')
        self.assertContains(first, 'value="zh-CN"')
        self.assertContains(first, 'src="../assets/i18n.js"')
        for name in ["maps-page.js", "maps-client.js", "navigation.js", "navigation-ui.js",
                     "i18n.js", "translations.js"]:
            self.assertEqual(self.client.get(f"/assets/{name}").status_code, 200)

    def test_config(self):
        """Functionality: key 只留服务端；Inputs: 假 key 和空值环境；Outputs: 配置断言。
        Logic: 验证状态和字段，不公开 key；Constraints: 不验证真实认证。
        """
        for key, status in [("fake-ors-key", 200), ("", 503)]:
            with patch.dict("os.environ", {"ORS_API_KEY": key}):
                response = self.client.get("/maps/config/")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["configured"], bool(key))
                if key:
                    self.assertNotContains(response, key)
                self.assertNotIn("apiKey", response.json())
                self.assertEqual(response["Cache-Control"], "no-store")

    @patch("core.maps.walking_route")
    def test_route_input(self, upstream):
        """Functionality: 拒绝跨源、非法/过大正文及坐标；Inputs: 请求，mock 上游。
        Outputs: 各错误状态，上游未调用；Logic: 输入拒绝先于外部请求。
        Constraints: Origin 可被非浏览器伪造，同源不是认证。
        """
        base = {"HTTP_HOST": "127.0.0.1:8766", "HTTP_ORIGIN": "http://127.0.0.1:8766"}
        valid = {"start": [103.7, 1.3], "end": [103.8, 1.4]}
        self.assertEqual(
            self.client.post("/maps/route/", valid, content_type="application/json").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/maps/route/",
                valid,
                content_type="application/json",
                **{**base, "HTTP_ORIGIN": "https://evil.example"},
            ).status_code,
            403,
        )
        for data in [
            {"start": [True, 1], "end": [1, 2]},
            {"start": [181, 1], "end": [1, 2]},
            {"start": [1, 2], "end": [1, 2]},
            {"start": [1, 2]},
            [],
            {"start": [10**500, 2], "end": [1, 2]},
        ]:
            self.assertEqual(
                self.client.post(
                    "/maps/route/", data, content_type="application/json", **base
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                "/maps/route/", "invalid", content_type="application/json", **base
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/maps/route/", "x" * 2049, content_type="application/json", **base
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post("/maps/route/", "{}", content_type="text/plain", **base).status_code,
            415,
        )
        self.assertEqual(self.client.get("/maps/route/").status_code, 405)
        upstream.assert_not_called()

    @patch("core.maps.walking_route")
    def test_route_result(self, upstream):
        """Functionality: 适配服务结果与错误；Inputs: 规范 fixture/RouteError mock。
        Outputs: 数值顺序/状态/诊断标识；Logic: HTTP 边界不暴露凭据。
        Constraints: mocked 路线不代表真实 ORS 联通。
        """
        options = {
            "content_type": "application/json",
            "HTTP_HOST": "127.0.0.1:8766",
            "HTTP_ORIGIN": "http://127.0.0.1:8766",
        }
        upstream.return_value = services.normalize_route(route_fixture())
        response = self.client.post(
            "/maps/route/", {"start": [103.7, 1.3], "end": [103.8, 1.4]}, **options
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(upstream.call_args.args[:2], ([103.7, 1.3], [103.8, 1.4]))
        self.assertEqual(len(response.json()["request_id"]), 12)
        upstream.side_effect = services.RouteError("ors_timeout", "safe diagnostic", 504)
        self.assertEqual(
            self.client.post(
                "/maps/route/", {"start": [103.7, 1.3], "end": [103.8, 1.4]}, **options
            ).status_code,
            504,
        )


@patch.dict("os.environ", {"ORS_API_KEY": "fake-ors-key"})
class ServiceTests(SimpleTestCase):
    """Functionality: 服务校验、传输和限流；Logic: 替换 HTTPSConnection，保留服务逻辑。
    Constraints: 假 key 不发往外部；生产默认边界不修改。
    """

    def setUp(self):
        """Functionality: 清空限流测试状态；Inputs: self/模块队列；Outputs: 空队列。
        Logic: 防止测试间污染；Constraints: 不调整容量、额度或生产时间。
        """
        services.request_times.clear()

    def test_coordinates(self):
        """Functionality: 严格数值边界；Inputs: 合法边界及异常值；Outputs: 坐标/异常断言。
        Logic: bool、NaN、无穷和超大整数均拒绝；Constraints: 不模拟位置设备。
        """
        self.assertEqual(services.validate_coordinate([-180, 90]), [-180.0, 90.0])
        for value in [
            [True, 1],
            [float("nan"), 1],
            [float("inf"), 1],
            [10**500, 1],
            ["1", 2],
            [1],
            None,
        ]:
            with self.assertRaises(ValueError):
                services.validate_coordinate(value)

    def test_normalize(self):
        """Functionality: 返回契约及损坏数据；Inputs: 合成 fixture；Outputs: 规范字段/502。
        Logic: 验证 metadata 清理和几何/步骤边界；Constraints: 无真实上游。
        """
        route = services.normalize_route(route_fixture())
        self.assertNotIn("metadata", route)
        self.assertEqual(route["steps"][0]["type"], 11)
        damaged = copy.deepcopy(route_fixture())
        damaged["features"][0]["properties"]["segments"][0]["steps"][0]["way_points"] = [0, 99]
        for data in [{}, {"type": "FeatureCollection", "features": []}, damaged]:
            with self.assertRaises(services.RouteError) as caught:
                services.normalize_route(data)
            self.assertEqual(caught.exception.code, "ors_invalid_response")

    @patch("core.services.http.client.HTTPSConnection")
    def test_success(self, connection):
        """Functionality: 固定主机/路径/Authorization 和一次请求；Inputs: HTTP mock。
        Outputs: 超时参数/结果/关闭断言；Logic: 真实解析 fixture 原始字节。
        Constraints: 不证明 TLS/公网或真实认证。
        """
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(route_fixture()).encode()
        route = services.walking_route([103.7, 1.3], [103.8, 1.4], "test")
        self.assertEqual(route["summary"]["distance_m"], 100)
        connection.assert_called_once_with("api.heigit.org", timeout=20)
        request = connection.return_value.request
        request.assert_called_once()
        self.assertEqual(
            request.call_args.args,
            ("POST", "/openrouteservice/v2/directions/foot-walking/geojson"),
        )
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "fake-ors-key")
        self.assertEqual(request.call_args.kwargs["headers"]["Accept"], "application/geo+json")
        self.assertEqual(
            json.loads(request.call_args.kwargs["body"])["coordinates"],
            [[103.7, 1.3], [103.8, 1.4]],
        )
        connection.return_value.close.assert_called_once()

    @patch("core.services.http.client.HTTPSConnection")
    def test_errors(self, connection):
        """Functionality: 认证/配额/不可用/重定向明确失败；Inputs: HTTP 状态 mock。
        Outputs: 错误码与每次单调用；Logic: 不跟随重定向或重试。
        Constraints: 未调用真实 ORS。
        """
        for status, code in [
            (403, "ors_authentication_failed"),
            (429, "ors_quota_exceeded"),
            (400, "ors_route_unavailable"),
            (500, "ors_upstream_error"),
            (302, "ors_upstream_error"),
        ]:
            connection.reset_mock()
            connection.return_value.getresponse.return_value.status = status
            with self.assertRaises(services.RouteError) as caught:
                services.walking_route([1, 2], [2, 3], "test")
            self.assertEqual(caught.exception.code, code)
            connection.return_value.request.assert_called_once()
            connection.return_value.close.assert_called_once()

    @patch("core.services.http.client.HTTPSConnection")
    def test_timeout_and_size(self, connection):
        """Functionality: 网络超时和响应尺寸上限；Inputs: HTTP mock。
        Outputs: 明确错误和槽位释放；Logic: 异常不吞掉或改成功。
        Constraints: mock 超时非真实网络计时。
        """
        connection.return_value.request.side_effect = TimeoutError()
        with self.assertRaises(services.RouteError) as caught:
            services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.status, 504)
        connection.return_value.request.side_effect = None
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b"x" * (services.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(services.RouteError) as caught:
            services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.code, "ors_response_too_large")
        self.assertTrue(services.route_slots.acquire(blocking=False))
        self.assertTrue(services.route_slots.acquire(blocking=False))
        services.route_slots.release()
        services.route_slots.release()

    @patch("core.services.http.client.HTTPSConnection")
    def test_rate_and_capacity(self, connection):
        """Functionality: 限流/容量拒绝先于网络；Inputs: 模块槽位和队列状态。
        Outputs: 两个 429、上游未调用；Logic: finally 恢复占用。
        Constraints: 只验证单 worker，不宣称跨 worker 限流。
        """
        services.request_times.extend([time.monotonic()] * services.MAX_REQUESTS_PER_MINUTE)
        with self.assertRaises(services.RouteError) as caught:
            services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.code, "route_rate_limit")
        services.request_times.clear()
        services.route_slots.acquire()
        services.route_slots.acquire()
        try:
            with self.assertRaises(services.RouteError) as caught:
                services.walking_route([1, 2], [2, 3], "test")
            self.assertEqual(caught.exception.code, "route_capacity")
        finally:
            services.route_slots.release()
            services.route_slots.release()
        connection.assert_not_called()

    @patch("core.services.http.client.HTTPSConnection")
    def test_missing_key(self, connection):
        """Functionality: 无 key 时失败且不发请求；Inputs: 显式空 key 环境。
        Outputs: 503 与零调用；Logic: 保留配置失败语义；Constraints: 不探测其他项目凭据。
        """
        with patch.dict("os.environ", {"ORS_API_KEY": ""}):
            with self.assertRaises(services.RouteError) as caught:
                services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.status, 503)
        connection.assert_not_called()
