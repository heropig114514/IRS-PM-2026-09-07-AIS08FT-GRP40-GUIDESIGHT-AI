"""Responsibilities: Admit a dedicated HTTPS LAN demo without exposing loopback-only APIs.
Implementation: Exact RFC1918 listener/subnet, HTTPS and mount gates precede the existing mobile
Host/Origin/path boundary; only validated peers are normalized to the canonical loopback scope.
Related Modules: integration.mobile/assist_asgi/lan_asgi; tools.run_lan_mvp.
Declaration Index: lan_address; LanApplication; LanApplication.__init__; LanApplication.__call__.
Variable Index: PRIVATE_NETWORKS is the explicit RFC1918 allowlist; logger records fixed failures.
"""

import logging
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from .mobile import MOUNT, MobileApplication, reject

PRIVATE_NETWORKS = tuple(
    ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
logger = logging.getLogger(__name__)


def lan_address(address, subnet):
    """Functionality: Validate one explicit LAN binding and its peer subnet.
    Inputs: IPv4 address and canonical CIDR strings. Outputs: parsed address/network.
    Logic: require an RFC1918 host inside a /24 or narrower private network.
    Constraints: no wildcard/loopback/public binding, inferred interface, DNS or automatic choice.
    """
    host, network = ip_address(address), ip_network(subnet, strict=True)
    if (
        host.version != 4
        or network.version != 4
        or network.prefixlen < 24
        or not any(network.subnet_of(private) for private in PRIVATE_NETWORKS)
        or host not in network
        or host in {network.network_address, network.broadcast_address}
    ):
        raise ValueError("Select an RFC1918 host and its /24 or narrower LAN subnet")
    return host, network


class LanApplication:
    """Functionality: Isolate the LAN phone entry from local software and public relay access.
    Logic: validate direct TLS peer/listener/mount, then reuse the unchanged mobile boundary.
    Constraints: anonymous within the selected subnet; no forwarded-header trust, MCP or admin.
    """

    def __init__(self, application, *, origin, subnet):
        """Functionality: Freeze the direct LAN boundary.
        Inputs: existing assist ASGI callable, HTTPS origin and selected CIDR.
        Outputs: wrapper. Logic: reuse canonical mobile-origin validation and verify LAN address.
        Constraints: no listener, credential lookup, DNS or GPU access during construction.
        """
        self.mobile = MobileApplication(application, public_origin=origin)
        parsed = urlsplit(origin)
        self.host, self.network = lan_address(parsed.hostname, subnet)
        self.port = parsed.port or 443

    async def __call__(self, scope, receive, send):
        """Functionality: Admit only direct TLS traffic on the configured LAN interface.
        Inputs: original ASGI scope/channels. Outputs: mobile result or explicit rejection.
        Logic: peer CIDR and listener identity precede exact mount removal and canonicalization.
        Constraints: untrusted forwarding headers cannot authorize access; lifespan alone bypasses
        HTTP admission; no certificate files, raw media, unrelated paths or exception details.
        """
        if scope["type"] == "lifespan":
            return await self.mobile(scope, receive, send)
        try:
            peer = ip_address(scope["client"][0])
            server = scope["server"]
            valid = (
                peer in self.network
                and server[0] == str(self.host)
                and server[1] == self.port
                and scope.get("scheme") in {"https", "wss"}
            )
        except (ValueError, TypeError, KeyError, IndexError):
            valid = False
        headers = [(name.lower(), value) for name, value in scope.get("headers", [])]
        valid = valid and [value for name, value in headers if name == b"host"] == [
            self.mobile.public_host
        ]
        if not valid or any(
            name
            in {
                b"forwarded",
                b"x-forwarded-for",
                b"x-forwarded-host",
                b"x-forwarded-proto",
                b"x-prs-relay",
            }
            for name, _ in headers
        ):
            return await reject(scope, receive, send, reason="lan_peer_rejected")
        path = scope.get("path", "")
        if not path.startswith(MOUNT + "/"):
            return await reject(scope, receive, send, status=404, reason="not_found")
        local = {
            **scope,
            "path": path[len(MOUNT) :],
            "raw_path": path[len(MOUNT) :].encode(),
            "client": ("127.0.0.1", scope["client"][1]),
        }
        return await self.mobile(local, receive, send)
