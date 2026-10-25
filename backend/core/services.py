"""Responsibilities: Call ORS walking routes, bound capacity, and enforce the shared route contract.
Implementation: Fixed HTTPS host, 20-second network-I/O timeout, 1 MiB response limit; no retries or redirects.
Related Modules: core.maps; the frontend consumes geometry/summary/steps.
Declaration Index: RouteError/RouteError.__init__ — public error; validate_coordinate — coordinate validation;
normalize_route — upstream contract validation; walking_route — rate-limit and call ORS.
Variable Index: ORS_HOST/ORS_PATH — sole upstream; TIMEOUT_SECONDS/MAX_RESPONSE_BYTES — transport bounds;
MAX_REQUESTS_PER_MINUTE — per-worker budget; route_slots — two concurrent slots;
request_times/request_lock — rate-limit state; logger — logs without keys/coordinates; ATTRIBUTION — route-data attribution.
"""

import http.client
import json
import logging
import math
import os
import time
from collections import deque
from threading import BoundedSemaphore, Lock

ORS_HOST = "api.heigit.org"
ORS_PATH = "/openrouteservice/v2/directions/foot-walking/geojson"
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1048576
MAX_REQUESTS_PER_MINUTE = 20
ATTRIBUTION = "© openrouteservice.org by HeiGIT | © OpenStreetMap contributors"
route_slots = BoundedSemaphore(2)
request_times = deque()
request_lock = Lock()
logger = logging.getLogger(__name__)


class RouteError(Exception):
    """Functionality: Carry public route errors without upstream bodies or credentials.
    Logic: Provide stable code/detail/status fields to the HTTP adapter.
    Constraints: detail is authored by this module and must not contain external exception text.
    """

    def __init__(self, code, detail, status):
        """Functionality: Initialize a diagnostic error.
        Inputs: self, public code/detail, and HTTP status; Outputs: exception state.
        Logic: Exception text is the safe detail; Constraints: no logging or network side effects.
        """
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def validate_coordinate(value):
    """Functionality: Validate a [longitude, latitude] coordinate.
    Inputs: JSON value; Outputs: two floats or ValueError.
    Logic: Reject booleans, non-finite values, incorrect dimensions, and out-of-range coordinates.
    Constraints: Do not accept numeric strings, change location, or infer coordinates from IP.
    """
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Coordinates must be [longitude, latitude].")
    for number, limit in zip(value, (180, 90), strict=True):
        if type(number) not in (int, float) or abs(number) > limit or not math.isfinite(number):
            raise ValueError("Longitude and latitude must be finite values within range.")
    return [float(number) for number in value]


def normalize_route(data):
    """Functionality: Convert real ORS GeoJSON into the stable application contract.
    Inputs: Decoded ORS JSON; Outputs: geometry/summary/steps/provider/profile/attribution.
    Logic: Validate geometry, distance, duration, and step indexes; remove request-coordinate copies from metadata.
    Constraints: Map upstream response failures to 502; do not generate substitute routes, invented steps, or default distances.
    """
    try:
        if data["type"] != "FeatureCollection" or len(data["features"]) != 1:
            raise ValueError
        feature = data["features"][0]
        geometry = feature["geometry"]
        if geometry["type"] != "LineString" or len(geometry["coordinates"]) < 2:
            raise ValueError
        coordinates = [validate_coordinate(point) for point in geometry["coordinates"]]
        properties = feature["properties"]
        summary = properties["summary"]
        steps = []
        for segment in properties["segments"]:
            for step in segment["steps"]:
                if not isinstance(step["instruction"], str) or not step["instruction"].strip():
                    raise ValueError
                points = step["way_points"]
                if (
                    not isinstance(points, list)
                    or len(points) != 2
                    or any(type(point) is not int for point in points)
                    or not 0 <= points[0] <= points[1] < len(coordinates)
                    or type(step["type"]) is not int
                    or not 0 <= step["type"] <= 13
                ):
                    raise ValueError
                steps.append(
                    {
                        "instruction": step["instruction"],
                        "type": step["type"],
                        "distance_m": step["distance"],
                        "duration_s": step["duration"],
                        "way_points": points,
                    }
                )
        values = [summary["distance"], summary["duration"]]
        values.extend(value for step in steps for value in (step["distance_m"], step["duration_s"]))
        if not steps or any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
            for value in values
        ):
            raise ValueError
        return {
            "provider": "openrouteservice",
            "profile": "foot-walking",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "summary": {"distance_m": summary["distance"], "duration_s": summary["duration"]},
            "steps": steps,
            "attribution": ATTRIBUTION,
        }
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise RouteError(
            "ors_invalid_response", "ORS returned route data that violates the API contract.", 502
        ) from error


def walking_route(start, end, request_id):
    """Functionality: Call hosted ORS while bounding upstream resource use for the anonymous diagnostic service.
    Inputs: Validated start/end, non-sensitive request_id, and ORS_API_KEY environment.
    Outputs: Normalized route or RouteError; logs contain only IDs, status, duration, and counts.
    Logic: Limit each worker to 20 requests/minute and two concurrent calls; send credentials only in Authorization; finally release the connection and slot.
    Constraints: Per-worker rate limiting is not authentication; enforce 1 MiB and 20-second network-I/O bounds; no retries, redirects, or coordinate storage.
    """
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise RouteError("ors_not_configured", "ORS_API_KEY is not configured on the server.", 503)
    started = time.monotonic()
    with request_lock:
        while request_times and request_times[0] <= started - 60:
            request_times.popleft()
        if len(request_times) >= MAX_REQUESTS_PER_MINUTE:
            raise RouteError(
                "route_rate_limit", "The route request limit of 20 per minute was reached; try again manually later.", 429
            )
        if not route_slots.acquire(blocking=False):
            raise RouteError("route_capacity", "Two route requests are already in progress; try again manually later.", 429)
        request_times.append(started)
    connection = None
    try:
        connection = http.client.HTTPSConnection(ORS_HOST, timeout=TIMEOUT_SECONDS)
        logger.info("ORS request started id=%s profile=foot-walking", request_id)
        body = json.dumps({"coordinates": [start, end], "instructions": True, "language": "en"})
        # Functionality: Request GeoJSON; Logic: Match Accept to the requested output format to avoid ORS 406.
        # Constraints: Content-Type describes the JSON request; Accept describes the GeoJSON response.
        connection.request(
            "POST",
            ORS_PATH,
            body=body,
            headers={
                "Authorization": key,
                "Content-Type": "application/json",
                "Accept": "application/geo+json",
                "User-Agent": "PRS-VisionGuide/1.0",
            },
        )
        response = connection.getresponse()
        logger.info("ORS response received id=%s status=%s", request_id, response.status)
        if response.status in (401, 403):
            raise RouteError(
                "ors_authentication_failed", "ORS rejected the credentials; check the server key or account permissions.", 502
            )
        if response.status == 429:
            raise RouteError("ors_quota_exceeded", "The ORS quota limit was reached; check the account quota.", 503)
        if response.status in (400, 404, 422):
            raise RouteError("ors_route_unavailable", "ORS could not generate this walking route; check the endpoints.", 422)
        if response.status != 200:
            raise RouteError("ors_upstream_error", "ORS returned a non-success status.", 502)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RouteError("ors_response_too_large", "The ORS route exceeds this prototype's 1 MiB response limit.", 502)
        try:
            route = normalize_route(json.loads(raw))
        except (ValueError, UnicodeDecodeError) as error:
            raise RouteError("ors_invalid_response", "The ORS response could not be decoded as JSON.", 502) from error
        logger.info(
            "ORS request completed id=%s elapsed_ms=%d points=%d steps=%d",
            request_id,
            (time.monotonic() - started) * 1000,
            len(route["geometry"]["coordinates"]),
            len(route["steps"]),
        )
        return route
    except TimeoutError as error:
        raise RouteError("ors_timeout", "ORS network I/O timed out; check the network and try again manually.", 504) from error
    except (OSError, http.client.HTTPException) as error:
        logger.warning(
            "ORS transport failed id=%s exception_type=%s", request_id, type(error).__name__
        )
        raise RouteError(
            "ors_network_error", "Could not connect to ORS; check server networking or TLS.", 502
        ) from error
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            route_slots.release()
