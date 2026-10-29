"""Expose only the existing mobile routes to an explicitly configured local HTTPS tunnel."""

from .mobile import MOUNT, MobileApplication, reject


class TunnelApplication:
    """Strip the public mount without exposing MobileApplication's localhost branch.

    The connector must target a loopback-only listener with proxy-header trust disabled.
    Public Host and Origin checks remain in force even when the connector is on localhost.
    """

    def __init__(self, application, *, public_origin=None):
        self.mobile = MobileApplication(application, public_origin=public_origin)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.mobile(scope, receive, send)
        hosts = [value for name, value in scope.get("headers", []) if name.lower() == b"host"]
        if hosts != [self.mobile.public_host]:
            return await reject(scope, receive, send, reason="tunnel_host_rejected")
        path = scope.get("path", "")
        if not path.startswith(MOUNT + "/"):
            return await reject(scope, receive, send, status=404, reason="not_found")
        mapped = {
            **scope,
            "path": path[len(MOUNT):],
            "raw_path": path[len(MOUNT):].encode(),
        }
        return await self.mobile(mapped, receive, send)
