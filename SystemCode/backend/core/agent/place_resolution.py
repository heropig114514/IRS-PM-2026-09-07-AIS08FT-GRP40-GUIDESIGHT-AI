"""Responsibilities: Verify optional pronunciation-based query hypotheses against real places.
Implementation: Preserve the original query; at most two explicitly supplied phonetic variants
share the same country/focus. Deduplicate service results and require user confirmation.
Related Modules: places.search_places handles ORS; navigation Skill generates linguistic
hypotheses; runtime enforces the confirmation field declared in tools.json.
Declaration Index: resolve_places.
Variable Index: logger — query-count diagnostics only; MAX_VARIANTS — phonetic-query bound.
"""

import logging

from .places import search_places
from .provider import AgentError

logger = logging.getLogger(__name__)
MAX_VARIANTS = 2


async def resolve_places(text, original_text, focus=None, country=None, pronunciation_queries=None):
    """Functionality: Search a name, optionally comparing explicit sound-alike hypotheses.
    Inputs: primary search text, exact original_text from the user, optional focus/country,
    zero to two pronunciation queries.
    Outputs: real candidates, original search metadata, variant evidence and confirmation flag.
    Logic: Validate all hypotheses before I/O and normalize duplicate spellings with an explicit
    omitted count; execute each distinct query once against the same
    ORS service and scope; merge by coordinates/name, preserving query provenance.
    Constraints: Hypotheses are model linguistic guesses, not verified aliases or phonetic scores.
    All rewritten/phonetic-mode results require user selection, even a single result.
    No retry on error,
    country/radius/provider substitution or fabricated coordinates. Runtime bounds total tool time.
    """
    variants = [] if pronunciation_queries is None else pronunciation_queries
    if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 200
            or not isinstance(original_text, str) or not 1 <= len(original_text.strip()) <= 200
            or not isinstance(variants, list) or len(variants) > MAX_VARIANTS
            or any(not isinstance(query, str) or not 1 <= len(query.strip()) <= 200
                   for query in variants)):
        raise AgentError(
            "invalid_pronunciation_queries", "地名及读音候选须为非空短文本，候选最多两项。",
        )
    supplied = [text.strip(), *(query.strip() for query in variants)]
    queries, seen = [], set()
    for query in supplied:
        if query.casefold() not in seen:
            queries.append(query)
            seen.add(query.casefold())
    omitted = len(supplied) - len(queries)
    if omitted:
        logger.info("Phonetic query duplicates omitted count=%d", omitted)
    original = await search_places(queries[0], focus=focus, country=country)
    if not variants:
        if text.strip().casefold() != original_text.strip().casefold():
            return {**original, "original_query": original_text.strip(),
                    "resolution_mode": "rewritten_name", "requires_confirmation": True,
                    "hypotheses_verified_as_aliases": False}
        return original
    # Functionality: Verify bounded phonetic hypotheses independently against provider data.
    # Logic: Retain every query's candidate count; any provider error aborts the entire operation.
    # Constraints: Partial results are not silently substituted for a failed query's outcome.
    evidence = [{"query": queries[0], "kind": "original", "result": original}]
    for index, query in enumerate(queries[1:], start=1):
        logger.info("Phonetic hypothesis search started index=%d total=%d", index, len(variants))
        try:
            result = await search_places(query, focus=focus, country=country)
        except AgentError as error:
            logger.warning("Phonetic hypothesis search failed index=%d code=%s", index, error.code)
            raise
        evidence.append({"query": query, "kind": "phonetic_hypothesis", "result": result})
    merged = {}
    for entry in evidence:
        for candidate in entry["result"]["candidates"]:
            identity = (tuple(candidate["coordinates"]), candidate.get("name", candidate["label"]))
            item = merged.setdefault(identity, {**candidate, "matched_queries": []})
            item["matched_queries"].append(entry["query"])
    logger.info("Phonetic place resolution queries=%d candidates=%d confirmation_required=true",
                len(queries), len(merged))
    return {
        **original, "candidates": list(merged.values()), "requires_confirmation": True,
        "resolution_mode": "pronunciation_hypotheses", "original_query": original_text.strip(),
        "queries": [{"query": entry["query"], "kind": entry["kind"],
                     "candidate_count": len(entry["result"]["candidates"])} for entry in evidence],
        "hypotheses_verified_as_aliases": False,
        "duplicate_queries_omitted": omitted,
    }
