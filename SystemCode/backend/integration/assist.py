"""Responsibilities: Serve product and diagnostic views over one assist controller contract.
Implementation: Fixed views/assets, prepared audio streaming, incompatible-page guards and the
original local gateway.
Related Modules: integration.assist_asgi/services/protocol; config.asgi; core.maps/camera;
SystemCode/frontend/maps.html and SystemCode/frontend/integration/assist-*.
Declaration Index: render_assist_page; assist_response; risk_audio_manifest; AssistApplication;
AssistApplication.__init__; AssistApplication.__call__; AssistApplication.page;
AssistApplication.asset; AssistApplication.risk_audio; AssistApplication.status;
AssistApplication.status.collect.
Variable Index: ASSIST_ASSETS — canonical integration module/MIME allowlist;
ENTRY_REQUIRED — explicit guidance for standalone 55-second GPU pages in assist mode;
RISK_AUDIO_PATH/RISK_AUDIO_MAX_BYTES — fixed prepared clip names and bounded resource reads;
logger — template failure diagnostics without private file paths or contents.
"""

import asyncio
import json
import logging
import os
import re
import secrets
from urllib.parse import urlsplit

from .protocol import local_session_seconds, web_origins
from .services import FRONTEND, IntegrationApplication

logger = logging.getLogger(__name__)
CAMERA_PROFILES = frozenset({
    "standard", "bounded-2", "bounded-3", "responsive-2", "responsive-3",
    "conservative-2", "conservative-3", "low-latency-1", "low-latency-2",
})
ASSIST_ASSETS = {
    "live-latency.js": "text/javascript",
    "live-camera-overlay.js": "text/javascript",
    "local-motion.js": "text/javascript",
    "motion-worker.js": "text/javascript",
    "motion-core.js": "text/javascript",
    "jsfeat.min.js": "text/javascript",
    "assist-mvp.js": "text/javascript",
    "assist-guidance.js": "text/javascript",
    "risk-speech-cache.js": "text/javascript",
    "risk-audio-resources.js": "text/javascript",
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
RISK_AUDIO_PATH = re.compile(
    r"(?P<language>zh-CN|en-US)/(?P=language)-(?:prefix|clause|sentence)-[a-f0-9]{64}\.wav\Z"
)
RISK_AUDIO_MAX_BYTES = 512 * 1024 + 44
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
    default_profile = ("low-latency-2" if os.environ.get("PRS_LATENCY_PANEL") == "1"
                       else "low-latency-1")
    profile = (os.environ.get("PRS_CAMERA_PROFILE", default_profile)
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
    if view == "product" and os.environ.get("PRS_LATENCY_PANEL") == "1":
        age = os.environ.get("PRS_RISK_TTL_MS", "1000")
        if age not in {"1000", "2000"}:
            raise ValueError("Unsupported demo warning age")
        prepared = "true" if os.environ.get("PRS_PREPARED_SPEECH") == "1" else "false"
        html = html.replace("<html ", f'<html data-camera-display="live-overlay" '
                            f'data-risk-max-age-ms="{age}" data-prepared-speech="{prepared}" ', 1)
        panel = (frontend / "integration/live-latency.html").read_text(encoding="utf-8")
        if html.count("</main>") != 1:
            raise ValueError("Assist template contract changed")
        html = html.replace("</main>", panel + "\n</main>", 1)
    nonce = secrets.token_urlsafe(24)
    html = re.sub(r' data-camera-profile="[^"]*"', "", html)
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
    csp = ("default-src 'self'; script-src 'self' https://unpkg.com; worker-src 'self'; "
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


def risk_audio_manifest():
    """Functionality: Read the owned manifest and its exact source-clip publication allowlist.
    Inputs: Fixed repository asset root. Outputs: Root, original JSON bytes and clip byte lengths.
    Logic: Bound manifest reads and require the version-2 pack's 96 canonical language/hash paths.
    Constraints: No directory listing, preview/document exposure, arbitrary paths or credentials;
    browser validation remains responsible for complete profile, composition and audio hashes.
    """
    root = (FRONTEND / "assets/risk-audio").resolve()
    manifest_path = (root / "manifest.json").resolve()
    if not root.is_relative_to(FRONTEND.resolve()) or not manifest_path.is_relative_to(root):
        raise ValueError("Invalid resource root")
    with manifest_path.open("rb") as stream:
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError("Oversized resource manifest")
    manifest = json.loads(raw)
    clips = manifest.get("clips") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 2
            or not isinstance(clips, list) or len(clips) != 96):
        raise ValueError("Invalid resource clip count")
    paths = {}
    for clip in clips:
        name = clip.get("path") if isinstance(clip, dict) else None
        length = clip.get("byte_length") if isinstance(clip, dict) else None
        if (not isinstance(name, str) or not RISK_AUDIO_PATH.fullmatch(name)
                or name in paths or type(length) is not int
                or not 44 < length <= RISK_AUDIO_MAX_BYTES):
            raise ValueError("Invalid resource clip")
        paths[name] = length
    return root, raw, paths


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
            if path.startswith("/assets/risk-audio/"):
                return await self.risk_audio(scope, send)
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

    async def risk_audio(self, scope, send):
        """Functionality: Publish only the manifest and its exact prepared source WAVs.
        Inputs: Canonical resource GET/HEAD. Outputs: JSON or bounded streamed WAV bytes.
        Logic: Validate names before disk access, resolve containment, and check exact file length;
        stream at most 64 KiB per ASGI body while preserving the existing mobile mount wrapper.
        Constraints: No synthesis, preview serving, directory listing, range alias, retry or change
        to detection/navigation. Missing or inconsistent resources fail explicitly.
        """
        head = scope.get("method") == "HEAD"
        if scope.get("method") not in {"GET", "HEAD"}:
            return await assist_response(send, 405, {"code": "method_not_allowed"})
        name = scope["path"].removeprefix("/assets/risk-audio/")
        if scope.get("query_string") or (
                name != "manifest.json" and not RISK_AUDIO_PATH.fullmatch(name)):
            return await assist_response(send, 404, {"code": "not_found"}, head=head)
        try:
            root, manifest, paths = await asyncio.to_thread(risk_audio_manifest)
        except (OSError, ValueError, UnicodeError) as error:
            logger.error("Prepared risk audio manifest unavailable type=%s", type(error).__name__)
            return await assist_response(send, 503, {"code": "risk_audio_unavailable"}, head=head)
        if name == "manifest.json":
            return await assist_response(send, 200, manifest, "application/json", head=head)
        if name not in paths:
            return await assist_response(send, 404, {"code": "not_found"}, head=head)
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            return await assist_response(send, 404, {"code": "not_found"}, head=head)
        stream = None
        try:
            stream = target.open("rb")
            if os.fstat(stream.fileno()).st_size != paths[name]:
                raise ValueError("Resource byte length changed")
        except (OSError, ValueError) as error:
            if stream is not None:
                stream.close()
            logger.error("Prepared risk audio clip unavailable type=%s", type(error).__name__)
            return await assist_response(send, 503, {"code": "risk_audio_unavailable"}, head=head)
        with stream:
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", b"audio/wav"),
                (b"content-length", str(paths[name]).encode()),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ]})
            if head:
                return await send({"type": "http.response.body", "body": b""})
            remaining = paths[name]
            while remaining:
                chunk = await asyncio.to_thread(stream.read, min(65536, remaining))
                if not chunk:
                    raise OSError("Prepared risk audio clip truncated during read")
                remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk,
                            "more_body": remaining > 0})

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
