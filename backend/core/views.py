"""Responsibilities: 只读数据库健康检查。
Implementation: 实际执行 SELECT 1；数据库异常返回 503 并记录异常栈，不重试。
Related Modules: core.urls；数据库由 config.settings/production 指定。
Declaration Index: health — 公开 GET 检查 HTTP 服务与数据库可用性。
Variable Index: logger — 公共应用控制台日志。
"""

import logging

from django.db import DatabaseError, connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Functionality: 检查数据库连接并返回机器可读的服务状态。
    Inputs: DRF request；隐式读取默认数据库连接配置，不读取用户输入。
    Outputs: 成功为 200/status=ok/database=引擎；数据库异常为 503/status=error。
    Logic: 在上下文管理器中执行 SELECT 1；只捕获 Django DatabaseError。
    Constraints: 明确允许匿名 GET；失败日志标识接口及数据库引擎，不返回连接凭据。
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception(
            "health database probe failed path=/api/health/ vendor=%s", connection.vendor
        )
        return Response({"status": "error", "code": "database_unavailable"}, status=503)
    return Response({"status": "ok", "database": connection.vendor})
