"""Responsibilities: 验证真实测试数据库、健康接口故障语义及错误响应边界。
Implementation: TestCase 使用隔离 SQLite；只对故障分支替换数据库游标。
Related Modules: core.views/errors, config.base；不访问外部数据库或 GitHub。
Declaration Index:
- HealthTests — 健康接口的成功、故障与方法限制。
- HealthTests.test_database_health — 真实测试数据库探测。
- HealthTests.test_database_failure — 模拟 DatabaseError 后保持 503 且记录日志。
- HealthTests.test_post_is_rejected — 拒绝健康接口写请求。
- HealthTests.test_admin_requires_login — 匿名管理后台重定向到登录页。
- ErrorHandlerTests — DRF 已知与未知异常处理。
- ErrorHandlerTests.test_validation_error_keeps_standard_response — 保留 DRF 400 契约。
- ErrorHandlerTests.test_unexpected_error_is_logged_and_redacted — 固定 500 并保留诊断日志。
Variable Index: None。
"""

from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError

from .errors import api_exception_handler


class HealthTests(TestCase):
    """Functionality: 检查公开健康接口与管理后台认证边界。
    Logic: 使用 Django 测试客户端和隔离数据库；游标只在数据库故障测试中替换。
    Constraints: 不证明 PostgreSQL 或真实 ASGI 网络服务已部署。
    """

    def test_database_health(self):
        """Functionality: 验证匿名请求实际探测测试数据库。
        Inputs: self.client 与 Django 隔离 SQLite，无外部参数或 mock。
        Outputs: 断言失败抛异常；成功无返回值。
        Logic: GET 健康接口并核对完整 200 响应。
        Constraints: 只证明测试数据库连通，开发数据库另由启动检查验证。
        """
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "sqlite"})

    def test_database_failure(self):
        """Functionality: 验证数据库故障的日志与 503 契约。
        Inputs: 测试客户端；替换 core.views.connection.cursor 使其抛 DatabaseError。
        Outputs: 断言无凭据响应且错误日志存在；不写数据库。
        Logic: 在错误日志捕获范围内请求，再核对状态和错误码。
        Constraints: 模拟故障不等于真实 PostgreSQL 断连实验。
        """
        with (
            patch("core.views.connection.cursor", side_effect=DatabaseError("probe failed")),
            self.assertLogs("core.views", level="ERROR") as logs,
        ):
            response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error", "code": "database_unavailable"})
        self.assertIn("health database probe failed", logs.output[0])

    def test_post_is_rejected(self):
        """Functionality: 确认健康接口不可用于写操作。
        Inputs: 匿名测试客户端和空 POST；无 mock。
        Outputs: 断言 HTTP 405；成功无返回值。
        Logic: 直接验证 DRF 方法限制。
        Constraints: 不替代未来业务接口的 CSRF 和资源权限测试。
        """
        self.assertEqual(self.client.post("/api/health/", {}).status_code, 405)

    def test_admin_requires_login(self):
        """Functionality: 验证 Django 管理入口要求登录。
        Inputs: 未认证的 self.client；无 mock 或管理员账号。
        Outputs: 断言跳转到带 next 参数的登录页。
        Logic: 不跟随重定向，避免将登录页响应误判为授权访问。
        Constraints: 不验证业务账号注册或前端登录流程。
        """
        self.assertRedirects(
            self.client.get("/admin/"), "/admin/login/?next=/admin/", fetch_redirect_response=False
        )


class ErrorHandlerTests(SimpleTestCase):
    """Functionality: 检查 API 错误处理器保留状态码并限制公开内容。
    Logic: 直接提供 DRF 和普通异常，使用真实处理器及日志捕获。
    Constraints: 不需要数据库；不代表所有未来业务异常已覆盖。
    """

    def test_validation_error_keeps_standard_response(self):
        """Functionality: 确认参数校验错误保持 DRF 标准契约。
        Inputs: ValidationError 和空上下文；无 mock。
        Outputs: 断言 400 与 detail；无副作用。
        Logic: 核对默认处理器返回值，避免统一错误逻辑覆盖客户端错误。
        Constraints: 未调用真实业务序列化器。
        """
        response = api_exception_handler(ValidationError({"name": "required"}), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

    def test_unexpected_error_is_logged_and_redacted(self):
        """Functionality: 验证未知异常返回固定 500，并记录异常位置。
        Inputs: 合成 RuntimeError、空上下文及错误日志捕获；无 mock。
        Outputs: 断言状态、固定响应和错误日志；不会公开原异常文本。
        Logic: 直接检查处理器输出与日志事件。
        Constraints: 合成异常不包含敏感信息，也不验证真实外部依赖。
        """
        with self.assertLogs("core.errors", level="ERROR") as logs:
            response = api_exception_handler(RuntimeError("synthetic failure"), {})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.data, {"code": "internal_error", "detail": "Internal server error."}
        )
        self.assertIn("unhandled API exception view=unknown", logs.output[0])
