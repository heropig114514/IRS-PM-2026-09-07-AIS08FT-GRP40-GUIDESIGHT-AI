---
name: navigation
description: Start/stop navigation, inspect status, obtain the real device origin, and find destinations by name/address or service category using the existing walking route and navigation controllers.
---

Use only the actual tools and parameters returned by discover_tools. Never invent a missing tool.
read_skill.environment_context describes search configuration; maps_locate supplies the real device origin.
The executor exposes places_search, places_categories and places_nearby only after a successful
maps_locate in this turn. This ordering is an execution invariant: a coordinate visible in a map
preview, a previous turn, or a test fixture cannot authorize a new provider query.
Do not maintain place, business or campus alias tables. Places, branches, categories and coordinates
must come from actual map service results. Language understanding cannot replace tool evidence
with remembered places or coordinates.
The requires/limits in tools.json constrain this turn: discover categories before nearby queries;
call the name search and nearby tools at most once each. Ordinary name lookup sends one query.
Explicit pronunciation correction checks the original name and at most two sound-alike hypotheses
inside the name search tool. Do not retry, change country or expand scope after a tool error.
result_counts allows name lookup to continue only with one candidate; zero or multiple results end
this turn's action phase. Ask for details or selection, then reuse results next turn. An empty
nearby category result also ends the action phase.

1. Read ui_snapshot. Navigation requires the maps page; use ui_navigate(page="maps") if needed,
   then maps_status. Changing pages stops the previous page's camera but preserves the conversation.
   On the product page, maps_locate supplies the origin and map selection supplies the destination;
   never infer either point from a map preview or diagnostic sample. A page-only request ends here;
   a location-only request uses maps_locate and reports its result without requiring a destination.
2. For "stop navigation", call navigation_stop and report returned active/state. For remaining
   distance or current status, use navigation_status and progress distance/time; never replan
   a route merely to answer a status query.
3. For "start navigation", if maps_status.navigation.route exists and no new destination was
   requested, call navigation_start directly. If already navigating, report that state without restarting.
4. If no route exists, inspect both the user's request and maps_status.destination.
   **If neither supplies a destination, ask where the user wants to go and end this turn.
   Do not call places_search, maps_plan or navigation_start. Never search for "start navigation"
   or for destinations appearing only in skill/page examples.**
5. Once the user supplies a destination, including in a follow-up, continue the navigation request.
   **Before searching for the destination**, call maps_locate to obtain the real device origin,
   and pass the successful result's origin to places_search.focus, unless the user explicitly
   requests another origin. On failure/denial, explain the returned reason and end this turn;
   do not substitute an example, IP-derived location or guess.
6. Select tools according to the supplied information; do not route every request through name search:
   A supplied business/brand/name or address takes priority over a generic category word. Requests
   to list stores of a named chain still use places_search with that name, including when described
   as a supermarket, pharmacy or restaurant. places_nearby cannot filter a named brand: never discard
   the name and substitute a category-only query. If a business-like transcript plausibly sounds like
   a specific business name from recent conversation, apply the contextual pronunciation rule below
   instead of requiring exact spelling. This rule uses the user's actual constraint, not a fixed brand
   dictionary.
   - Explicit coordinates, an existing map destination or an already selected candidate: reuse directly.
   - Specific name/address: use places_search. Normalize an ordinary name according to
     environment_context.query_language. original_text must preserve the user's place name verbatim,
     never a translation, normalized name or guess. text is the actual search query; any rewrite
     that differs from original_text requires confirmation, even with a single result.
     For example, en selects English search for this deployment; reply in the user's language.
     Preserve brand, branch, address and city qualifiers; do not invent unfamiliar proper names.
     country defaults to the deployment scope; override for an explicitly requested overseas destination.
     focus uses the real origin and affects ranking, not a radius boundary. For overseas requests,
     preserve local destination names and original addresses rather than forcing the default region's language.
     Make one tool call per destination per turn; do not repeat with another language/country afterward.
     Check name, country_code, layer and accuracy: a center is not an entrance, and a remote branch
     is not a substitute for the requested place.
   - Category/nearby requests without a specific business/brand/name/address constraint, such as
     supermarkets, pharmacies, restaurants or stations: first use
     places_categories to discover real service categories, select returned IDs by meaning, then call
     places_nearby(focus=actual_origin, category_ids=discovered_categories).
     **Never pass "nearby supermarket" to places_search as a proper name or fill category IDs from memory.**
     If no radius was specified, use the tool's published 2000-meter default and state that scope.
     Query once; do not silently expand the radius. If no category matches, explain the gap and ask
     for a name/address instead of substituting another category.
     Candidate distance_m is straight-line distance, not walking distance. At most describe a result
     as the closest by straight-line distance among returned candidates. Select the first only if the
     user explicitly authorized choosing the nearest; explain that basis. Otherwise list up to three
     actual candidates for selection.
   Plan directly for a single clear match at the requested granularity. For multiple candidates,
   ask one discriminating question without reconfirming an already clear country/city. Do not ignore
   plausible branches because an institution's center is nearest, or treat an institution name as
   selection of a particular campus/store.
   - Suspected pronunciation/transcription error, approximate-name wording or explicit sound-alike
     correction: preserve the heard name in text and infer at most two pronunciation_queries from
     Chinese homophones/near sounds, English pronunciation/spelling or cross-language transliteration.
     Use recent conversation context to resolve likely speech-recognition substitutions before
     searching: when the user previously named a specific business and the current transcript is a
     plausible sound-alike (for example, prior “Sheng Siong supermarket” followed by “Samsung” or
     “Shin Song”), include that exact contextual business name as the first pronunciation query.
     Do not require the user to announce that recognition failed, and do not treat the contextual
     name as confirmed intent.
     Use no fixed alias table; preserve explicit branch, address and country constraints.
     The user need not announce a pronunciation error. Nonstandard Chinese phonetic renderings of
     foreign names that require a sound-based guess must use pronunciation_queries and retain the
     original transcript. Even a plausible guess is not an ordinary translation that bypasses confirmation.
     Hypotheses are search leads, not verified names or place knowledge. Accurate translation is not
     pronunciation correction; do not present an uncertain unfamiliar name as established fact.
     If no plausible hypothesis exists, ask for spelling or an address.
     The tool queries the original text and hypotheses separately; names/locations still come only
     from the service. Never invent numerical pronunciation confidence. requires_confirmation=true
     ends the action phase: identify these as sound-alike candidates, show up to three actual names
     with necessary area information, and ask for confirmation even for a single candidate.
     After confirmation, reuse its returned coordinates without searching the same name again.
     If nothing matched, explain that result and ask for details.
     The executor renders selection lists from verbatim service labels, without model rewriting.
     A later user name that differs from the candidate entity is not confirmation: a nearby bridge,
     entrance, road or area cannot stand in for a building. The executor accepts only a whole-utterance
     number (for example, "第一个", "第四个", "first") or a full candidate name; abbreviations,
     related names and paraphrases do not release planning permission. Explain the actual candidate
     type and request its number. Once selected, maps_plan must use that candidate's exact coordinates;
     navigation can start only after successful planning. A search term is not a returned entity name.
   Zero results mean this service query returned no candidates in its scope, not that no shops exist.
   Ask for a specific name/address or a map selection; do not change providers, expand scope or retry.
   Search-only requests list candidates without planning or starting navigation; do not claim proximity
   without location evidence. After selection next turn, reuse the actual returned coordinates;
   if the origin is missing/stale, call maps_locate, then maps_plan. If navigation was requested,
   continue with navigation_start without asking for the same destination again.
7. maps_plan(destination=[longitude,latitude]) uses confirmed candidate or user coordinates;
   destination=null uses the map destination. Omit origin to use the origin filled by maps_locate;
   supply origin only when the user explicitly provided coordinates. Check accepted/error for the
   planning outcome; do not infer an ORS failure cause merely from a short distance.
8. After successful planning, continue navigation requests with navigation_start; the executor
   requires this as the next tool action for `navigation.start`, so do not ask the user to click
   the map's Start button. `navigation.plan` ends after planning and is never auto-started.
   Browser location permission remains user-controlled. navigation_start includes at most 12 seconds
   of read-only observation.
   Report state/verified: navigating/verified=true means turn-by-turn navigation is running; locating
   means the accepted location watch is waiting for a usable fix; off_route with active=true means
   the watch is running but its latest fix is outside the planned route tolerance, so it is not a
   start failure; error is a failed navigation. For off_route, ask the user to verify the current
   location/origin and manually replan; do not claim guidance is available or start a second watch.
   Do not repeat observation of the same terminal state merely for formality. **A promise to locate or plan ends the turn with no background continuation.
   Invoke tools before reporting results; sufficient input requires execution, not a future-tense promise.**
9. Enable spoken instructions by default when starting walking navigation for this blind-user
   application, unless the user explicitly requests silent navigation. Inspect
   navigation_status.navigation_speech and the observed output controls. The agent may enable
   `#navigation-speech-enabled` and `#speech-enabled` with ui_action when they are off; do not ask
   the user to operate these application switches. For an explicit silent-navigation request,
   disable both application switches through ui_action and verify their checked state.
   If no voice is configured, inspect available choices and select a matching available voice with
   ui_action; preserve an explicit user voice choice and do not replace a requested but unavailable
   voice. enabled/active_started indicates software selection/engine start, not audible speaker
   output. Report paused/reason accurately, and request a real page interaction only when the
   browser requires activation. Automatic vibration guidance and obstacle avoidance remain
   unavailable (vibration_guidance=false); do not promise them. There is no automatic rerouting,
   navigation cannot guarantee road safety, and timed-out actions must not be resubmitted.
