"""Responsibilities: Render one explicit temporary public phone bridge for operator review.
Implementation: Validate a single generated hostname, then substitute the fixed loopback template.
Related Modules: deploy.phone-access.conf.template; integration.mobile; PHONE-ACCESS.md.
Declaration Index: render; main.
Variable Index: HOSTNAME bounds the DNS label; TEMPLATE selects the repository configuration.
"""

import argparse
import re
from pathlib import Path

HOSTNAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*[.]trycloudflare[.]com", re.ASCII)
TEMPLATE = Path(__file__).resolve().parents[1] / "deploy/phone-access.conf.template"


def render(hostname):
    """Functionality: Produce an exact Host/Origin allowlist for one approved demo hostname.
    Inputs: Lowercase generated hostname without URL syntax. Outputs: Nginx configuration text.
    Logic: Reject arbitrary domains, ports, control characters and configuration interpolation.
    Constraints: No tunnel creation, system writes, Nginx reload or GPU/MCP exposure.
    """
    if not isinstance(hostname, str) or not HOSTNAME.fullmatch(hostname):
        raise ValueError("Expected one generated trycloudflare.com hostname")
    if len(hostname.removesuffix(".trycloudflare.com")) > 63:
        raise ValueError("Hostname label exceeds DNS limits")
    template = TEMPLATE.read_text(encoding="utf-8")
    return template.replace("@HOST@", hostname).replace(
        "@ORIGIN_PATTERN@", hostname.replace(".", "[.]"),
    )


def main(argv=None):
    """Functionality: Print a candidate configuration for review.
    Inputs: One explicit hostname. Outputs: Standard-output configuration or argument error.
    Logic: Validate before rendering. Constraints: No network, installation or service mutation.
    """
    parser = argparse.ArgumentParser(description="Render the approved temporary phone bridge")
    parser.add_argument("hostname")
    args = parser.parse_args(argv)
    try:
        print(render(args.hostname), end="")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
