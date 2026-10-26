"""Responsibilities: Implement a read-only database health check.
Implementation: Execute SELECT 1; return 503 and log the exception stack on database failure, with
no retry.
Related Modules: core.urls; the database is selected by config.settings/production.
Declaration Index: health — public GET check for HTTP service and database availability.
Variable Index: logger — shared application console logger.
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
    """Functionality: Check the database connection and return machine-readable service status.
    Inputs: DRF request; implicitly reads the default database connection settings and no user
    input.
    Outputs: 200/status=ok/database=engine on success; 503/status=error on database failure.
    Logic: Execute SELECT 1 inside a context manager; catch only Django DatabaseError.
    Constraints: Anonymous GET is explicitly allowed; failure logs identify the endpoint and
    database engine without returning credentials.
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
