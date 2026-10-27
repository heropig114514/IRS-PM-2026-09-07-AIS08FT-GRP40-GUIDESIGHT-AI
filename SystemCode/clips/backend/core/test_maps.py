"""Responsibilities: Test the map page, same-origin input boundary, and ORS transport, capacity, and
response contracts.
Implementation: Read actual page files; mock upstream HTTP with a fake key; make no ORS or
device-location calls.
Related Modules: core.maps and core.services; live upstream integration is verified separately.
Declaration Index: route_fixture — synthetic upstream payload; MapsTests — HTTP endpoints;
MapsTests.test_page — policy, bilingual UI, and navigation asset wiring;
MapsTests.test_navigation_speech_assets — nested asset allowlist and rejection boundaries;
MapsTests.test_config — credential boundary; MapsTests.test_route_input — request validation;
MapsTests.test_route_result — route adaptation;
ServiceTests — service behavior; ServiceTests.setUp — rate-limit reset;
ServiceTests.test_coordinates — coordinates;
ServiceTests.test_normalize — upstream format; ServiceTests.test_success — transport contract;
ServiceTests.test_errors — error mapping; ServiceTests.test_timeout_and_size — resource limits;
ServiceTests.test_rate_and_capacity — rate and concurrency limits; ServiceTests.test_missing_key —
missing configuration.
Variable Index: None; fixture coordinates are synthetic test points and do not represent the current
device.
"""

import copy
import json
import re
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from . import services


def route_fixture():
    """Functionality: Build a minimal valid ORS GeoJSON response.
    Inputs: None. Outputs: An independent dictionary. Logic: Two points and real departure/arrival
    field structure.
    Constraints: Synthetic data does not prove that a road exists, GPS works, or a public request
    succeeds.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "geometry": {"type": "LineString", "coordinates": [[103.7, 1.3], [103.8, 1.4]]},
                "properties": {
                    "summary": {"distance": 100, "duration": 60},
                    "segments": [
                        {
                            "steps": [
                                {
                                    "instruction": "Depart",
                                    "type": 11,
                                    "distance": 100,
                                    "duration": 60,
                                    "way_points": [0, 1],
                                },
                                {
                                    "instruction": "Arrive",
                                    "type": 10,
                                    "distance": 0,
                                    "duration": 0,
                                    "way_points": [1, 1],
                                },
                            ]
                        }
                    ],
                },
            }
        ],
        "metadata": {"query": {"coordinates": "must not be forwarded"}},
    }


class MapsTests(SimpleTestCase):
    """Functionality: Verify HTTP page and API contracts. Logic: Use real routes and files while
    replacing upstream calls.
    Constraints: No database, external network, or browser permissions.
    """

    def test_page(self):
        """Functionality: Verify CSP, nonce, bilingual UI, and navigation assets against the real
        page.
        Inputs: Actual page. Outputs: Policy and wiring assertions.
        Logic: Check HTML/CSP nonce consistency, language selector and map/navigation modules, and
        removal of Google domains and credentials.
        Constraints: Does not execute browser CSP or prove dynamic translation; browser tools cover
        the real DOM separately.
        """
        first = self.client.get("/maps/")
        second = self.client.get("/maps/")
        nonce = re.search(r'nonce="([^"]+)"', first.content.decode())[1]
        self.assertIn(f"'nonce-{nonce}'", first["Content-Security-Policy"])
        self.assertNotIn(nonce, second.content.decode())
        self.assertNotContains(first, "__CSP_NONCE__")
        self.assertNotIn("google", first["Content-Security-Policy"])
        self.assertNotIn("unpkg", self.client.get("/live/")["Content-Security-Policy"])
        self.assertIn("geolocation=(self)", first["Permissions-Policy"])
        self.assertContains(first, 'id="language"')
        self.assertContains(first, 'value="system"')
        self.assertContains(first, 'value="en"')
        self.assertContains(first, 'value="zh-CN"')
        self.assertContains(first, 'src="../assets/i18n.js"')
        for name in ["maps-page.js", "maps-client.js", "navigation.js", "navigation-ui.js",
                     "i18n.js", "translations.js"]:
            self.assertEqual(self.client.get(f"/assets/{name}").status_code, 200)
        self.assertContains(first, 'src="../assets/integration/maps-navigation-speech.js"')
        self.assertContains(first, 'href="../assets/integration/maps-navigation-speech.css"')
        self.assertContains(first, 'id="speech-enabled"')
        self.assertContains(first, 'id="navigation-speech-enabled"')
        self.assertRegex(first.content.decode(), r'id="speech-rate"[^>]*value="2"')
        self.assertNotContains(first, "video-mvp.js")

    def test_navigation_speech_assets(self):
        """Functionality: Serve only the explicitly named nested navigation integration assets.
        Inputs: Actual fixed files, HEAD and unknown/traversal requests.
        Outputs: MIME, body and 404 checks.
        Logic: Use the shared nested route while retaining the live single-segment route.
        Constraints: No browser, GPS, route service or real speech engine is exercised.
        """
        names = (
            "maps-navigation-speech.js", "maps-navigation-bridge.js", "maps-navigation-speech.css",
            "navigation-speech.js", "events.js", "catalog.js",
        )
        for name in names:
            with self.subTest(name=name):
                response = self.client.get("/assets/integration/" + name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"].split(";")[0],
                                 "text/css" if name.endswith(".css") else "text/javascript")
                self.assertTrue(response.content)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertEqual(self.client.head("/assets/integration/" + name).status_code, 200)
                live = self.client.get("/live/assets/integration/" + name)
                self.assertEqual(live.status_code, 404)
        for name in ("integration/unknown.js", "integration/../../backend/.env",
                     "integration/../navigation.js", "integration/navigation-sample.json",
                     "integration/video-mvp.js", "../.env", "README.md"):
            with self.subTest(rejected=name):
                self.assertEqual(self.client.get("/assets/" + name).status_code, 404)

    def test_config(self):
        """Functionality: Keep the key server-side. Inputs: A fake key and an empty-key environment.
        Outputs: Configuration assertions.
        Logic: Check status and fields without exposing the key. Constraints: Does not verify real
        authentication.
        """
        for key, status in [("fake-ors-key", 200), ("", 503)]:
            with patch.dict("os.environ", {"ORS_API_KEY": key}):
                response = self.client.get("/maps/config/")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["configured"], bool(key))
                if key:
                    self.assertNotContains(response, key)
                self.assertNotIn("apiKey", response.json())
                self.assertEqual(response["Cache-Control"], "no-store")

    @patch("core.maps.walking_route")
    def test_route_input(self, upstream):
        """Functionality: Reject cross-origin, malformed or oversized bodies, and invalid
        coordinates.
        Inputs: Requests and a mocked upstream. Outputs: Error statuses; upstream remains uncalled.
        Logic: Reject invalid input before any external request.
        Constraints: Non-browser clients can forge Origin; same-origin checks are not
        authentication.
        """
        base = {"HTTP_HOST": "127.0.0.1:8766", "HTTP_ORIGIN": "http://127.0.0.1:8766"}
        valid = {"start": [103.7, 1.3], "end": [103.8, 1.4]}
        self.assertEqual(
            self.client.post("/maps/route/", valid, content_type="application/json").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/maps/route/",
                valid,
                content_type="application/json",
                **{**base, "HTTP_ORIGIN": "https://evil.example"},
            ).status_code,
            403,
        )
        for data in [
            {"start": [True, 1], "end": [1, 2]},
            {"start": [181, 1], "end": [1, 2]},
            {"start": [1, 2], "end": [1, 2]},
            {"start": [1, 2]},
            [],
            {"start": [10**500, 2], "end": [1, 2]},
        ]:
            self.assertEqual(
                self.client.post(
                    "/maps/route/", data, content_type="application/json", **base
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                "/maps/route/", "invalid", content_type="application/json", **base
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/maps/route/", "x" * 2049, content_type="application/json", **base
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post("/maps/route/", "{}", content_type="text/plain", **base).status_code,
            415,
        )
        self.assertEqual(self.client.get("/maps/route/").status_code, 405)
        upstream.assert_not_called()

    @patch("core.maps.walking_route")
    def test_route_result(self, upstream):
        """Functionality: Adapt service results and errors. Inputs: Canonical fixtures and mocked
        RouteError.
        Outputs: Numeric order, status, and diagnostic identifier assertions.
        Logic: Ensure the HTTP boundary does not expose credentials.
        Constraints: A mocked route does not prove connectivity to real ORS.
        """
        options = {
            "content_type": "application/json",
            "HTTP_HOST": "127.0.0.1:8766",
            "HTTP_ORIGIN": "http://127.0.0.1:8766",
        }
        upstream.return_value = services.normalize_route(route_fixture())
        response = self.client.post(
            "/maps/route/", {"start": [103.7, 1.3], "end": [103.8, 1.4]}, **options
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(upstream.call_args.args[:2], ([103.7, 1.3], [103.8, 1.4]))
        self.assertEqual(len(response.json()["request_id"]), 12)
        upstream.side_effect = services.RouteError("ors_timeout", "safe diagnostic", 504)
        self.assertEqual(
            self.client.post(
                "/maps/route/", {"start": [103.7, 1.3], "end": [103.8, 1.4]}, **options
            ).status_code,
            504,
        )


@patch.dict("os.environ", {"ORS_API_KEY": "fake-ors-key"})
class ServiceTests(SimpleTestCase):
    """Functionality: Verify service validation, transport, and rate limits while replacing
    HTTPSConnection.
    Logic: Keep the service implementation in the exercised path.
    Constraints: The fake key is never sent externally; production defaults remain unchanged.
    """

    def setUp(self):
        """Functionality: Clear rate-limit test state. Inputs: self and the module queue. Outputs:
        An empty queue.
        Logic: Prevent cross-test contamination. Constraints: Do not change production capacity,
        quota, or time settings.
        """
        services.request_times.clear()

    def test_coordinates(self):
        """Functionality: Verify strict numeric boundaries. Inputs: Valid boundary values and
        invalid values.
        Outputs: Coordinate and exception assertions.
        Logic: Reject bool, NaN, infinity, and oversized integers. Constraints: No location device
        is simulated.
        """
        self.assertEqual(services.validate_coordinate([-180, 90]), [-180.0, 90.0])
        for value in [
            [True, 1],
            [float("nan"), 1],
            [float("inf"), 1],
            [10**500, 1],
            ["1", 2],
            [1],
            None,
        ]:
            with self.assertRaises(ValueError):
                services.validate_coordinate(value)

    def test_normalize(self):
        """Functionality: Verify response contracts and malformed data. Inputs: Synthetic fixtures.
        Outputs: Canonical fields and 502.
        Logic: Check metadata cleanup and geometry/step boundaries. Constraints: No real upstream is
        used.
        """
        route = services.normalize_route(route_fixture())
        self.assertNotIn("metadata", route)
        self.assertEqual(route["steps"][0]["type"], 11)
        damaged = copy.deepcopy(route_fixture())
        damaged["features"][0]["properties"]["segments"][0]["steps"][0]["way_points"] = [0, 99]
        for data in [{}, {"type": "FeatureCollection", "features": []}, damaged]:
            with self.assertRaises(services.RouteError) as caught:
                services.normalize_route(data)
            self.assertEqual(caught.exception.code, "ors_invalid_response")

    @patch("core.services.http.client.HTTPSConnection")
    def test_success(self, connection):
        """Functionality: Verify fixed host, path, Authorization, and single-request behavior using
        an HTTP mock.
        Outputs: Timeout, result, and close assertions. Logic: Parse actual fixture bytes.
        Constraints: Does not prove TLS, public-network access, or real authentication.
        """
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(route_fixture()).encode()
        route = services.walking_route([103.7, 1.3], [103.8, 1.4], "test")
        self.assertEqual(route["summary"]["distance_m"], 100)
        connection.assert_called_once_with("api.heigit.org", timeout=20)
        request = connection.return_value.request
        request.assert_called_once()
        self.assertEqual(
            request.call_args.args,
            ("POST", "/openrouteservice/v2/directions/foot-walking/geojson"),
        )
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "fake-ors-key")
        self.assertEqual(request.call_args.kwargs["headers"]["Accept"], "application/geo+json")
        self.assertEqual(
            json.loads(request.call_args.kwargs["body"])["coordinates"],
            [[103.7, 1.3], [103.8, 1.4]],
        )
        connection.return_value.close.assert_called_once()

    @patch("core.services.http.client.HTTPSConnection")
    def test_errors(self, connection):
        """Functionality: Fail explicitly on authentication, quota, unavailable, and redirect
        responses.
        Inputs: Mocked HTTP statuses. Outputs: Error codes and one call per case.
        Logic: Do not follow redirects or retry. Constraints: Real ORS is not called.
        """
        for status, code in [
            (403, "ors_authentication_failed"),
            (429, "ors_quota_exceeded"),
            (400, "ors_route_unavailable"),
            (500, "ors_upstream_error"),
            (302, "ors_upstream_error"),
        ]:
            connection.reset_mock()
            connection.return_value.getresponse.return_value.status = status
            with self.assertRaises(services.RouteError) as caught:
                services.walking_route([1, 2], [2, 3], "test")
            self.assertEqual(caught.exception.code, code)
            connection.return_value.request.assert_called_once()
            connection.return_value.close.assert_called_once()

    @patch("core.services.http.client.HTTPSConnection")
    def test_timeout_and_size(self, connection):
        """Functionality: Verify network timeouts and response-size limits using an HTTP mock.
        Outputs: Explicit errors and released capacity slots. Logic: Do not swallow errors or report
        success.
        Constraints: Mock timeouts do not measure real network timing.
        """
        connection.return_value.request.side_effect = TimeoutError()
        with self.assertRaises(services.RouteError) as caught:
            services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.status, 504)
        connection.return_value.request.side_effect = None
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b"x" * (services.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(services.RouteError) as caught:
            services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.code, "ors_response_too_large")
        self.assertTrue(services.route_slots.acquire(blocking=False))
        self.assertTrue(services.route_slots.acquire(blocking=False))
        services.route_slots.release()
        services.route_slots.release()

    @patch("core.services.http.client.HTTPSConnection")
    def test_rate_and_capacity(self, connection):
        """Functionality: Reject requests for rate or capacity limits before network access.
        Inputs: Module slots and queue state. Outputs: Two 429 responses and no upstream calls.
        Logic: Restore capacity in finally. Constraints: Verifies one worker only, not cross-worker
        limiting.
        """
        services.request_times.extend([time.monotonic()] * services.MAX_REQUESTS_PER_MINUTE)
        with self.assertRaises(services.RouteError) as caught:
            services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.code, "route_rate_limit")
        services.request_times.clear()
        services.route_slots.acquire()
        services.route_slots.acquire()
        try:
            with self.assertRaises(services.RouteError) as caught:
                services.walking_route([1, 2], [2, 3], "test")
            self.assertEqual(caught.exception.code, "route_capacity")
        finally:
            services.route_slots.release()
            services.route_slots.release()
        connection.assert_not_called()

    @patch("core.services.http.client.HTTPSConnection")
    def test_missing_key(self, connection):
        """Functionality: Fail without making a request when no key is configured.
        Inputs: An explicitly empty-key environment. Outputs: 503 and zero upstream calls.
        Logic: Preserve configuration-failure semantics. Constraints: Do not probe credentials for
        other projects.
        """
        with patch.dict("os.environ", {"ORS_API_KEY": ""}):
            with self.assertRaises(services.RouteError) as caught:
                services.walking_route([1, 2], [2, 3], "test")
        self.assertEqual(caught.exception.status, 503)
        connection.assert_not_called()
