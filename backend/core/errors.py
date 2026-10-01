"""Responsibilities: 保留 DRF 标准错误响应，记录未处理异常并公开固定错误码。
Implementation: 已知 DRF 异常由其默认处理器处理；未知异常保持 HTTP 500，不重试。
Related Modules: config.base.REST_FRAMEWORK；由 DRF APIView 调用。
Declaration Index: api_exception_handler — 转换未知 API 异常并记录位置。
Variable Index: logger — API 异常控制台日志。
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """Functionality: 为 API 未知异常提供安全且可诊断的 500 响应。
    Inputs: exc 为异常；context 为 DRF 上下文，可能包含当前视图。
    Outputs: 标准 DRF Response 或固定 internal_error 的 HTTP 500 Response。
    Logic: 优先使用 DRF 默认映射；未映射时记录视图类型和原异常栈。
    Constraints: 不记录请求正文、查询参数、cookie 或凭据；不会改变为成功状态。
    """
    response = exception_handler(exc, context)
    if response is not None:
        return response
    view = context.get("view")
    logger.error(
        "unhandled API exception view=%s",
        type(view).__name__ if view is not None else "unknown",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return Response({"code": "internal_error", "detail": "Internal server error."}, status=500)
