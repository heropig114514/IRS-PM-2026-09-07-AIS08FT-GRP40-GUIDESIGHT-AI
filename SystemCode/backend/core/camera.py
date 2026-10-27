"""Responsibilities: Serve the camera page and allowlisted camera/map frontend assets.
Implementation: Read small static source files into responses; accept no uploads or user-built
paths.
Related Modules: config.urls, core.maps, SystemCode/frontend/index.html;
i18n/translations, camera/detection/frame-pipeline/maps/navigation/ai-control/vibration/speech/scene
modules; agent-client/agent-device/voice-input/voice-worklet; integration navigation speech wiring;
core.scene for the scene endpoint.
Declaration Index: camera_page — main page; camera_asset — allowed assets;
serve_file — shared response headers.
Variable Index: FRONTEND_DIR — frontend root; ASSETS — fixed filename/MIME allowlist.
"""

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

FRONTEND_DIR = settings.BASE_DIR.parent / "frontend"
ASSETS = {
    "agent-client.js": "text/javascript",
    "agent-device.js": "text/javascript",
    "voice-input.js": "text/javascript",
    "voice-worklet.js": "text/javascript",
    "agent.css": "text/css",
    "i18n.js": "text/javascript",
    "translations.js": "text/javascript",
    "camera.css": "text/css",
    "camera.js": "text/javascript",
    "echo-client.js": "text/javascript",
    "live-detection-client.js": "text/javascript",
    "frame-pipeline.js": "text/javascript",
    "maps-client.js": "text/javascript",
    "maps-page.js": "text/javascript",
    "navigation.js": "text/javascript",
    "navigation-ui.js": "text/javascript",
    "ai-control.js": "text/javascript",
    "vibration.js": "text/javascript",
    "speech-output.js": "text/javascript",
    "scene-client.js": "text/javascript",
    "integration/maps-navigation-speech.js": "text/javascript",
    "integration/maps-navigation-bridge.js": "text/javascript",
    "integration/maps-navigation-speech.css": "text/css",
    "integration/navigation-speech.js": "text/javascript",
    "integration/events.js": "text/javascript",
    "integration/catalog.js": "text/javascript",
}


def serve_file(name, content_type):
    """Functionality: Serve an explicitly allowed frontend file with origin access boundaries.
    Inputs: Internal allowlisted name and content_type; FRONTEND_DIR and configured origins.
    Outputs: HttpResponse; read_bytes closes the file; missing files propagate filesystem errors.
    Logic: Return small fixed source files directly, without adapting synchronous file iterators to
    ASGI streaming; disable page caching.
    Constraints: Only camera and validated asset views call this helper; arbitrary paths are
    forbidden.
    """
    response = HttpResponse((FRONTEND_DIR / name).read_bytes(), content_type=content_type)
    response["Cache-Control"] = "no-store"
    response["Permissions-Policy"] = "camera=(self), microphone=(self)"
    websocket_origins = " ".join(
        origin.replace("https://", "wss://").replace("http://", "ws://")
        for origin in settings.CAMERA_ALLOWED_ORIGINS
    )
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob:; media-src 'self' blob:; "
        f"connect-src 'self' {websocket_origins}; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@require_safe
def camera_page(request):
    """Functionality: Display the phone camera echo/YOLO annotated return page.
    Inputs: Read-only Django request; no user account or database dependency.
    Outputs: HTML HttpResponse; non-GET/HEAD methods return 405.
    Logic: Serve a fixed filename without client template interpolation.
    Constraints: A user click or explicit agent request initiates camera access; the browser
    grants permission.
    """
    return serve_file("index.html", "text/html")


@require_safe
def camera_asset(request, name):
    """Functionality: Serve JavaScript/CSS from a fixed allowlist.
    Inputs: Django request and routed name; nested names must be explicitly present in ASSETS.
    Outputs: HttpResponse with a fixed MIME type; unknown filenames raise Http404.
    Logic: Validate the mapping before reading a file.
    Constraints: Never expose README files, configuration, credentials or arbitrary directories.
    """
    if name not in ASSETS:
        raise Http404("Unknown frontend asset")
    return serve_file(name, ASSETS[name])
