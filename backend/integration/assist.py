"""Responsibilities: Serve product and diagnostic views over one assist controller contract.
Implementation: Fixed views/assets, incompatible-page guards and the original local gateway.
Related Modules: integration.assist_asgi/services/protocol; config.asgi; core.maps/camera;
frontend/maps.html and frontend/integration/assist-*.
Declaration Index: render_assist_page; assist_response; AssistApplication;
AssistApplication.__init__; AssistApplication.__call__; AssistApplication.page;
AssistApplication.asset; AssistApplication.status; AssistApplication.status.collect.
Variable Index: CAMERA_PROFILES — explicit capture policies shared with frontend camera.js;
PRS_CAMERA_PROFILE — explicit page policy selection, defaulting to the original profile;
ASSIST_ASSETS — canonical integration module/MIME allowlist;
ENTRY_REQUIRED — explicit guidance for standalone 55-second GPU pages in assist mode;
logger — template failure diagnostics without private file paths or contents.
"""

import json
import logging
import os
import secrets
from urllib.parse import urlsplit

from .protocol import local_session_seconds, web_origins
from .services import FRONTEND, IntegrationApplication

logger = logging.getLogger(__name__)
CAMERA_PROFILES = frozenset({
    "standard", "bounded-2", "bounded-3", "responsive-2", "responsive-3",
    "conservative-2", "conservative-3",
})
ASSIST_ASSETS = {
    "assist-mvp.js": "text/javascript",
    "assist-guidance.js": "text/javascript",
    "speech-activation.js": "text/javascript",
    "voice-preferences.js": "text/javascript",
    "product-ui.js": "text/javascript",
    "assist.css": "text/css",
    "assist-product.css": "text/css",
    "video-replay.js": "text/javascript",
    "video-alerts.js": "text/javascript",
    "devices.js": "text/javascript",
    "events.js": "text/javascript",
    "catalog.js": "text/javascript",
    "maps-navigation-bridge.js": "text/javascript",
    "navigation-speech.js": "text/javascript",
}
ENTRY_REQUIRED = {
    "code": "assist_entry_required",
    "detail": "统一模式请使用/maps/；独立55秒测试请用默认离线启动器",
}


def render_assist_page(frontend=FRONTEND, *, view="product", camera_profile=None):
    """Functionality: Render an owned product view or the existing full diagnostic composition.
    Inputs: fixed frontend root, exact view and explicit profile or PRS_CAMERA_PROFILE environment.
    Outputs: HTML and fresh style nonce.
    Logic: both views use the same controller IDs and sibling document URL base; only diagnostics
    compose the original map template. Constraints: no core source edits, user HTML or fallback.
    """
    if view not in {"product", "test"}:
        raise ValueError("Unknown assist view")
    profile = (os.environ.get("PRS_CAMERA_PROFILE", "standard")
               if camera_profile is None else camera_profile)
    if profile not in CAMERA_PROFILES:
        raise ValueError("Unknown camera capture profile")
    if view == "product":
        html = (frontend / "integration/assist-product.html").read_text(encoding="utf-8")
    else:
        html = (frontend / "maps.html").read_text(encoding="utf-8")
        panel = (frontend / "integration/assist-panel.html").read_text(encoding="utf-8")
        old_script = ('<script type="module" '
                      'src="../assets/integration/maps-navigation-speech.js"></script>')
        old_style = ('<link rel="stylesheet" '
                     'href="../assets/integration/maps-navigation-speech.css" />')
        for marker in (old_script, old_style, "</head>", "</main>", "</header>"):
            if html.count(marker) != 1:
                raise ValueError("Assist template contract changed")
        html = html.replace(old_script, "").replace(old_style, "")
        html = html.replace("</main>", panel + "\n</main>", 1)
        html = html.replace("</head>",
                            '<link rel="stylesheet" href="../assets/integration/assist.css">'
                            '<script type="module" '
                            'src="../assets/integration/assist-mvp.js"></script></head>', 1)
        html = html.replace("</header>", '<a href="./">返回导航</a></header>', 1)
        html = html.replace("<title>PRS · 步行路线测试</title>", "<title>PRS · 集成测试</title>")
        html = html.replace("当前导航未接入避障和风险播报。", "本页可同时运行本地感知与导航播报。")
        html = html.replace("导航语音与视频警报保持独立。", "导航与感知由本页统一调度输出。")
        html = html.replace("启用导航播报（默认关闭）", "启用导航播报（默认开启）")
        html = html.replace("还需启用下方语音输出。", "语音输出默认开启，首次请点击启动语音。")
    if html.count("<html ") != 1:
        raise ValueError("Assist template contract changed")
    nonce = secrets.token_urlsafe(24)
    html = html.replace("<html ", f'<html data-camera-profile="{profile}" '
                        'data-local-mvp="true" data-assist="true" '
                        f'data-assist-view="{view}" data-api-base="../" ', 1)
    return html.replace("__CSP_NONCE__", nonce).encode(), nonce


async def assist_response(send, status, body, content_type="application/json", *, head=False,
                          nonce=None, redirect=False, allow_microphone=False):
    """Functionality: Return the combined entry's explicit maps/media permission boundary.
    Inputs: send/status/fixed body/MIME, HEAD/nonce, redirect and microphone-policy flag.
    Outputs: no-store HTTP messages. Logic: preserve Leaflet/OSM policy; permit same-origin mic
    only on views that expose an explicit assistant recording control.
    Constraints: policy permits browser requests but does not request permission; redirect targets
    only /maps/ and no arbitrary connect target or private data is exposed.
    """
    raw = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
    sockets = " ".join(item.replace("https://", "wss://").replace("http://", "ws://")
                       for item in web_origins())
    style_nonce = f" 'nonce-{nonce}'" if nonce is not None else ""
    csp = ("default-src 'self'; script-src 'self' https://unpkg.com; "
           f"style-src 'self'{style_nonce} https://unpkg.com; "
           "img-src 'self' blob: https://tile.openstreetmap.org data:; "
           f"media-src 'self' blob:; connect-src 'self' {sockets}; "
           "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    headers = [
        (b"content-type", content_type.encode()), (b"content-length", str(len(raw)).encode()),
        (b"cache-control", b"no-store"),
        (b"permissions-policy", b"geolocation=(self), camera=(self), " +
         (b"microphone=(self)" if allow_microphone else b"microphone=()")),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"content-security-policy", csp.encode()),
    ]
    if redirect:
        headers.append((b"location", b"/maps/"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else raw})


class AssistApplication:
    """Functionality: Add explicit combined maps/perception UI around the offline application.
    Logic: only maps config/route bypass the offline block; all local inference uses its gateway.
    Constraints: original software/algorithm and offline entry stay intact; shared module URLs.
    """

    def __init__(self, software, local=None):
        """Functionality: Bind original software and the isolated local integration wrapper.
        Inputs: ASGI software and optional explicit local test seam. Outputs: assist callable.
        Logic: no services or GPU are started. Constraints: assist requires matching process policy.
        """
        if local_session_seconds() is not None:
            raise ValueError("Assist entry requires LOCAL_MVP_MODE=assist")
        self.software = software
        self.local = local if local is not None else IntegrationApplication(software)

    async def __call__(self, scope, receive, send):
        """Functionality: Route the explicit assist extension and preserve original capabilities.
        Inputs: ASGI scope/receive/send. Outputs: page, allowlisted asset or delegated reply.
        Logic: root opens maps; standalone GPU pages fail explicitly under continuous policy;
        fixed maps API exceptions and canonical modules preserve HF/scene blocking.
        Constraints: no traversal, new network provider, duplicate device singleton or URL secret.
        """
        path = scope.get("path", "")
        if scope["type"] == "http":
            if path == "/":
                if scope.get("method") not in {"GET", "HEAD"}:
                    return await assist_response(send, 405, {"code": "method_not_allowed"})
                return await assist_response(send, 302, b"", redirect=True)
            if path in {"/live/", "/integration/video/"}:
                return await assist_response(send, 409, ENTRY_REQUIRED,
                                             head=scope.get("method") == "HEAD")
            if path in {"/maps/", "/maps/test"}:
                return await self.page(scope, send)
            if path == "/maps/test/":
                return await assist_response(send, 404, {"code": "not_found"},
                                             head=scope.get("method") == "HEAD")
            if path in {"/maps/config/", "/maps/route/"}:
                return await self.software(scope, receive, send)
            if path.startswith("/assets/integration/"):
                return await self.asset(scope, send)
            if path.startswith("/assets/"):
                name = path.removeprefix("/assets/")
                if "\\" in name or any(part in {".", "..", ""} for part in name.split("/")):
                    return await assist_response(send, 404, {"code": "not_found"})
                return await self.software(scope, receive, send)
            if path == "/integration/status/":
                return await self.status(scope, send)
        await self.local(scope, receive, send)

    async def page(self, scope, send):
        """Functionality: Serve a fixed product or diagnostic maps/perception page.
        Inputs: safe same-host GET/HEAD. Outputs: view HTML or explicit contract/method failure.
        Logic: exact sibling paths preserve existing relative APIs; render with one fresh nonce and
        allow same-origin microphone use for both views' explicit assistant recording controls.
        Constraints: the permission policy does not request access; no source rewriting or concealed
        template faults.
        """
        if scope.get("method") not in {"GET", "HEAD"}:
            return await assist_response(send, 405, {"code": "method_not_allowed"})
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("ascii", "replace")
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        allowed_hosts = {urlsplit(item).netloc for item in web_origins()}
        if (scope.get("query_string") or host not in allowed_hosts
                or origin and (origin not in web_origins() or urlsplit(origin).netloc != host)):
            return await assist_response(send, 403, {"code": "origin_rejected"})
        try:
            body, nonce = render_assist_page(
                view="test" if scope["path"] == "/maps/test" else "product")
        except (OSError, ValueError) as error:
            logger.error("Assist template unavailable type=%s", type(error).__name__)
            return await assist_response(send, 503, {"code": "assist_template_unavailable"})
        await assist_response(send, 200, body, "text/html; charset=utf-8",
                              head=scope.get("method") == "HEAD", nonce=nonce,
                              allow_microphone=scope["path"] in {"/maps/", "/maps/test"})

    async def asset(self, scope, send):
        """Functionality: Serve only canonical modules belonging to the combined entry.
        Inputs: fixed asset path and GET/HEAD. Outputs: bytes/404/405.
        Logic: exact allowlist precedes reads. Constraints: no aliases, traversal or raw HTML.
        """
        if scope.get("method") not in {"GET", "HEAD"}:
            return await assist_response(send, 405, {"code": "method_not_allowed"})
        name = scope["path"].removeprefix("/assets/integration/")
        if name not in ASSIST_ASSETS or scope.get("query_string"):
            return await assist_response(send, 404, {"code": "not_found"})
        try:
            body = (FRONTEND / "integration" / name).read_bytes()
        except OSError:
            return await assist_response(send, 404, {"code": "assist_asset_missing"})
        await assist_response(send, 200, body, ASSIST_ASSETS[name],
                              head=scope.get("method") == "HEAD")

    async def status(self, scope, send):
        """Functionality: Report the original worker health with an honest assist feature policy.
        Inputs: existing status scope/send. Outputs: original status plus explicit route allowance.
        Logic: reuse original validation/health request and replace only entry labels.
        Constraints: no ORS key, external probe or inference; worker fields stay unchanged.
        """
        messages = []

        async def collect(message):
            """Functionality: Capture the known bounded local status response for relabeling.
            Inputs: original ASGI status message. Outputs: appended message.
            Logic: status emits start/body once. Constraints: not a general streaming buffer.
            """
            messages.append(message)

        await self.local.status(scope, collect)
        body = json.loads(messages[1]["body"])
        body.update(mode="assist", external_services="explicit_maps_only")
        await assist_response(send, messages[0]["status"], body)
