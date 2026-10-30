"""Responsibilities: Expose only existing mobile routes to an explicit local HTTPS tunnel.
Implementation: Exact public Host and mount gates precede canonical mobile Origin/path checks.
Related Modules: integration.mobile/tunnel_asgi; tools.run_public_mvp.
Declaration Index: TunnelApplication; TunnelApplication.__init__; TunnelApplication.__call__.
Variable Index: None.
"""

from .mobile import MOUNT, MobileApplication, reject


class TunnelApplication:
    """Functionality: Strip the public mount without exposing the localhost branch.
    Logic: retain public Host/Origin checks even when the connector is on localhost.
    Constraints: require a loopback-only listener with proxy-header trust disabled.
    """

    def __init__(self, application, *, public_origin=None):
        """Functionality: Freeze the mobile route boundary. Inputs: app and explicit origin.
        Outputs: wrapper. Logic: reuse mobile validation. Constraints: no wildcard origin.
        """
        self.mobile = MobileApplication(application, public_origin=public_origin)

    async def __call__(self, scope, receive, send):
        """Functionality: Admit only the configured tunnel scope. Inputs: ASGI request/channels.
        Outputs: rejection or canonical mobile response. Logic: check Host before path rewriting.
        Constraints: no forwarded-header trust or private localhost API exposure.
        """
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
