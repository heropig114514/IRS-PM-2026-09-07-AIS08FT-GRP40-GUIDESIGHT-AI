"""Responsibilities: Admit one explicit HTTPS relay around the unchanged local assist application.
Implementation: Loopback peer, exact Host/Origin/path gates; canonical internal headers preserve
existing Web/GPU checks. Public responses retain /mvp/WSS; two fixed map assets adapt only
same-origin credential transport so the proxy's existing browser authentication is retained.
Related Modules: integration.mobile_asgi/assist_asgi/assist/protocol; core.ai_bridge/ai_mcp;
deployment reverse SSH and Nginx terminate public TLS without exposing the worker or MCP.
Declaration Index: mobile_origin; reject; MobileApplication; MobileApplication.__init__;
MobileApplication.__call__; MobileApplication.public_path; MobileApplication.authenticated_asset;
MobileApplication.forward;
MobileApplication.forward.public_send.
Variable Index: MOUNT — fixed public /mvp prefix; HTTP_METHODS — exact public endpoint methods;
WS_PATHS — two original browser sockets; STRIPPED_HEADERS — proxy/relay headers never trusted
downstream; logger — body-free admission diagnostics. Public origin is explicit configuration,
not an authentication credential; the external proxy owns the deployment access gate.
MAP_AUTH_ASSETS/MAX_ASSET_BYTES/OMIT_CREDENTIALS — exact map-only byte adaptation contract.
"""

import json
import logging
import os
from urllib.parse import urlsplit

from .protocol import is_loopback, web_origins
from .services import FRONTEND

MOUNT = "/mvp"
HTTP_METHODS = {
    "/": frozenset({"GET", "HEAD"}),
    "/maps/": frozenset({"GET", "HEAD"}),
    "/maps/test": frozenset({"GET", "HEAD"}),
    "/maps/config/": frozenset({"GET", "HEAD"}),
    "/maps/route/": frozenset({"POST"}),
    "/integration/status/": frozenset({"GET"}),
}
WS_PATHS = frozenset({"/ws/control/", "/ws/detect/local/"})
MAP_AUTH_ASSETS = {
    "/assets/maps-page.js": "maps-page.js",
    "/assets/maps-client.js": "maps-client.js",
}
MAX_ASSET_BYTES = 65536
OMIT_CREDENTIALS = b'credentials: "omit"'
STRIPPED_HEADERS = frozenset(
    {
        b"host",
        b"origin",
        b"x-prs-relay",
        b"forwarded",
        b"x-forwarded-host",
        b"x-forwarded-proto",
        b"x-forwarded-for",
    }
)
logger = logging.getLogger(__name__)


def mobile_origin(value=None):
    """Functionality: Validate one explicitly configured public HTTPS origin.
    Inputs: optional supplied value or MOBILE_PUBLIC_ORIGIN environment. Outputs: same canonical
    origin.
    Logic: require ASCII HTTPS authority with no credentials/path/query/fragment or loopback alias.
    Constraints: no wildcard, default public destination, HTTP downgrade or implicit hostname
    lookup.
    """
    raw = os.environ.get("MOBILE_PUBLIC_ORIGIN", "") if value is None else value
    try:
        if (
            not isinstance(raw, str)
            or not raw
            or not raw.isascii()
            or any(char.isspace() for char in raw)
        ):
            raise ValueError
        parsed = urlsplit(raw)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or is_loopback(parsed.hostname)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(char in parsed.netloc for char in "*\\")
            or parsed.netloc != parsed.netloc.lower()
            or raw != "https://" + parsed.netloc
            or parsed.port in {0, 443}
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        _ = parsed.port
        return raw
    except (ValueError, TypeError):
        raise ValueError(
            "MOBILE_PUBLIC_ORIGIN requires an explicit canonical public HTTPS origin"
        ) from None


async def reject(scope, receive, send, *, status=403, reason="origin_rejected"):
    """Functionality: Refuse a relay operation before any downstream handler executes.
    Inputs: ASGI channels, fixed status and safe reason. Outputs: HTTP JSON or WS1008.
    Logic: diagnose only a fixed reason/protocol; HEAD omits its body.
    Constraints: no raw Host/Origin, location, credential, media or exception-body logging.
    """
    logger.warning("Mobile relay rejected protocol=%s reason=%s", scope["type"], reason)
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"code": reason}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send(
        {"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body}
    )


class MobileApplication:
    """Functionality: Isolate one public mobile relay from original localhost capabilities.
    Logic: validate original peer/headers before mapping admitted public traffic to canonical local
    inputs. Same process/hub remains available to the computer's original local MCP client.
    Constraints: one worker, proxy-stripped path contract; no public MCP/admin/API, authentication
    substitution, trusted forwarded headers, algorithm changes or extra controllers.
    """

    def __init__(self, application, *, public_origin=None):
        """Functionality: Freeze explicit public and canonical internal boundaries.
        Inputs: original assist callable and optional origin test/config seam. Outputs: wrapper.
        Logic: read existing loopback origin allowlist without widening or mutating it.
        Constraints: construction never starts listeners, SSH, TLS, inference or external requests.
        """
        self.application = application
        self.public_origin = mobile_origin(public_origin)
        self.public_host = urlsplit(self.public_origin).netloc.encode("ascii")
        self.public_socket = self.public_origin.replace("https://", "wss://", 1).encode("ascii")
        self.local_origins = web_origins()
        self.local_hosts = {
            urlsplit(origin).netloc.encode("ascii") for origin in self.local_origins
        }
        self.canonical_origin = self.local_origins[0].encode("ascii")
        self.canonical_host = urlsplit(self.local_origins[0]).netloc.encode("ascii")

    async def __call__(self, scope, receive, send):
        """Functionality: Validate original relay/local admission and delegate one operation.
        Inputs: ASGI scope/channels with already stripped public path. Outputs: same handler or
        rejection.
        Logic: raw loopback/local Host preserves original MCP; any relay marker forces the public
        gate.
        Constraints: Origin is mandatory for public POST/WS; lifecycle bypasses HTTP peer checks;
        marker/Host/Origin duplicates and nonloopback peers fail before source/GPU activity.
        """
        kind = scope["type"]
        if kind == "lifespan":
            return await self.application(scope, receive, send)
        if kind not in {"http", "websocket"}:
            raise ValueError("Unsupported mobile ASGI protocol")
        if not is_loopback((scope.get("client") or (None, 0))[0]):
            return await reject(scope, receive, send, reason="relay_peer_rejected")
        headers = [(name.lower(), value) for name, value in scope.get("headers", [])]
        hosts = [value for name, value in headers if name == b"host"]
        origins = [value for name, value in headers if name == b"origin"]
        markers = [value for name, value in headers if name == b"x-prs-relay"]
        if len(hosts) != 1 or len(origins) > 1 or len(markers) > 1 or markers and markers != [b"1"]:
            return await reject(scope, receive, send)
        host, origin = hosts[0], origins[0] if origins else None
        if not markers and host in self.local_hosts:
            if origin is not None and (
                origin not in {item.encode("ascii") for item in self.local_origins}
                or urlsplit(origin.decode("ascii")).netloc.encode("ascii") != host
            ):
                return await reject(scope, receive, send)
            return await self.application(scope, receive, send)
        if (
            host != self.public_host
            or origin is not None
            and origin != self.public_origin.encode("ascii")
        ):
            return await reject(scope, receive, send)
        if (kind == "websocket" or scope.get("method") == "POST") and origin is None:
            return await reject(scope, receive, send)
        if scope.get("query_string"):
            return await reject(scope, receive, send, status=404, reason="not_found")
        return await self.forward(scope, receive, send, headers)

    def public_path(self, path, kind):
        """Functionality: Resolve exact allowed public endpoint methods.
        Inputs: already stripped decoded path and protocol. Outputs: permitted method set or None.
        Logic: two original WS paths and fixed HTTP routes; asset serving retains assist's exact
        allowlist.
        Constraints: prefixed/double-prefixed paths, traversal, empty segments and backslashes
        rejected;
        this layer never invents an asset filename or delegates unrelated software routes.
        """
        if not isinstance(path, str) or not path.startswith("/") or "\\" in path:
            return None
        if kind == "websocket":
            return frozenset() if path in WS_PATHS else None
        if path in HTTP_METHODS:
            return HTTP_METHODS[path]
        if path.startswith("/assets/"):
            name = path.removeprefix("/assets/")
            if name and all(part not in {".", "..", ""} for part in name.split("/")):
                return frozenset({"GET", "HEAD"})
        return None

    async def authenticated_asset(self, scope, receive, send):
        """Functionality: Deliver a fixed public map asset with same-origin proxy authentication.
        Inputs: admitted GET/HEAD scope for one MAP_AUTH_ASSETS key and ASGI channels.
        Outputs: derived JavaScript bytes, or explicit503 when the source contract changes.
        Logic: bounded original read; replace exactly one literal credential option only.
        Constraints: no source writes, localhost changes, arbitrary asset map, route/navigation
        edits, global fetch interception, secret injection, fallback or implicit retry.
        """
        name = MAP_AUTH_ASSETS[scope["path"]]
        try:
            with (FRONTEND / name).open("rb") as source:
                original = source.read(MAX_ASSET_BYTES + 1)
            if len(original) > MAX_ASSET_BYTES or original.count(OMIT_CREDENTIALS) != 1:
                raise ValueError
        except (OSError, ValueError):
            return await reject(scope, receive, send, status=503,
                                reason="mobile_asset_contract_invalid")
        body = original.replace(OMIT_CREDENTIALS, b'credentials: "same-origin"', 1)
        logger.info("Mobile map credential transport served asset=%s", name)
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"text/javascript; charset=utf-8"),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
            (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body",
                    "body": b"" if scope["method"] == "HEAD" else body})

    async def forward(self, scope, receive, send, headers):
        """Functionality: Delegate an admitted public route with immutable local contracts.
        Inputs: validated original scope/headers/channels. Outputs: original response with mount/CSP
        edges.
        Logic: preserve path, set fixed root_path and map only trusted Host/Origin; original request
        scope remains untouched. Public transport scheme reflects the explicitly configured TLS
        origin.
        Constraints: no widened worker/Django origin policy, proxy-header trust, path
        auto-detection,
        general body rewriting, retry, media processing or unknown public capabilities.
        Only the two fixed public map assets adapt credential transport before delegation.
        """
        allowed = self.public_path(scope.get("path"), scope["type"])
        if allowed is None:
            return await reject(scope, receive, send, status=404, reason="not_found")
        if scope["type"] == "http" and scope.get("method") not in allowed:
            return await reject(scope, receive, send, status=405, reason="method_not_allowed")
        if scope["type"] == "http" and scope["path"] in MAP_AUTH_ASSETS:
            return await self.authenticated_asset(scope, receive, send)
        mapped_headers = [(name, value) for name, value in headers if name not in STRIPPED_HEADERS]
        mapped_headers.append((b"host", self.canonical_host))
        if any(name == b"origin" for name, _ in headers):
            mapped_headers.append((b"origin", self.canonical_origin))
        forwarded = {
            **scope,
            "headers": mapped_headers,
            "root_path": MOUNT,
            "scheme": "wss" if scope["type"] == "websocket" else "https",
        }

        async def public_send(message):
            """Functionality: Preserve public mount and permitted native WSS in original HTTP
            headers.
            Inputs: original ASGI response event. Outputs: copied header changes only.
            Logic: fixed /maps redirect and connect-src replacement preserve nonce/CDN/blob
            directives.
            Constraints: no location/body/media rewrite, CSP weakening or effect on localhost
            responses.
            """
            if message["type"] == "http.response.start":
                output = []
                for name, value in message.get("headers", []):
                    if name.lower() == b"location" and value == b"/maps/":
                        value = (MOUNT + "/maps/").encode()
                    elif name.lower() == b"content-security-policy":
                        directives = [part.strip() for part in value.split(b";") if part.strip()]
                        directives = [
                            part
                            for part in directives
                            if part.split(None, 1)[0].lower() != b"connect-src"
                        ]
                        directives.append(b"connect-src 'self' " + self.public_socket)
                        value = b"; ".join(directives)
                    output.append((name, value))
                message = {**message, "headers": output}
            await send(message)

        logger.info(
            "Mobile relay admitted protocol=%s category=%s",
            scope["type"],
            "asset" if scope["path"].startswith("/assets/") else scope["path"],
        )
        return await self.application(forwarded, receive, public_send)
