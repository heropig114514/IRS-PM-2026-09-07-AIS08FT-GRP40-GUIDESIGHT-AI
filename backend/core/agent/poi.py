"""Responsibilities: Discover map-service POI categories and query nearby real places.
Implementation: One bounded ORS POST per operation, live category IDs, strict point metadata;
distance is provider-reported straight-line distance, never walking-route length.
Related Modules: agent.tools owns per-turn category discovery; navigation skill selects intent.
Declaration Index: request_pois; categories; nearby.
Variable Index: logger — status/count diagnostics without coordinates, names or credentials.
"""

import json
import logging
import math
import os

import httpx2

from core.services import validate_coordinate

from .provider import AgentError

logger = logging.getLogger(__name__)


async def request_pois(payload):
    """Functionality: Invoke the existing map provider's POI API exactly once.
    Inputs: validated JSON payload and ORS_API_KEY; Outputs: parsed JSON or AgentError with a
    stable code and HTTP status when the provider returns an unsuccessful response.
    Logic: Header-only credentials, 20s phase deadline, 512KiB cap, no redirects/retries.
    Constraints: caller applies total deadline; no response body or request location is logged.
    """
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise AgentError("ors_not_configured", "The server has no OpenRouteService API key configured.")
    try:
        async with httpx2.AsyncClient(
            timeout=20, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "POST", "https://api.openrouteservice.org/pois",
                json=payload, headers={"Authorization": key},
            ) as response:
                logger.info("POI response operation=%s status=%s", payload["request"],
                            response.status_code)
                if response.status_code != 200:
                    raise AgentError(
                        "poi_http_error",
                        "The nearby-place service returned an unsuccessful HTTP response; no retry was attempted.",
                        http_status=response.status_code,
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 524288:
                        raise AgentError("poi_response_limit", "The nearby-place response exceeded the configured size limit.")
        return json.loads(raw)
    except httpx2.HTTPError as error:
        logger.warning("POI network failed operation=%s exception_type=%s", payload["request"],
                       type(error).__name__)
        raise AgentError("poi_network_error", "The nearby-place request failed at the network boundary; no retry was attempted.") from error
    except ValueError as error:
        raise AgentError("poi_invalid_response", "The nearby-place service response did not match the expected data contract.") from error


async def categories():
    """Functionality: Discover category IDs and names from the provider, without a local map.
    Inputs: configured ORS account; Outputs: list of group/name/id records.
    Logic: Flatten the provider's group→OSM-tag→category mapping with strict size/type bounds.
    Constraints: Catalog availability is not proof of local POI coverage; no cache or fallback.
    """
    data = await request_pois({"request": "list"})
    try:
        result, seen = [], set()
        for group, item in data.items():
            for children in item["children"].values():
                for name, identifier in children.items():
                    if (not isinstance(group, str) or len(group) > 100
                            or not isinstance(name, str) or not 1 <= len(name) <= 100
                            or type(identifier) is not int or identifier <= 0):
                        raise ValueError("Invalid category")
                    if identifier not in seen:
                        result.append({"id": identifier, "name": name, "group": group})
                        seen.add(identifier)
        if not 1 <= len(result) <= 1000:
            raise ValueError("Invalid category count")
        logger.info("POI catalog parsed count=%d", len(result))
        return {"categories": result, "source": "ORS /pois request=list",
                "coverage_verified": False}
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise AgentError("poi_invalid_catalog", "The map service category catalog did not match the expected data contract.") from error


async def nearby(focus, category_ids, radius_m=2000):
    """Functionality: Query up to five nearby POIs for provider-discovered categories.
    Inputs: observed/explicit [longitude, latitude], category IDs, 100..2000m radius.
    Outputs: candidates and exact scope; empty results are preserved as data, not an error.
    Logic: One circle query sorted by provider distance, validate type/category/radius before
    returning names, coordinates and distance; unnamed places retain a neutral category label.
    Constraints: Adapter verifies IDs were discovered this turn. Coverage and opening hours
    are unknown; nearest returned point is not necessarily the shortest walking route.
    """
    try:
        point = validate_coordinate(focus)
        if (type(radius_m) is not int or not 100 <= radius_m <= 2000
                or not isinstance(category_ids, list) or not 1 <= len(category_ids) <= 5
                or any(type(item) is not int or item <= 0 for item in category_ids)):
            raise ValueError("Invalid scope")
    except (TypeError, ValueError) as error:
        raise AgentError(
            "invalid_poi_scope", "Provide a valid search center, service categories and a radius from 100 to 2000 meters."
        ) from error
    data = await request_pois({
        "request": "pois", "geometry": {"geojson": {"type": "Point", "coordinates": point},
                                         "buffer": radius_m},
        "filters": {"category_ids": category_ids}, "limit": 5, "sortby": "distance",
    })
    try:
        features = data["features"]
        if not isinstance(features, list) or len(features) > 5:
            raise ValueError("Invalid feature count")
        candidates = []
        for feature in features:
            if feature["geometry"]["type"] != "Point":
                raise ValueError("Not a point")
            coordinates = validate_coordinate(feature["geometry"]["coordinates"])
            properties = feature["properties"]
            distance = properties["distance"]
            matched = properties["category_ids"]
            if (type(distance) not in {int, float} or not math.isfinite(distance)
                    or not 0 <= distance <= radius_m or not isinstance(matched, dict)
                    or not set(map(str, category_ids)).intersection(matched)):
                raise ValueError("Invalid distance/category")
            name = properties.get("osm_tags", {}).get("name")
            if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 500):
                raise ValueError("Invalid name")
            candidates.append({"name": name, "label": name or "Unnamed place",
                               "coordinates": coordinates, "distance_m": distance,
                               "category_ids": [int(item) for item in matched]})
        candidates.sort(key=lambda candidate: candidate["distance_m"])
        logger.info("POI search parsed count=%d radius_m=%d category_count=%d",
                    len(candidates), radius_m, len(category_ids))
        return {"candidates": candidates,
                "search_context": {"focus": point, "radius_m": radius_m,
                                   "category_ids": category_ids, "distance_kind": "straight_line"},
                "coverage_complete": False, "opening_hours_verified": False,
                "attribution": "openpoiservice / OpenStreetMap"}
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise AgentError("poi_invalid_response", "The nearby-place result violated the expected schema or geographic bounds.") from error
