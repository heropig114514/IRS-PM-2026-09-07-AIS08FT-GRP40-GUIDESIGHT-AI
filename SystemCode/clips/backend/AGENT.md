# PRS voice/text agent MVP

The maps and camera pages share an assistant entry point. Users record up to 20 seconds or type
requests. Qwen interprets them, the backend invokes existing controllers through the official MCP
SDK, and the page displays progress and replies. Browser permission remains under user control.
Changing pages stops the previous page's device functions but preserves the same tab's conversation.

Development prose, tool descriptions and Skill workflows use English. User-facing messages and
intent examples retain Chinese/English support; reply language is independent of development language.

## Architecture and responsibilities

```text
SystemCode/clips/frontend/voice-input.js + voice-worklet.js   Microphone -> 16 kHz PCM16 WAV
SystemCode/clips/frontend/agent-client.js                    Voice/text, progress, cancel, clear, page reattachment
          /ws/agent/
core/agent/transport.py                     Origin checks, tab ownership, event replay
core/agent/intents.py + intents.json         Semantic routing, supported catalog, unmatched policy
core/agent/runtime.py                       Intent admission, mapped Skills, bounded execution/memory
core/agent/provider.py                      Qwen tool calling and qwen3-asr-flash
core/agent/skills.py + agent_skills/         Skill discovery and on-demand loading
core/agent/tools.py                         Official MCP server bound to the current tab
          Official MCP Client(server), in-memory protocol transport
core.ai_mcp / core.ai_bridge                Existing operations and browser control channel
SystemCode/clips/frontend/agent-device.js                    Semantic map/camera adapters
          maps-page / navigation / camera / speech-output / vibration
```

`agent_skills/*/SKILL.md` owns workflows, missing-input questions, place ambiguity and capability
boundaries. Python does not branch on natural-language command keywords. `agent_skills/intents.json`
defines intent IDs, bilingual labels, semantic descriptions, examples and Skill mappings. Examples
are not exhaustive keywords or restrictions on destinations. Each turn first invokes Qwen's structured
`route_intent`; the server validates catalog enums and response contracts before entering Skill/MCP
execution. The catalog and `intent_router.md` are separate from execution code and included in the
bundle fingerprint. System instructions load Skill names/descriptions; a selected Skill may load
`references/*.md`, with a combined 16000-character limit. There is no place/business/campus alias table;
actual places and coordinates come from the map service.

A system intent requires `discover_tools` against the bound MCP registry, then `read_skill` for every
mapped Skill. Both the name schema and execution validation prevent loading unmapped Skills.
`tools.json` declares allowed tool names; the runtime intersects them with actual MCP registration
before exposing schemas. Skipping discovery/loading or crossing an unauthorized domain fails before
action, without repair retries. Compound requests must match their individual intents, at most three
per turn by default. Discovery reports missing tools; registration does not prove device permission,
service credentials or provider availability. Actual tool results remain authoritative.

The same JSON declares per-turn prerequisites (`requires`) and call limits (`limits`). Callable schemas
reflect successful tool evidence. Navigation requires category discovery before nearby queries;
name search, category discovery and nearby queries are each limited to one call per turn. The model
cannot silently expand scope and search repeatedly. `result_counts` specifies result-count ranges
required to continue; `confirm_if` identifies boolean confirmation flags. Empty/multiple named-place
results, empty nearby results and pronunciation confirmation end the action phase for clarification
or selection. Candidates remain available next turn. This conservative flow prevents automatically
using an institution center as a specific destination.

`selection` specifies label fields, display limits and bilingual prompts. The executor renders numbered
lists from verbatim service `label` values and ends the action phase, preventing model renaming of
roads/bridges as target buildings. Full candidates remain in tool history. The policy also declares
confirmation-sensitive tools, the planning tool and its coordinate argument. Pending selection persists
across turns. Only a whole-utterance number, such as "第一个", "第四个" or "first", or a unique full
candidate name is accepted. Until confirmation, planning/start tools are hidden and rejected. Planning
coordinates must exactly match the selected candidate; only accepted planning releases start permission.
Abbreviations and related names cannot be inferred as confirmation. A new named lookup replaces
pending candidates; clearing the conversation clears selection.

Subsequent execution alternates model tool selection, MCP execution and real results until completion
or clarification. Model-generated replies and semantic routing are not guarantees for arbitrary
expressions; no uncalibrated self-reported confidence is used. Missing parameters do not mean an
unsupported intent: starting navigation without a destination still enters that workflow and asks
for the destination after inspecting map state. Follow-up names/numbers use prior dialogue; explicit
new actions override pending tasks and completed actions are not automatically repeated.

Ambiguous operations, such as an unqualified stop request, use `clarify` without creating MCP.
General questions use `general`; unsupported actions or compounds containing them use `unsupported`,
without partial execution. `unknown_policy=reject` is the default and declines both unmatched routes.
Explicitly setting `unknown_policy=general` enables factual conversation via `general_assistant.md`,
with dialogue only and no MCP/tools. Unsupported actions still fail. Model errors/timeouts do not
switch to general chat or retry. Help renders the current catalog without MCP or device-readiness
claims. The UI displays intent decisions before actual tool activity.

History retains discovery/Skill bodies only as loaded markers; each turn discovers and loads again.
Actual candidates, user input and action results remain intact. The 120000-character context boundary
is unchanged. MCP registry schemas are reused rather than duplicated. Code owns input validation,
permissions, cancellation, deadlines, correlation IDs, cleanup and observed state. Camera/navigation
start tools issue one start and observe for up to 12 seconds, returning `verified` and actual state.
Observation is not retrying and changes no controller parameters. Snapshots exclude the assistant UI
so its own replies cannot become independent evidence.

The 24 conversation tools are available only through the in-memory MCP server. Existing public `/mcp`
`full/outputs` catalogs and database contracts are unchanged. The agent receives no database, arbitrary
HTTP/file access or control over other tabs.

## Connected operations

| Domain | Operations and evidence boundaries |
|---|---|
| Navigation | Start/stop/status, remaining distance, missing-destination questions, separate speech status |
| Maps | Inspect forms, device origin, name/address lookup, live category discovery, nearby POIs, route planning |
| Camera | Open/close, explicit Echo/HF selection, media tracks and decoded-frame evidence |
| Scene description | Existing page controls and VLM module, with its separate configuration |
| Speech output | Voices, speak, stop, engine status; the user must first enable output |
| Vibration | Status, short/long/double patterns and stop; the user must first enable output |
| Interface | Camera/maps page changes, language selection and observed existing controls |

Example: request navigation on the camera page, switch to maps and inspect the existing route, ask
for a missing destination, obtain the actual origin and candidates, clarify genuine campus/entrance
ambiguity, then plan, start and verify. Neither the NUS map preview nor the first geocoder result is
proof of user location or destination selection.

Navigation distinguishes deployment search scope, live device location and service-returned places
and categories. `read_skill` reports the configured search country and its source, explicitly separate
from user location. The Singapore MVP defaults to `SGP`; explicit overseas requests may override
`places_search.country`. Requests carry `boundary.country`; GPS `focus` only ranks results. Responses
include country, entity name, layer and accuracy. A remote namesake or neighborhood center cannot
substitute for a specified campus/entrance. Name/address normalization preserves branch/address
qualifiers without inventing unfamiliar proper names or coordinates.
An explicit business/brand/name or address takes priority over generic category words: listing
branches of a named chain uses name lookup. Category-only nearby search cannot preserve a brand filter.

Pronunciation correction handles suspected homophones, misspellings and cross-language phonetic
renderings separately from accurate translation. Qwen supplies at most two sound-alike hypotheses.
`places_search.original_text` preserves the exact user place name, while `text` is the actual query;
explicit pronunciation mode adds at most two `pronunciation_queries`. `place_resolution.py` queries
each distinct term once through ORS, retains provenance and deduplicates by actual name/coordinates,
using the same country/focus. Ordinary searches still issue one query. Case-insensitive duplicate
terms are omitted and counted in `duplicate_queries_omitted`, never replaced with other terms; the
original candidate parameter still permits at most two entries.

These hypotheses are neither verified aliases nor acoustic confidence. There is no handwritten place
table or numerical pronunciation score. Pronunciation mode and every `text`/`original_text` rewrite,
including translation, set `requires_confirmation=true`; even one candidate ends the action phase
until the user confirms. At most three distinct queries share the existing 35-second tool deadline.
Any provider error fails directly without remaining queries or partial success. An empty ordinary
lookup followed by suspected transcription error requires clarification first and a new turn for the
corrected lookup. There are no ASR n-best alternatives, phonemes or acoustic scores; correction is
based on transcript text. Coverage depends on model hypotheses and provider data, so no results do
not prove a place does not exist.

This conservative policy adds confirmation even for accurate translations with one result, to prevent
uncertain transliterations from bypassing review. A later user name different from the actual entity
requires clarification. Only explicit number/full-name selection of a returned bridge, road or other
entity permits using its coordinates, without presenting it as the original target. Executor checks
enforce this boundary independently of Skill prose.

`PRS_AGENT_SEARCH_LANGUAGE` (default `en`) chooses the default region's initial name-query language,
independently of voice/UI language; explicit overseas requests retain local names. Normalization
provides no place facts; coordinates must come from the service. Category requests first discover
IDs via `places_categories` using ORS `/pois` with `request=list`, then call `places_nearby`. No static
category-to-ID table is maintained; the adapter validates IDs against discovery from this turn.
Nearby queries default to 2000 meters, accept explicit 100–2000-meter radii and return at most five
results ordered by service-provided straight-line distance. These are not walking distances or proof
of complete business/opening-hours coverage. Unless authorized to choose the nearest, ask the user
to select among multiple candidates; then reuse coordinates with `maps_plan` and `navigation_start`.
Empty results and service errors are reported explicitly, without changing country, expanding radius,
switching to name lookup or another provider. See the [ORS POI contract](https://giscience.github.io/openrouteservice/api-reference/endpoints/poi/).

Navigation includes map/text guidance and a separate speech module disabled by default. Users must
enable navigation speech and speech output and select a matching local voice. The agent inspects
state without enabling these switches or guaranteeing audible output. There is no automatic vibration,
obstacle avoidance or rerouting. Generic camera opening preserves the selected mode; HF is selected
only for an explicit detection request. Hidden pages stop existing navigation/camera activity and
cancel recording. There is no wake word, background or lock-screen operation. This MVP submits one
utterance after recording, then transcribes and executes tools; it is not continuous realtime dialogue.

## Configuration and operation

Set in `SystemCode/clips/backend/.env`:

```dotenv
PRS_AGENT_API_KEY=<private DashScope key, stored locally only>
PRS_AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
PRS_AGENT_MODEL=qwen3.6-plus
PRS_ASR_MODEL=qwen3-asr-flash
PRS_AGENT_SEARCH_COUNTRY=SGP
PRS_AGENT_SEARCH_LANGUAGE=en
```

The explicit mainland/international DashScope URLs require matching regional keys. There is no
automatic region/model substitution. Search country is an uppercase three-letter code, default `SGP`;
it bounds named-place lookup without replacing GPS or prohibiting explicit overseas requests.
`ORS_API_KEY` serves existing routes and place search. Scene description retains `DASHSCOPE_API_KEY`,
`DASHSCOPE_HOST` and `VLM_MODEL`. When using a reference project's configuration, copy only actually
missing fields, never its whole `.env`; preserve Django/database/inference conditions. Keys must not
reach the frontend, reports, logs or Git.

Start from `SystemCode/clips/backend/` with one worker:

```powershell
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --workers 1 --lifespan on --ws websockets-sansio --ws-max-size 1310728 --ws-per-message-deflate false
```

Open `http://127.0.0.1:8766/live/` or `/maps/`. Localhost is a secure context for browser recording;
remote access requires HTTPS and actual microphone/location/camera permissions. Disconnection asks
for refresh, without automatic reconnect. Normal cross-page reattachment resumes the same conversation
without executing submitted requests again.

## Execution, data and diagnostics

- Accept one task at a time. Do not execute duplicate request IDs, implicitly queue or retry.
- Each task has 180 seconds, 12 text-model calls including one intent decision, and 30 tool calls.
  Model/ASR requests each have 40 seconds. Routing consumes the existing budget; model, temperature,
  total deadline and step limits remain unchanged.
- Keep the latest eight complete turns and 160 events, up to 16 conversations. Disconnected sessions
  idle for 30 minutes are cleaned on subsequent connections. Context over 120000 characters fails
  explicitly with a request to clear history, without hidden summarization, model switching or truncation.
- Audio is processed in memory: 0.1–20 seconds, 16 kHz mono PCM16 WAV. Text, audio and necessary tool
  results go to Qwen in the configured region; search queries/focus go to ORS. Device coordinates
  are tool results and may enter Qwen context. The app does not persist recordings, conversations
  or coordinates to disk/database; external data policies belong to each provider.
- The session ID and separate random tab token remain in `sessionStorage` across pages. The control
  connection verifies ownership; a public session ID alone cannot attach to an existing conversation.
  This binding does not replace account authentication or existing MCP access controls.
- Logs record request/session IDs, intent route/IDs/policy, Skill, phase, tool name, status code,
  duration and exception type, never keys, audio, user text, tool arguments/results or coordinates.
  Tool arguments remain visible in that page's conversation events for local evaluation.
- Cancel prevents later steps but cannot undo dispatched browser actions. Timeout/disconnection leaves
  completion unknown; observe real state before continuing. Explicitly request device shutdown when needed.

## Validation

```powershell
# backend: unit/protocol boundaries with explicit Qwen/network/device doubles
.\.venv\Scripts\python.exe manage.py test core.test_agent core.test_agent_discovery core.test_agent_intents
# frontend: existing contracts plus WAV/device-state tests
node --test tests/*.test.mjs
# frontend: real Qwen + MCP + Chrome; explicit GPS/ORS/camera test inputs
$env:PRS_PLAYWRIGHT_PATH='<absolute path to an installed playwright package>'
node tools/check_agent_browser.mjs http://127.0.0.1:8766/live/ ../backend/benchmarks/2026-10-21/agent/browser.json --live-qwen
```

Optional `--audio=<absolute WAV path>` uses Chrome's synthetic microphone to test recording, WAV,
real Qwen ASR and the agent. `check_agent_extended.mjs <URL> <REPORT> --live-qwen` covers real ORS lookup,
location denial, cancellation, enabled speech/vibration and real VLM. GPS/routes/camera/output hardware
are explicit doubles; this does not prove physical output. `check_agent_location.mjs` uses the original
Chinese NUS request to test semantic names, country bounds, campus clarification, real ORS routing,
overseas branches and denied location. It substitutes only GPS; geocoding, routes and model calls are
real, without claiming real phone location or road safety. `check_agent_intents.mjs` additionally checks
catalog admission, multi-turn selection and exact provider identity before pronunciation-based navigation.

`--live-qwen` consumes configured model quota and does not establish phone GPS/microphone quality or
speaker output. Preserve failed and corrected reports with explicit boundaries. Production success
rates require natural expressions, noise and multiple devices. The explicit main model is `qwen3.6-plus`,
non-thinking, temperature=0, without runtime switching/retries. Record model, Skill fingerprint, source
hashes, test inputs and device doubles in evaluations; retain failures.

Releases must include agent code, Skills and new assets together. The full allowlist includes them;
legacy vibration/outputs/navigation overlays require explicit pre-agent snapshots to avoid partial
dependencies. Full releases can preserve the restricted public catalog with `--mcp-profile outputs`.
Activation accepts explicit `--agent-env .env`, transferring only allowlisted voice/VLM settings while
preserving server HF/ORS/database credentials. Configuration goes through SSH/SFTP to a dedicated
root0600 environment file, never a complete local `.env` upload. Scene description retains shared VLM
configuration, default `qwen3-vl-plus`; verify permissions/region on 403 without changing models.
Validate actual phone microphone, GPS, speakers, vibration and GPU inference separately end to end.

## Design references

- [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents):
  simple composable execution, explicit tool contracts and evaluation before adding complexity.
- [Anthropic: Agent Skills](https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills):
  discover metadata before loading workflows on demand; maintain workflows separately from execution code.
- [Qwen Function Calling](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-function-calling):
  structured tool calls, returned execution evidence and explicit non-thinking mode.
- [Qwen-ASR API](https://help.aliyun.com/en/model-studio/qwen-asr-api-reference): base64 WAV input through
  the verified regional interface, independent of browser SpeechRecognition provider differences.
