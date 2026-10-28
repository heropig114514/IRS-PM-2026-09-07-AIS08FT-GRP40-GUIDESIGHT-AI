"""Responsibilities: Guard authority injection and public-scope boundaries in the phone bridge.
Implementation: Render valid and hostile hostname inputs without starting networking services.
Related Modules: tools.render_phone_access; deploy.phone-access.conf.template.
Declaration Index: PhoneAccessTests; PhoneAccessTests.test_rejects_authority_and_nginx_injection;
PhoneAccessTests.test_preserves_guest_and_loopback_boundaries.
Variable Index: None.
"""

from django.test import SimpleTestCase

from tools.render_phone_access import render


class PhoneAccessTests(SimpleTestCase):
    """Functionality: Verify generated bridge admission constraints.
    Logic: Reject malformed hostnames before substitution and inspect the fixed upstream scope.
    Constraints: Nginx syntax and actual TLS/WS admission still require operator deployment checks.
    """

    def test_rejects_authority_and_nginx_injection(self):
        """Functionality: Reject alternate authorities and configuration injection.
        Inputs: URLs, ports, foreign domains, control characters and oversized labels.
        Outputs: ValueError for every invalid case. Logic: Full-match ASCII DNS validation.
        Constraints: No DNS resolution or transport behavior is inferred.
        """
        for value in (None, "", "*.trycloudflare.com", "https://demo.trycloudflare.com",
                      "demo.trycloudflare.com:443", "demo.trycloudflare.com.evil.test",
                      "demo.trycloudflare.com;", "demo.trycloudflare.com\n", "$host",
                      "a" * 64 + ".trycloudflare.com", "demo..trycloudflare.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                render(value)

    def test_preserves_guest_and_loopback_boundaries(self):
        """Functionality: Keep guest routing bounded behind one exact hostname.
        Inputs: Generated hostname. Outputs: Fixed loopback, origin, path and credential guards.
        Logic: Retain the original mobile application's independent allowlist and CSP content.
        Constraints: This static test does not claim real service or Nginx validation.
        """
        config = render("sample-phone-demo.trycloudflare.com")
        self.assertNotIn("@HOST@", config)
        self.assertNotIn("@ORIGIN_PATTERN@", config)
        self.assertIn("~^https://sample-phone-demo[.]trycloudflare[.]com$", config)
        self.assertIn("listen 127.0.0.1:18770;", config)
        self.assertNotIn("listen 0.0.0.0", config)
        self.assertIn('if ($http_host != "sample-phone-demo.trycloudflare.com")', config)
        self.assertIn('if ($prs_access_origin = "REJECT")', config)
        self.assertIn('location = /mvp/mcp { return 404; }', config)
        self.assertIn('location ^~ /mvp/admin/ { return 404; }', config)
        self.assertIn('proxy_set_header Authorization "";', config)
        self.assertIn('proxy_set_header Cookie "";', config)
        self.assertIn('proxy_next_upstream off;', config)
        self.assertIn('default $upstream_http_content_security_policy;', config)
        self.assertIn('proxy_pass http://127.0.0.1:18766/;', config)
        self.assertNotIn('18767', config)
