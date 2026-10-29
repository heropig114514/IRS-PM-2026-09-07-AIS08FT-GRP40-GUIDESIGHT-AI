"""Responsibilities: Issue an isolated seven-day LAN demo certificate and public trust anchor.
Implementation: In-memory CA signing key; RSA/SHA256 IP-SAN server leaf; exclusive private outputs.
Related Modules: tools.run_lan_mvp; cryptography from the existing phone development environment.
Declaration Index: create_certificate.
Variable Index: None. The CA private key is never serialized; only the server key is persisted.
"""

import json
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def create_certificate(directory, address):
    """Functionality: Create a dedicated short-lived trust anchor and exact-IP TLS certificate.
    Inputs: new output directory and validated LAN IP. Outputs: nonsecret certificate metadata.
    Logic: create an in-memory CA, sign one server-auth leaf, save leaf key privately and discard
    the CA key. Constraints: no existing file replacement, system trust installation, public
    certificate/key copying, network or additional certificate issuance.
    """
    target, host = Path(directory), ip_address(address)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    now = datetime.now(UTC)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PRS LAN Demo - 7 days")])
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(host))]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(host)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (target / "demo-ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (target / "server.crt").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    with (target / "server.key").open("xb") as output:
        output.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    (target / "server.key").chmod(0o600)
    report = {
        "address": str(host),
        "expires_at": leaf.not_valid_after_utc.isoformat(),
        "ca_sha256": ca.fingerprint(hashes.SHA256()).hex(),
        "server_sha256": leaf.fingerprint(hashes.SHA256()).hex(),
        "ca_private_key_saved": False,
        "system_trust_modified": False,
    }
    (target / "certificate.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
