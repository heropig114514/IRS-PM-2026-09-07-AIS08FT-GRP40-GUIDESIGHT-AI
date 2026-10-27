# PRS AI Control Interface

The system uses version 2.3.0 of the [official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) to provide an MCP Streamable HTTP service. It shares one ASGI process and database with Django, the camera, and maps. MCP authentication and SQL permission restrictions have not been added, as currently requested.

The full interface described below uses the default `PRS_MCP_PROFILE=full`. Device-output configuration uses `PRS_MCP_PROFILE=outputs`, which includes `ui_sessions`, vibration/speech tools, and three navigation tools (11 tools total). Preserve the profile name; do not add generic DOM or database tools to this profile. Navigation status includes device location. This document describes the source contract; code changes do not mean the public service has been updated. Other functions remain available for the local full profile but are not registered in the public tool catalog. Invalid configuration stops startup and does not fall back.

The public entry is `https://47.239.50.129/prs/mcp` and uses the same Streamable HTTP client protocol. A caller discovers the tab by session after a real click and enabling output on a camera or map page with the corresponding controls. Vibration remains available only on the camera page. The public outputs profile has no tool that automatically enables the switch.

The local unified `--assist` entry also uses the `outputs` profile. Navigation MCP and page buttons on `/maps/` share the original navigation instance; speech MCP and automatic alerts share the original speech driver. Users explicitly control perception, navigation, and automatic-output switches. `navigation_start` does not automatically enable the camera or speech. Unified risk arbitration belongs to the page integration layer; it adds no navigation tools and changes no MCP parameters. `navigation_status` may still return device location. Unified mode applies only when started locally and does not prove that the public service has been updated.

## Startup and Connection

Run from `backend`; the existing `DJANGO_SECRET_KEY` and database configuration in `.env` remain in use:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --workers 1 --lifespan on --ws websockets-sansio --ws-max-size 1310728 --ws-per-message-deflate false
```

Configure this endpoint in an AI/MCP client that supports Streamable HTTP:

```text
http://127.0.0.1:8766/mcp
```

For an HTTP client using the `mcpServers` configuration format, use:

```json
{
  "mcpServers": {
    "prs": {
      "type": "http",
      "url": "http://127.0.0.1:8766/mcp"
    }
  }
}
```

Configuration-file formats vary by client, but the service address and transport type are the same. No personal AI-client configuration was modified. A client on another device must be able to reach the service host; `127.0.0.1` refers to the client itself.

Open `http://127.0.0.1:8766/live/` or `/maps/` in a browser. Both pages register an AI-control connection automatically. The AI first calls `ui_sessions`, selects a returned `session_id`, then calls `ui_snapshot` to read the page and available controls. The AI controls this open page; no browser extension is required.

Enable ASGI lifespan and use one worker. Django `runserver` remains for a separate local administration backend only; it does not provide MCP or WebSocket support. Multiple workers would distribute browser connections and tool requests across different processes.

## Tools

| Tool | Operation |
|---|---|
| `system_status` | Probe the actual database, connected pages, and presence of ORS/HF configuration. |
| `system_routes` | Discover Django routes, page destinations, and ASGI paths. |
| `system_request` | Call an existing HTTP endpoint while preserving middleware, validation, and HTTP status. |
| `ui_sessions` | List open PRS pages with an active connection. |
| `ui_snapshot` | Read the current URL, text, state, selectors, values, options, and coordinates. |
| `ui_navigate` | Navigate to `camera` or `maps`, wait for the new page to connect, and return a snapshot. |
| `ui_action` | Click, fill, select, check, send keyboard events, submit, scroll, or wait for state. |
| `vibration_status` | Read vibration support, enable switch, visibility, and user-activation state for a specified camera page. |
| `vibration_test` | Request one short, long, or double vibration and return browser acceptance/rejection and reason. |
| `vibration_stop` | Request one vibration cancellation; the enable switch need not remain on. |
| `speech_status` | Read speech prerequisites, queued/speaking/completed/stopped/error state, and recent request metadata. |
| `speech_voices` | Read device voice IDs, languages, local/remote service, and default markers. |
| `speech_speak` | Submit one controlled text-to-speech request without queuing or replacing a selected voice. |
| `speech_stop` | Request cancellation of current-page speech once. |
| `navigation_start` | Start continuous location-based navigation on a planned route in the specified map tab. |
| `navigation_stop` | Stop navigation and release the location watcher while preserving the route for explicit restart. |
| `navigation_status` | Read navigation state, position/accuracy/timestamp, progress, current/next instructions, and route summary. |
| `database_schema` | Read actual database tables, columns, and Django model structure. |
| `database_query` | Run ORM queries with Django conditions, sorting, and pagination. |
| `database_write` | Create, update, or delete through the ORM and return affected row counts. |
| `database_sql` | Execute one SQL statement with bound parameters; supports reads, writes, and DDL. |

`ui_action` can operate only controls that are visible and enabled on the current page. The unified product page `/maps/` sets the origin through “Use current location”, selects the destination on the map, and then plans the route. It does not show the NUS example or manual coordinate input; hidden coordinate fields cannot be filled with `ui_action`. Agent semantic tools `maps_locate/maps_plan` still use the same map controller and support actual location, an existing map destination, or explicit coordinates. The standard map and diagnostic pages may retain their own test controls. Existing route service, HF inference conditions, image parameters, and error handling are unchanged.

New backend HTTP routes appear automatically in route discovery. A new page that needs DOM control should load `ai-control.js` and set `data-api-base` to a same-origin relative path from the application root.

## Usage Examples

The following JSON values are tool arguments; replace `session_id` with a value returned by `ui_sessions`.

For navigation, first plan a walking route on `/maps/`, then select that map page's session:

```text
navigation_status({"session_id":"..."})
navigation_start({"session_id":"..."})
navigation_status({"session_id":"..."})
navigation_stop({"session_id":"..."})
```

The three tools are available in both `full` and `outputs`. They reuse `WalkingNavigation` and do not depend on DOM selectors. In `outputs`, users plan routes on the map page; `full` can use `ui_action` on visible map controls. On the unified product page, MCP may click `#locate` to get the origin, select a destination on `#map` using pixel offsets, then click `#plan` or submit `#route-form`; read the snapshot and confirm control state first. The Agent route workflow should prefer `maps_locate/maps_plan`, then call the same `navigation_start`. The three navigation MCP tools still handle only state/start/stop for a planned route.

A successful start returns `operation=start, accepted=true, state=locating`; this does not mean a position has been obtained. The browser may still require user approval for location. Denial, timeout, or unavailable location changes state to `error`; no retry occurs. A duplicate start returns `already_active`; no planned route returns `route_required`; a non-map page returns `available=false, accepted=false, reason=page_unavailable`; a hidden page rejects start. Stop can also cancel a session waiting for location; after success, `active=false`. `stop_failed` means native cancellation was not confirmed; inspect the page and explicitly stop or close it. Disconnection and MCP timeout retain their original error semantics; completion is unknown and the action is not repeated automatically.

`navigation_status` is read-only and makes no location/route request. It returns:

| Field | Meaning |
|---|---|
| `state`, `active`, `reason` | State (`idle/ready/locating/navigating/off_route/arrived/stopped/error`), whether navigation is active, and reason code; on `stop_failed`, release of the native watcher is unconfirmed. |
| `supported`, `visible` | Whether continuous location is available and whether the page is in the foreground. |
| `position` | Latest longitude/latitude, `accuracy_m`, Unix-millisecond `timestamp`, and `age_ms`; `null` before the first fix. |
| `route` | `request_id`, provider/profile, ORS summary, `geometry_distance_m`, start/destination, step count, and attribution. |
| `progress` | `along_m`, `cross_track_m`, `remaining_distance_m`, `remaining_duration_s`, `distance_to_next_m`, `step_index`, `current_step`, and `next_step`. |
| `policy` | `max_accuracy_m=30`, `off_route_m=40`, `arrival_m=15`, `max_fix_age_ms=15000`. |

`progress=null` when accuracy is low or navigation is stopped/errored. During off-route state, only `cross_track_m` is returned and current/next instructions are empty; remaining distance/time are omitted. Arrival releases location automatically while preserving arrival state and the last position. A stopped session may still return its last position; check `active/state` and `age_ms` together rather than treating it as a live fix. Steps retain ORS `type/instruction/way_points/distance_m/duration_s`, with zero-based indexes. Estimated time uses the ORS duration scaled by the remaining geometry; there is no live speed estimate. See the [frontend guide](../frontend/README.md) for accuracy, off-route, and arrival boundaries. The system does not replan, speak, vibrate, or infer road safety automatically.

Navigation position is not written to the database/local storage or application logs; the tool response returns coordinates to the caller through the control WebSocket/MCP. Hiding or leaving the browser page stops navigation; explicitly restart after returning to the foreground. Background/lock-screen navigation is not guaranteed.

Vibration calls must target an open camera page. The user must first click on that page and enable “Vibration test”; the AI can then call these tools. MCP does not enable the switch, fabricate trusted user activation, or call the API repeatedly.

```text
vibration_status({"session_id":"..."})
vibration_test({"session_id":"...","pattern":"double"})
vibration_stop({"session_id":"..."})
```

`pattern` accepts only `short` (200 ms), `long` (800 ms), or `double` (150 ms/pause 100 ms/150 ms); if omitted it defaults to `short`. All three reuse the frontend button's `VibrationTester`; they do not run detection or request camera permission. Status returns `available/supported/enabled/visible/user_activated/request_pending/hardware_verified`; `user_activated` is `null` if the browser does not expose User Activation information. Test/stop also return `operation/accepted/reason`, and test includes `pattern`. `request_pending` records only a request that was accepted and not successfully cancelled; it does not mean the motor is still running. `accepted: true` means only that the browser API accepted the request; `hardware_verified` is always false.

Missing device prerequisites or native API failures return explicit `accepted: false`, independent of localized display text:

| `reason` | Meaning |
|---|---|
| `page_unavailable` | Selected page has no vibration UI; explicitly select or navigate to a camera page. |
| `unsupported` | Browser has no vibration API. |
| `disabled` | User has not enabled the test. |
| `page_hidden` | Test page is not in the foreground. |
| `activation_required` | Browser has not recorded a real user interaction. |
| `native_rejected` | Native API returned false. |
| `native_error` | Native call threw; details are recorded in the browser console. |

Invalid parameters, disconnected sessions, and transport timeout/disconnection remain MCP errors. After a timeout, action completion is unknown; query page state first and do not replay automatically. Stop can send a cancellation independently of the switch, but the browser may still reject it. Android Chrome and similar browsers can be tested; iPhone Safari does not support this API. See [browser limitations](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/vibrate).

See the frontend guide for real HTTP/WS/Chrome integration commands. Tests explicitly replace the native vibration API and do not verify a physical phone motor.

Speech output requires a real click and an enabled switch on the camera page or a map page with speech controls. The shared browser interface is `speechOutput`, exported by `SystemCode/frontend/speech-output.js` (null on other pages): `describe()`, `voiceList()`, `speak(text, options)`, and `stop()`. UI buttons, the browser-control bridge, and the MCP tools below use this instance and do not request microphone/camera permission.

```text
speech_status({"session_id":"..."})
speech_voices({"session_id":"..."})
speech_speak({"session_id":"...","text":"Hello, this is a speech output test.","language":"en-US","rate":1.0,"volume":0.8})
speech_stop({"session_id":"..."})
```

`text` must contain non-whitespace content, may be at most 1,000 Unicode code points, and is preserved without rewriting. On unified assist pages, `language` accepts `zh-CN/en-US/auto` (default `zh-CN`) and `voice_id` may be omitted or set to the fixed `Cherry` backend voice. On standalone SpeechSynthesis pages, `language` accepts only `zh-CN/en-US` (default `zh-CN`); an omitted/empty `voice_id` selects the browser default, otherwise use an exact ID returned by `speech_voices`, up to 1,024 characters. An invalid/ambiguous native ID, query failure, or primary-language mismatch is rejected explicitly; there is no fallback to the default voice. Native voice inventory may load later through `voiceschanged`. For native voices, `local_service` comes from the browser; false indicates a remote service that may use the network. See [voice service property](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice/localService).

On the unified `/mvp/maps/` and `/mvp/maps/test` pages, `speech_voices` reports the fixed `Cherry` backend voice with `zh-CN` and `en-US` support; `speech_speak` accepts `language=zh-CN`, `en-US`, or `auto`. The text is sent over the same-origin `/ws/tts/` connection to the app server, which calls its configured Qwen realtime service and streams mono 24 kHz PCM back to Web Audio. The unified pages do not read browser voice inventories or invoke `speechSynthesis`; `auto` asks the server provider to determine the language from the submitted text. Backend settings, model, voice, and provider region are not request-controlled. Standalone pages keep their browser SpeechSynthesis contract described below.

Successful submission means only that the request was constructed and the selected service accepted the local submission; it does not mean audio has started or completed. Continue reading `state`: `queued/speaking/completed/stopped/error`. For backend pages, `speaking` begins only after PCM is scheduled for playback and `completed` waits for scheduled buffers to end; for standalone pages, native `start/end/error` events update the engine state. `audio_output_verified` is always false; actual audio still depends on device/system volume even when the service reports completion. The interface returns request ID, Unicode length, language, voice ID, rate, volume, and other metadata but does not echo speech text; application logs also omit the text. Native errors return standard codes only, and backend/provider details are reduced to fixed error codes.

Stop cancels only the current request: standalone pages call `cancel()` once, while unified pages close their TTS WebSocket and stop only their scheduled audio buffers. Late events from the old request are isolated. If cancellation throws, the current request remains available for diagnosis and the result is `cancel_failed`. Disabling the switch or hiding/leaving the page cancels current output; returning to the foreground does not resume automatically. An existing request or busy provider returns `busy`; nothing is queued, interrupted, or retried automatically. Other explicit reasons include `page_unavailable/unsupported/disabled/page_hidden/activation_required`, `invalid_text/invalid_parameters/voice_query_failed/voice_unavailable/voice_ambiguous/voice_language_mismatch/native_error`, and backend TTS errors such as `tts_not_configured/tts_timeout/tts_upstream_unavailable/tts_upstream_error`. MCP type/range violations, missing sessions, and transport failures remain tool errors; asynchronous synthesis errors appear in `last_error`. This interface plays on the current browser device, does not create MP3/WAV files, and is not tied to detection results.
[Output and Event Semantics](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis)

```json
{"session_id":"...","page":"maps"}
```

The call above uses `ui_navigate`. After the unified product page initializes, wait for the location button to become enabled, then use page controls to choose a point and plan:

```json
{"session_id":"...","action":"wait","selector":"#locate","state":"enabled","timeout":20}
```

Clicking location requires browser permission. After reading a new snapshot, choose the destination on the map using `#map` `x/y` offsets, then submit the route form. Do not call `fill` on hidden coordinate fields in the product page:

```json
{"session_id":"...","action":"click","selector":"#locate"}
{"session_id":"...","action":"click","selector":"#map","x":160,"y":120}
{"session_id":"...","action":"submit","selector":"#route-form"}
```

Example buttons and fillable coordinate fields are available only on standard/diagnostic map pages where those controls remain visible; they do not apply to the unified product page.

For map selection, `click` accepts `x/y` pixel offsets measured from the element's top-left corner. `scroll` centers a control in the viewport; `check` accepts a boolean; `select/fill/key` accept strings. `key` dispatches a DOM keyboard event and does not perform browser-default typing or system shortcuts. Use `fill` to enter text and `submit` to submit. Non-unique selectors, hidden/disabled controls, and invalid options fail explicitly.

Successful click means the event was dispatched; camera or route requests may still be running. Continue with `wait` or `ui_snapshot` to observe completion; a click response alone does not establish business success. Snapshots contain at most 30,000 text characters and 500 controls; truncation is marked explicitly. Snapshots do not include camera-image pixels.

Use `system_request` to check existing backend endpoints:

```json
{"path":"/api/health/"}
{"path":"/maps/route/","method":"POST","body":{"start":[103.7766,1.2971],"end":[103.7735,1.2991]}}
```

Endpoint paths omit the deployment prefix `/prs`. Default Host/Origin use the page origin from system configuration. Responses preserve the original HTTP status and do not retry or follow redirects; existing authenticated endpoints may be given required headers explicitly.

Use `database_schema` to retrieve model labels and fields before ORM operations:

```json
{"model":"auth.Group","fields":["id","name"],"limit":20}
{"action":"create","model":"auth.Group","values":{"name":"example-group"}}
{"action":"update","model":"auth.Group","filters":{"name":"example-group"},"values":{"name":"renamed-group"}}
{"action":"delete","model":"auth.Group","filters":{"name":"renamed-group"}}
```

Updates/deletes require explicit `filters`; `{}` means match all rows. Foreign keys use the schema's `attname` (for example, `user_id`). Create/update calls run `full_clean` and `save`; the batch rolls back on failure. Deletes follow Django cascade rules. Password fields are not automatically hashed; this is not an account-registration API. There are currently no product business models. ORM tools operate on existing Django models, and SQL can operate on actual tables.

SQL parameters use Django `%s` placeholders; do not concatenate input:

```json
{"sql":"SELECT id, name FROM auth_group WHERE id >= %s ORDER BY id","parameters":[1],"limit":20}
```

SQL and ORM queries return at most 1,000 rows. SQL `limit` constrains returned rows only; it does not limit write volume. SQL adds no permission filtering and can execute INSERT/UPDATE/DELETE/DDL; actual support depends on the database. Database failures return the exception type and necessary validation fields/codes; logs do not include SQL, data values, or credentials.

## Connection and Runtime Boundaries

The browser page must remain open. Navigation preserves the tab's `sessionStorage` ID, and a
new connection replaces the previous connection with the same ID. Browsers may copy
`sessionStorage` when duplicating a tab; if IDs collide, the newest connection wins. A separate
new tab receives its own ID. Closing or refreshing a tab terminates unfinished control requests.
After disconnection, refresh manually; the client does not reconnect automatically. The browser
control channel sends a heartbeat every 15 seconds to prevent idle Nginx connections from closing.

Camera and location access still require browser permission and usable hardware. MCP DOM clicks
cannot grant browser permissions or create a trusted user gesture recognized by the operating
system. The interface does not provide file-picker dialogs, system shortcuts outside the browser,
or control of other websites. The implementation covers the current PRS pages and backend
features; it does not remove these browser and device limitations. Standard tool timeouts,
disconnections, and browser-action failures are returned explicitly. An action may have completed
after a timeout, so inspect the current state before deciding what to do. The service does not
retry automatically, silently switch cameras or inference modes, or replay database writes.

## Deployment and Verification

The production template uses `/prs/mcp` with lifespan enabled. The MCP proxy read timeout is 70
seconds, and the existing media-path budget is retained. The release tool allowlist includes the
new backend modules, dependency files, and frontend bridge module; staging validates dependencies
and the ASGI import. Install the new dependencies from `requirements.txt` on the server explicitly
before deployment; the release script does not modify the runtime environment implicitly.
Deployment verification should separately cover the protocol, browser control, and physical
devices. Passing locally does not establish that the public proxy or external services work.

Read-only protocol verification:

```powershell
.\.venv\Scripts\python.exe -m tools.check_mcp --url http://127.0.0.1:8766/mcp --output .venv/tmp/mcp-readonly.json
```

Adding `--exercise-db` creates, reads, updates, and deletes a uniquely named test Group, then
cleans up its own test row. `--exercise-ui` targets a dedicated test tab and changes the language,
camera, and map controls. Run full Chrome integration from `frontend`; set `PYTHON` to the
backend's `.venv/Scripts/python.exe` and `PRS_PLAYWRIGHT_PATH` to the existing Playwright package:

```powershell
node ../frontend/tools/check_mcp_browser.mjs http://127.0.0.1:8766/live/ .venv/tmp/mcp-browser.json --synthetic-facing
```

Chrome's native simulated camera cannot satisfy `facingMode.exact=environment`. The explicit
`--synthetic-facing` option removes only this condition, which the simulated device does not
support, in the test browser; width and audio constraints remain, and strict camera selection is
unchanged in the real system. Map configuration and routes also use explicit synthetic fixtures.
The check exercises the real DOM, JavaScript, JPEG, WebSocket, MCP HTTP, database, and page
navigation paths; it does not verify a physical camera, GPS, ORS, or GPU.

Before delivery, run Django checks, migration-drift checks, backend and frontend tests, and full
`ruff check .`, then manually review comments and declaration/variable indexes in changed files.
Protocol checks cover `initialize`, `tools/list`, and `tools/call`; the tool list must match the
selected MCP profile. Runtime details are saved as a structured report. Verify the public proxy,
systemd, PostgreSQL, physical cameras/GPS, and ORS/HF upstreams separately in their target
environments; local tests and synthetic fixtures cannot establish those results.
