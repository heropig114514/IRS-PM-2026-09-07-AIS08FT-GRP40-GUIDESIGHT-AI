"""Responsibilities: 验证地图 HTTP 配置边界和 nonce 策略。
Implementation: 真实文件和视图；仅替换进程 key 环境，不调用 Google 或设备定位。
Related Modules: core.maps, frontend/maps.html。
Declaration Index: MapsTests — HTTP 契约；MapsTests.test_nonce_and_isolated_policy — 页面策略；
MapsTests.test_config_missing — 明确缺失；MapsTests.test_browser_key — 浏览器配置；
MapsTests.test_assets — 地图资源。
Variable Index: None。
"""

import re
from unittest.mock import patch

from django.test import SimpleTestCase


class MapsTests(SimpleTestCase):
    """Functionality: 验证页面和配置契约。
    Logic: Django 客户端请求实际路由；无数据库、外网或浏览器。
    Constraints: 测试 key 是固定假值，不能验证 Google 认证。
    """

    def test_nonce_and_isolated_policy(self):
        """Functionality: 确认 nonce 每响应独立且允许定位和 Google 域。
        Inputs: 两次地图页面及一次摄像头请求；无 mock。
        Outputs: nonce/策略断言；Logic: 比对 HTML 与 CSP 并验证原策略保留。
        Constraints: 不是浏览器 CSP 执行验证。
        """
        first = self.client.get("/maps/")
        second = self.client.get("/maps/")
        nonce = re.search(r'nonce="([^"]+)"', first.content.decode())[1]
        self.assertIn(f"'nonce-{nonce}'", first["Content-Security-Policy"])
        self.assertNotIn(nonce, second.content.decode())
        self.assertNotContains(first, "__CSP_NONCE__")
        self.assertIn("https://*.googleapis.com", first["Content-Security-Policy"])
        self.assertIn("geolocation=(self)", first["Permissions-Policy"])
        self.assertNotIn("googleapis", self.client.get("/")["Content-Security-Policy"])

    @patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": ""})
    def test_config_missing(self):
        """Functionality: 缺少 key 必须返回配置失败。
        Inputs: 显式空 key 环境；其余视图真实执行。
        Outputs: 503/code；Logic: 不能降级至虚构地图。
        Constraints: 不读取生产环境 key。
        """
        response = self.client.get("/maps/config/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"code": "maps_not_configured"})
        self.assertEqual(response["Cache-Control"], "no-store")

    @patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "test-browser-key"})
    def test_browser_key(self):
        """Functionality: 配置公开浏览器 key 时只返回该值。
        Inputs: 合成 key 环境；Outputs: JSON/405 断言。
        Logic: 检查字段全集和只读请求约束。
        Constraints: 合成 key 不验证外部 SDK、计费或限制。
        """
        self.assertEqual(self.client.get("/maps/config/").json(), {"apiKey": "test-browser-key"})
        self.assertEqual(self.client.post("/maps/config/").status_code, 405)

    def test_assets(self):
        """Functionality: 地图模块位于现有资源白名单。
        Inputs: 两个固定模块路径；无 mock。
        Outputs: 200 与 MIME；Logic: 读取实际源码。
        Constraints: 不执行 JavaScript。
        """
        for name in ["maps-page.js", "maps-client.js"]:
            response = self.client.get(f"/assets/{name}")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/javascript", response["Content-Type"])
