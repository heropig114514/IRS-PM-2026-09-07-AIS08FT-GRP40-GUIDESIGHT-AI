"""Responsibilities: Serve the OSM page, nonsensitive configuration and same-origin walking route
API.
Implementation: Separate map assets from route services; check Origin and Host; keep credentials
in services.
Related Modules: core.services, core.camera, config.urls; frontend maps and voice agent modules.
Declaration Index: maps_page — page; maps_config — configuration state; maps_route — route POST.
Variable Index: logger — safe diagnostics; MAX_REQUEST_BYTES — coordinate request size limit.
"""

import json
import logging
import os
import secrets
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .camera import FRONTEND_DIR
from .services import RouteError, validate_coordinate, walking_route

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 2048


@require_safe
def maps_page(request):
    """Functionality: Display the Leaflet/OSM walking route test page.
    Inputs: request and fixed HTML; Outputs: uncached HTML with nonce CSP and location/microphone
    policies.
    Logic: Use local modules and pinned Leaflet/SRI; fetch OSM tiles directly with attribution and
    normal caching.
    Constraints: Only this map page permits unpkg/OSM; camera page policy remains independent.
    """
    nonce = secrets.token_urlsafe(24)
    html = (FRONTEND_DIR / "maps.html").read_text(encoding="utf-8")
    response = HttpResponse(html.replace("__CSP_NONCE__", nonce), content_type="text/html")
    response["Cache-Control"] = "no-store"
    response["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(self)"
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://unpkg.com; "
        f"style-src 'self' 'nonce-{nonce}' https://unpkg.com; "
        "img-src 'self' https://tile.openstreetmap.org data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@require_safe
def maps_config(request):
    """Functionality: Report whether ORS is configured without exposing its key.
    Inputs: request and ORS_API_KEY setting; Outputs: uncached JSON, 200 or 503 for missing
    configuration.
    Logic: Return only provider/profile/configured; configured does not mean live authentication
    succeeded.
    Constraints: Route calls verify actual service authentication.
    """
    configured = bool(os.environ.get("ORS_API_KEY", "").strip())
    response = JsonResponse(
        {
            "provider": "openrouteservice",
            "profile": "foot-walking",
            "configured": configured,
            "code": "ready" if configured else "ors_not_configured",
        },
        status=200 if configured else 503,
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def maps_route(request):
    """Functionality: Send explicitly submitted endpoints to ORS and return a normalized route.
    Inputs: request Origin/Host and JSON start/end coordinates [longitude,latitude].
    Outputs: Route or safe error JSON.
    Logic: This anonymous API does not use cookies; strict Origin/Host validation replaces cookie
    CSRF.
    Bound request size and validate input before invoking the service.
    Constraints: Same-origin admission is not identity authentication; forward without storing
    coordinates,
    logging coordinates/keys or automatically retrying.
    """
    request_id = uuid.uuid4().hex[:12]
    try:
        origin = request.headers.get("Origin", "")
        if (
            origin not in settings.CAMERA_ALLOWED_ORIGINS
            or urlsplit(origin).netloc != request.get_host()
        ):
            raise RouteError(
                "origin_rejected",
                "The route endpoint accepts same-origin requests from this page only.",
                403,
            )
        if request.content_type != "application/json":
            raise RouteError("invalid_content_type", "The request must use application/json.", 415)
        if len(request.body) > MAX_REQUEST_BYTES:
            raise RouteError("request_too_large", "The route request exceeds the 2 KiB limit.", 413)
        try:
            body = json.loads(request.body)
            if not isinstance(body, dict) or set(body) != {"start", "end"}:
                raise ValueError("Only start and end coordinate fields are accepted.")
            start, end = validate_coordinate(body["start"]), validate_coordinate(body["end"])
            if start == end:
                raise ValueError("The start and end points must differ.")
        except (ValueError, UnicodeDecodeError, OverflowError) as error:
            raise RouteError(
                "invalid_route_input",
                "Start and end must be distinct valid [longitude, latitude] coordinates.",
                400,
            ) from error
        route = walking_route(start, end, request_id)
        response = JsonResponse({**route, "request_id": request_id})
    except RouteError as error:
        logger.warning(
            "Route request failed id=%s code=%s status=%s", request_id, error.code, error.status
        )
        response = JsonResponse(
            {"code": error.code, "detail": error.detail, "request_id": request_id},
            status=error.status,
        )
    response["Cache-Control"] = "no-store"
    return response
