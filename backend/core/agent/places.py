"""Responsibilities: Resolve user-supplied destination names through ORS Pelias.
Implementation: Bounded HTTPS GET with header-only credentials, explicit country boundary and
observed focus coordinates; retain identity metadata for semantic candidate selection.
Related Modules: agent.tools, core.services.validate_coordinate; navigation skill.
Declaration Index: search_context; search_places.
Variable Index: logger — metadata diagnostics, no query/location/key logging;
DEFAULT_SEARCH_COUNTRY — Singapore deployment search scope, never a device-location claim;
PRS_AGENT_SEARCH_LANGUAGE — configured query language, default en, not the UI/voice language.
"""

import json
import logging
import os
import re

import httpx2

from core.services import validate_coordinate

from .provider import AgentError

logger = logging.getLogger(__name__)
DEFAULT_SEARCH_COUNTRY = "SGP"


def search_context():
    """Functionality: Describe the deployment's explicit default place-search boundary.
    Inputs: PRS_AGENT_SEARCH_COUNTRY (SGP), PRS_AGENT_SEARCH_LANGUAGE (en).
    Outputs: non-secret scope/language metadata. Logic: Validate uppercase country and lowercase
    language codes before any request is constructed; language guides the model's first query.
    Constraints: This configuration is not GPS or evidence of the user's current country;
    explicit per-request countries override it without changing the deployment setting.
    """
    country = os.environ.get("PRS_AGENT_SEARCH_COUNTRY", DEFAULT_SEARCH_COUNTRY).strip()
    if not re.fullmatch(r"[A-Z]{3}", country):
        raise AgentError("search_not_configured", "The configured search country must be a three-letter uppercase code.")
    language = os.environ.get("PRS_AGENT_SEARCH_LANGUAGE", "en").strip()
    if not re.fullmatch(r"[a-z]{2,3}", language):
        raise AgentError("search_not_configured", "The configured search language must be a lowercase language code.")
    return {"default_search_country": country, "query_language": language,
            "source": "deployment_configuration",
            "is_device_location": False}


async def search_places(text, focus=None, country=None):
    """Functionality: Return at most five real candidate labels and coordinates.
    Inputs: 1..200-character query, optional [longitude, latitude] focus, optional alpha-3
    country override, ORS_API_KEY and deployment country configuration.
    Outputs: Candidates with identity metadata, applied query/scope and rejected-candidate count;
    failures raise AgentError with a stable code and HTTP status when available.
    Logic: Apply country as a hard boundary and focus only as a ranking hint; verify country
    metadata, exclude outside-boundary results explicitly, never select a destination.
    Constraints: Query/focus go to ORS, remain out of logs; 20s phase timeout and 256KiB cap;
    caller adds total timeout. No retries, fallback geocoders, persistence or nearest guarantee.
    """
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
        raise AgentError("invalid_place", "Provide a destination name or address between 1 and 200 characters.")
    selected_country = search_context()["default_search_country"] if country is None else country
    if not isinstance(selected_country, str) or not re.fullmatch(r"[A-Z]{3}", selected_country):
        raise AgentError("invalid_country", "The search country must be a three-letter uppercase code.")
    params = {"text": text.strip(), "size": 5, "boundary.country": selected_country}
    if focus is not None:
        try:
            lon, lat = validate_coordinate(focus)
        except ValueError as error:
            raise AgentError("invalid_focus", "The search focus must contain valid longitude and latitude.") from error
        params.update({"focus.point.lon": lon, "focus.point.lat": lat})
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise AgentError("ors_not_configured", "The server has no OpenRouteService API key configured.")
    try:
        async with httpx2.AsyncClient(
            timeout=20, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "GET",
                "https://api.openrouteservice.org/geocode/search",
                params=params,
                headers={"Authorization": key},
            ) as response:
                logger.info("Place search response status=%s", response.status_code)
                if response.status_code != 200:
                    raise AgentError(
                        "geocoder_http_error",
                        "The geocoding service returned an unsuccessful HTTP response; no retry was attempted.",
                        http_status=response.status_code,
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise AgentError("geocoder_response_limit", "The geocoding response exceeded the configured size limit.")
        data = json.loads(raw)
        candidates, excluded = [], 0
        for feature in data["features"][:5]:
            if feature["geometry"]["type"] != "Point":
                raise ValueError("Not a point")
            point = validate_coordinate(feature["geometry"]["coordinates"])
            properties = feature["properties"]
            label = properties["label"]
            if not isinstance(label, str) or not 1 <= len(label) <= 500:
                raise ValueError("Invalid label")
            country_code = properties["country_a"]
            if not isinstance(country_code, str) or not re.fullmatch(r"[A-Z]{3}", country_code):
                raise ValueError("Invalid country provenance")
            if country_code != selected_country:
                excluded += 1
                continue
            candidate = {"label": label, "coordinates": point, "country_code": country_code}
            for name in ("name", "country", "region", "locality", "layer", "accuracy"):
                value = properties.get(name)
                if value is not None:
                    if not isinstance(value, str) or len(value) > 500:
                        raise ValueError("Invalid candidate metadata")
                    candidate[name] = value
            candidates.append(candidate)
        logger.info("Place search parsed candidates=%d excluded_country=%d scope=%s",
                    len(candidates), excluded, selected_country)
        return {
            "candidates": candidates,
            "search_context": {"query": text.strip(), "country": selected_country,
                               "country_source": "deployment" if country is None else "request",
                               "focus": focus},
            "excluded_country_count": excluded,
            "attribution": "openrouteservice / Pelias; sources in ORS geocoding attribution",
        }
    except httpx2.HTTPError as error:
        logger.warning("Place search network failed exception_type=%s", type(error).__name__)
        raise AgentError("geocoder_network_error", "The geocoding request failed at the network boundary; no retry was attempted.") from error
    except (KeyError, TypeError, ValueError) as error:
        raise AgentError("geocoder_invalid_response", "The geocoding service response did not match the expected data contract.") from error
