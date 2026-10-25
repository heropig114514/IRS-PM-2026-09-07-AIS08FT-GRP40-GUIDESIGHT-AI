"""Responsibilities: Preserve DRF error responses, log unhandled exceptions, and expose a fixed error code.
Implementation: Delegate known DRF exceptions to its default handler; keep unknown exceptions as HTTP 500 without retrying.
Related Modules: config.base.REST_FRAMEWORK; called by DRF APIView.
Declaration Index: api_exception_handler — maps unknown API exceptions and logs their location.
Variable Index: logger — API exception logger.
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """Functionality: Return a safe, diagnosable HTTP 500 response for an unhandled API exception.
    Inputs: exc is the exception; context is DRF context and may include the current view.
    Outputs: A standard DRF Response or a fixed internal_error HTTP 500 Response.
    Logic: Prefer DRF's default mapping; when unmapped, log the view type and original traceback.
    Constraints: Do not log request bodies, query parameters, cookies, or credentials; never convert failure to success.
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
