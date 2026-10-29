"""Responsibilities: Serve only public certificate enrollment and local setup instructions.
Implementation: Exact LAN listener/peer/Host and three fixed GET/HEAD paths; no secret files.
Related Modules: tools.lan_certificate/run_lan_mvp; frontend/lan-setup.html; integration.lan.
Declaration Index: LanSetupApplication; LanSetupApplication.__init__; LanSetupApplication.__call__.
Variable Index: None. Profile bytes contain only the public root certificate and fixed metadata.
"""

import html
import json
import plistlib
from ipaddress import ip_address
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from .lan import lan_address
from .services import FRONTEND


class LanSetupApplication:
    """Functionality: Provide a bounded HTTP enrollment page before iPhone trusts local HTTPS.
    Logic: cache public certificate/profile bytes and enforce exact selected LAN admission.
    Constraints: no directory serving, private key reads, GPU frames, API relay or device changes.
    """

    def __init__(self, *, address, subnet, port, origin, tls_directory):
        """Functionality: Prepare the three public enrollment responses.
        Inputs: explicit network/ports/origin and certificate directory. Outputs: fixed byte map.
        Logic: embed only the CA certificate in an unsigned iOS enrollment profile.
        Constraints: the user must install and trust it on the device; no system trust mutation.
        """
        self.address, self.network = lan_address(address, subnet)
        self.port, self.authority = port, f"{address}:{port}".encode()
        directory = Path(tls_directory)
        ca = (directory / "demo-ca.crt").read_bytes()
        certificate = x509.load_pem_x509_certificate(ca)
        metadata = json.loads((directory / "certificate.json").read_text(encoding="utf-8"))
        profile = {
            "PayloadType": "Configuration",
            "PayloadVersion": 1,
            "PayloadIdentifier": "local.prs.lan-demo." + str(uuid4()),
            "PayloadUUID": str(uuid4()),
            "PayloadDisplayName": "PRS LAN Demo",
            "PayloadDescription": "Seven-day local HTTPS demo certificate; remove after testing.",
            "PayloadContent": [
                {
                    "PayloadType": "com.apple.security.root",
                    "PayloadVersion": 1,
                    "PayloadIdentifier": "local.prs.lan-demo.ca",
                    "PayloadUUID": str(uuid4()),
                    "PayloadDisplayName": "PRS LAN Demo - 7 days",
                    "PayloadCertificateFileName": "demo-ca.crt",
                    "PayloadContent": certificate.public_bytes(Encoding.DER),
                }
            ],
        }
        page = (FRONTEND / "lan-setup.html").read_text(encoding="utf-8")
        for key, value in {
            "origin": origin,
            "fingerprint": metadata["ca_sha256"],
            "expires": metadata["expires_at"],
        }.items():
            page = page.replace("{{" + key + "}}", html.escape(value, quote=True))
        self.responses = {
            "/": (page.encode(), b"text/html; charset=utf-8"),
            "/demo-ca.crt": (ca, b"application/x-x509-ca-cert"),
            "/demo.mobileconfig": (plistlib.dumps(profile), b"application/x-apple-aspen-config"),
        }

    async def __call__(self, scope, receive, send):
        """Functionality: Serve a public trust file only to a directly connected LAN peer.
        Inputs: ASGI scope/channels. Outputs: bounded HTTP response or websocket rejection.
        Logic: lifespan acknowledgements; exact Host/listener/subnet/path and GET/HEAD gates.
        Constraints: no redirects, query parameters, forwarded headers, filesystem paths or logs
        containing certificate-private material; rejection bodies are empty.
        """
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] == "websocket":
            await receive()
            return await send({"type": "websocket.close", "code": 1008})
        headers = [(name.lower(), value) for name, value in scope.get("headers", [])]
        try:
            valid = (
                ip_address(scope["client"][0]) in self.network
                and scope["server"] == (str(self.address), self.port)
                and [value for name, value in headers if name == b"host"] == [self.authority]
                and not any(
                    name.startswith(b"x-forwarded-") or name == b"forwarded" for name, _ in headers
                )
            )
        except (ValueError, KeyError, TypeError, IndexError):
            valid = False
        path = scope.get("path", "")
        status = (
            403
            if not valid
            else 404
            if path not in self.responses or scope.get("query_string")
            else 405
            if scope.get("method") not in {"GET", "HEAD"}
            else 200
        )
        body, mime = self.responses[path] if status == 200 else (b"", b"text/plain")
        output = [
            (b"content-type", mime),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
        ]
        if status == 200 and path != "/":
            output.append((b"content-disposition", f'attachment; filename="{path[1:]}"'.encode()))
        await send({"type": "http.response.start", "status": status, "headers": output})
        await send(
            {"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body}
        )
