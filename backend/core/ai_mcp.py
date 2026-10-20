"""Responsibilities: Expose PRS system, browser and database capabilities through MCP.
Implementation: Official MCP SDK tools share the ASGI browser hub and existing Django services.
Related Modules: config.asgi, core.ai_bridge/ai_database;
frontend/ai-control.js/vibration.js/speech-output.js/navigation.js; MCP.md.
Declaration Index: database_call — threaded ORM/errors; browser_call — browser command/errors;
register_tool — explicit registry profile; register_tool.register — selected tool decorator;
system_status — connectivity/configuration; system_routes — route discovery;
system_request — existing HTTP invocation; ui_sessions — tab discovery; ui_snapshot — live DOM;
ui_navigate — page transition verification; ui_action — control dispatch/state observation;
vibration_status — read browser prerequisites; vibration_test — one fixed vibration request;
vibration_stop — one cancellation request;
speech_status — observe engine state; speech_voices — list device voices;
speech_speak — submit one bounded utterance; speech_stop — cancel page speech;
navigation_status — read walking guidance; navigation_start — request GPS watch;
navigation_stop — cancel walking guidance;
database_schema — schema tool; database_query — ORM read; database_write — ORM mutation;
database_sql — explicit raw SQL tool.
Variable Index: logger — operation diagnostics; mcp — protocol/tool registry; mcp_application —
unauthenticated stateless Streamable HTTP app at /mcp, requiring ASGI lifespan startup.
SpeechText/SpeechRate/SpeechVolume/SpeechVoice — SDK-validated text/option schemas.
MCP_PROFILE — explicit full(default)/outputs registration policy; OUTPUT_TOOL_NAMES —
session discovery, device output and navigation allowlist; no database/DOM tools in outputs.
"""

import asyncio
import logging
import os
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import httpx2
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.urls import get_resolver
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from . import ai_database
from .ai_bridge import hub

logger = logging.getLogger(__name__)
MCP_PROFILE = os.environ.get("PRS_MCP_PROFILE", "full")
if MCP_PROFILE not in {"full", "outputs"}:
    raise ValueError("PRS_MCP_PROFILE must be full or outputs")
OUTPUT_TOOL_NAMES = frozenset({
    "ui_sessions", "vibration_status", "vibration_test", "vibration_stop",
    "speech_status", "speech_voices", "speech_speak", "speech_stop",
    "navigation_status", "navigation_start", "navigation_stop",
})
SpeechText = Annotated[str, Field(min_length=1, max_length=1000)]
SpeechRate = Annotated[float, Field(strict=True, ge=0.5, le=2, allow_inf_nan=False)]
SpeechVolume = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
SpeechVoice = Annotated[str, Field(max_length=1024)]
mcp = MCPServer(
    "PRS Device Outputs" if MCP_PROFILE == "outputs" else "PRS System Control",
    version="1.0.0",
    log_level="WARNING",
    instructions=(
        "Discover an explicit camera tab with ui_sessions, then use only vibration/speech "
        "status, voice inventory, one test/speak request or stop. The user must enable the "
        "output switch and interact with the visible page. Do not automatically enable, "
        "replace voices or repeat timed-out actions. Submission/engine completion never "
        "verifies physical vibration or audible output. Navigation tools operate on a maps "
        "tab with an already planned route. navigation_start requests location permission; "
        "read navigation_status for fresh guidance and navigation_stop to release GPS. "
        "Status includes device location. No automatic rerouting or background navigation."
    ) if MCP_PROFILE == "outputs" else (
        "Control PRS through browser and database tools. First call system_status, "
        "system_routes and ui_sessions. Browser tools require an open PRS tab; choose "
        "its session_id explicitly. Snapshot before acting. An action acknowledgement "
        "does not imply an asynchronous camera or route request completed; observe its "
        "status with ui_action wait or ui_snapshot. Never automatically repeat timed-out "
        "writes/actions: their completion may be unknown. Database tools are unrestricted."
        " Vibration tools require an explicit session_id and the camera page. The user must "
        "enable testing and interact with the visible page first. accepted=true is only "
        "browser acknowledgement, never physical vibration verification."
        " Speech tools use the same explicit camera tab and user prerequisites. Read "
        "speech_voices for exact voice IDs, and speech_status for queued/start/end/error "
        "state after submission. Never infer audible output or automatically replace an "
        "unavailable selected voice. Navigation requires a planned route on the maps page. "
        "Use navigation_start/stop/status with that explicit session_id. Start acceptance "
        "does not mean a GPS fix or arrival; inspect state and position age. Status returns "
        "device location. Hidden pages stop navigation; no automatic rerouting."
    ),
)


def register_tool(*, description):
    """Functionality: Register only capabilities selected by the explicit MCP profile.
    Inputs: Tool description, module MCP_PROFILE/OUTPUT_TOOL_NAMES and initialized server.
    Outputs: Decorator preserving the original callable/schema behavior in the full profile.
    Logic: outputs uses an allowlist, so future unrelated tools are excluded by default.
    Constraints: Invalid configuration fails at import; no fallback registry, permission bypass
    or alternative device implementation. Python functions remain available to local code.
    """
    def register(function):
        """Functionality: Apply the official SDK decorator to a selected callable.
        Inputs: function, captured description and explicit registry policy.
        Outputs: SDK-registered original function, or the same unregistered callable.
        Logic: full registers all tools; outputs exposes the device/navigation allowlist.
        Constraints: Does not change arguments, validation or execution/failure semantics.
        """
        if MCP_PROFILE == "full" or function.__name__ in OUTPUT_TOOL_NAMES:
            return mcp.tool(description=description)(function)
        return function

    return register


async def database_call(operation, function, *args):
    """Functionality: Run ORM work outside the event loop and log operation boundaries.
    Inputs: operation name, synchronous function and positional args. Outputs: Function result.
    Logic: Thread-sensitive execution follows Django connection ownership; cleanup surrounds work.
    Constraints: Failures expose their type/validation codes, without row values or SQL text.
    SDK logging uses WARNING so third-party informational request logs do not expose URL queries.
    """
    logger.info("MCP database operation start operation=%s", operation)
    try:
        await sync_to_async(close_old_connections, thread_sensitive=True)()
        result = await sync_to_async(function, thread_sensitive=True)(*args)
        logger.info("MCP database operation complete operation=%s", operation)
        return result
    except Exception as error:
        logger.error(
            "MCP database operation failed operation=%s error_type=%s",
            operation,
            type(error).__name__,
        )
        details = "Inspect the schema, constraints and supplied arguments."
        if isinstance(error, ValidationError) and hasattr(error, "error_dict"):
            codes = {
                field: [item.code or "invalid" for item in items]
                for field, items in error.error_dict.items()
            }
            details = f"Validation fields/codes: {codes}"
        elif isinstance(error, ValueError):
            details = (
                "Invalid arguments; verify model, fields, filters, limits and operation contract."
            )
        raise ToolError(
            f"Database {operation} failed ({type(error).__name__}). {details}"
        ) from error
    finally:
        await sync_to_async(close_old_connections, thread_sensitive=True)()


async def browser_call(session_id, action, arguments=None, timeout=10):
    """Functionality: Convert diagnosed browser failures into actionable MCP tool errors.
    Inputs: session_id, action, arguments and timeout. Outputs: Browser result or ToolError.
    Logic: Issue one hub command; preserve failure details without allowing SDK crash logging.
    Constraints: No retry; exceptions identify unknown completion for timeout/disconnection.
    """
    try:
        return await hub.command(session_id, action, arguments, timeout)
    except (ValueError, TimeoutError, ConnectionError) as error:
        logger.error(
            "MCP browser action failed session=%s action=%s error_type=%s",
            session_id,
            action,
            type(error).__name__,
        )
        raise ToolError(str(error)) from error


@register_tool(
    description="Inspect database availability, connected browser tabs and configured services."
)
async def system_status() -> dict[str, Any]:
    """Functionality: Report control prerequisites and actual database connectivity.
    Inputs: Environment/configuration, hub and default connection. Outputs: Status dictionary.
    Logic: SELECT 1 checks the database; report only presence of ORS/HF credentials.
    Constraints: Configuration presence does not verify ORS or HF authentication/GPU availability.
    """
    database = await database_call("status", ai_database.execute_sql, "SELECT 1", [], 1)
    return {
        "database": database,
        "browsers": hub.sessions(),
        "authentication": "none",
        "transport": "streamable-http",
        "mcp_path": f"{settings.FORCE_SCRIPT_NAME or ''}/mcp",
        "ors_configured": bool(os.environ.get("ORS_API_KEY", "").strip()),
        "hf_configured": bool(os.environ.get("HF_INFERENCE_TOKEN", "").strip()),
        "browser_requires": "Open a PRS camera/maps page in a browser; one ASGI worker",
    }


@register_tool(
    description="List Django URL patterns and the camera/maps destinations for UI navigation."
)
async def system_routes() -> dict[str, Any]:
    """Functionality: Discover the system's current route registry.
    Inputs: Django resolver and deployment prefix. Outputs: Flat route records and UI destinations.
    Logic: Iteratively traverse nested URL resolvers so new API routes appear without duplication.
    Constraints: Patterns describe paths, not authentication, HTTP methods or API input schemas.
    """
    routes = []
    stack = [("", get_resolver().url_patterns)]
    while stack:
        prefix, patterns = stack.pop()
        for pattern in patterns:
            path = prefix + str(pattern.pattern)
            if hasattr(pattern, "url_patterns"):
                stack.append((path, pattern.url_patterns))
            else:
                routes.append({"path": path, "name": pattern.name})
    return {
        "prefix": settings.FORCE_SCRIPT_NAME or "",
        "routes": routes,
        "pages": {"camera": "live/", "maps": "maps/"},
        "asgi": ["mcp", "ws/control/", "ws/echo/", "ws/detect/hf/"],
    }


@register_tool(
    description="Invoke an existing Django HTTP endpoint in-process; returns status and body. "
    "Use unprefixed paths such as /api/health/ or /maps/route/. Existing validation applies."
)
async def system_request(
    path: str,
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Functionality: Make one request through existing Django middleware and HTTP views.
    Inputs: Unprefixed path, method, optional JSON body/headers. Outputs: Status, type and body.
    Logic: ASGITransport calls Django directly; default Origin/Host use the configured page origin.
    Constraints: No network retry/redirect or permission bypass; credentials may be supplied in
    headers explicitly. HTTP error responses retain their original status and response content.
    """
    from config.asgi import django_application

    if not path.startswith("/") or path.startswith("//") or urlsplit(path).scheme:
        raise ToolError("path must be an application path beginning with one slash")
    origin = settings.CAMERA_ALLOWED_ORIGINS[0]
    request_headers = httpx2.Headers({"Origin": origin})
    request_headers.update(headers or {})
    logger.info("MCP API request method=%s path=%s", method, urlsplit(path).path)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=django_application),
            base_url=origin,
            follow_redirects=False,
        ) as client:
            response = await client.request(
                method, path, json=body, headers=request_headers
            )
    except Exception as error:
        logger.error(
            "MCP API request failed method=%s path=%s error_type=%s",
            method,
            urlsplit(path).path,
            type(error).__name__,
        )
        raise ToolError(f"API request failed ({type(error).__name__})") from error
    content_type = response.headers.get("content-type", "")
    payload = response.json() if "application/json" in content_type else response.text
    return {"status": response.status_code, "content_type": content_type, "body": payload}


@register_tool(description="List connected PRS browser tabs; select a session_id for all UI tools.")
async def ui_sessions() -> dict[str, Any]:
    """Functionality: Discover pages eligible for browser control.
    Inputs: Process-local hub. Outputs: Connected page identities.
    Logic: Query transport registrations; do not create or attach a hidden browser.
    Constraints: A disconnected tab must be reopened/refreshed explicitly.
    """
    return {"sessions": hub.sessions()}


@register_tool(
    description="Read a live DOM snapshot: URL, text, status, selectors, values and options."
)
async def ui_snapshot(session_id: str) -> dict[str, Any]:
    """Functionality: Request current observable state from a chosen browser tab.
    Inputs: session_id. Outputs: Browser snapshot or explicit failure.
    Logic: One correlated snapshot command; ten-second acknowledgement budget.
    Constraints: Text is bounded and marked truncated; no camera-frame capture or retry.
    """
    return await browser_call(session_id, "snapshot")


@register_tool(
    description="Navigate a PRS tab to camera or maps and wait for its new page to connect."
)
async def ui_navigate(
    session_id: str, page: Literal["camera", "maps"], timeout: float = 20
) -> dict[str, Any]:
    """Functionality: Navigate an existing tab and verify its replacement page registration.
    Inputs: session_id, page alias, total timeout seconds (0 < timeout <= 60).
    Outputs: Fresh snapshot after navigation; explicit timeout/disconnection error otherwise.
    Logic: Capture the connection generation, acknowledge navigation then observe its successor.
    Constraints: Same sessionStorage tab ID survives navigation; no repeated navigation on failure.
    """
    if not 0 < timeout <= 60:
        raise ToolError("timeout must be greater than 0 and at most 60 seconds")
    previous = hub.connection(session_id)
    try:
        async with asyncio.timeout(timeout):
            await browser_call(session_id, "navigate", {"page": page}, timeout=timeout)
            await hub.wait_page(session_id, previous, timeout)
            return await browser_call(session_id, "snapshot", timeout=timeout)
    except TimeoutError as error:
        logger.error("MCP navigation timeout session=%s page=%s", session_id, page)
        raise ToolError("Navigation timed out; inspect connected pages before retrying") from error


@register_tool(
    description="Operate a DOM control with an exact CSS selector. Actions: click (optional "
    "x/y offsets for map points), fill, select, check, key, submit, scroll, wait. Values are "
    "strings except check's boolean. wait accepts text and state=visible/hidden/enabled; "
    "it observes completion without repeating operations. Synthetic keys do not implement "
    "browser-native typing/default actions; use fill, click or submit for those effects."
)
async def ui_action(
    session_id: str,
    action: Literal["click", "fill", "select", "check", "key", "submit", "scroll", "wait"],
    selector: str,
    value: str | bool | None = None,
    x: float | None = None,
    y: float | None = None,
    text: str | None = None,
    state: Literal["visible", "hidden", "enabled"] = "visible",
    timeout: float = 10,
) -> dict[str, Any]:
    """Functionality: Execute one selected DOM action or bounded state observation.
    Inputs: session/action/selector, value, x/y pixel offsets, wait text/state, timeout (0..59s).
    Outputs: Post-dispatch DOM snapshot; async application completion must be observed separately.
    Logic: Send all explicit arguments to the browser's documented action dispatcher.
    Constraints: Disabled/ambiguous controls fail; browser permissions/trusted-event rules apply.
    """
    if not 0 < timeout <= 59:
        raise ToolError("timeout must be greater than 0 and at most 59 seconds")
    return await browser_call(
        session_id,
        action,
        {
            "selector": selector,
            "value": value,
            "x": x,
            "y": y,
            "text": text,
            "state": state,
            "timeout_ms": timeout * 1000,
        },
        timeout=timeout + 1,
    )


@register_tool(description="Read walking navigation in an explicit maps tab without starting GPS. "
               "Returns state/reason, active, policy, route summary, last device coordinates and "
               "accuracy/timestamp/age, remaining distance/estimated duration and current/next "
               "instructions. Check active/state/age before using guidance; no obstacle detection.")
async def navigation_status(session_id: str) -> dict[str, Any]:
    """Functionality: Read one live snapshot of the shared maps navigation controller.
    Inputs: Explicit session_id. Outputs: Structured navigation state or page_unavailable.
    Logic: One correlated browser query; device coordinates are returned only on this request.
    Constraints: No GPS start, route request, persistence or retry; transport failure is MCP error.
    """
    return await browser_call(session_id, "navigation_status")


@register_tool(description="Start continuous walking navigation on an already planned route in "
               "an explicit visible maps tab. Browser location permission is required. Returns "
               "accepted/reason; acceptance means watch submitted, not location acquired. Read "
               "navigation_status afterwards. No automatic planning, retry, voice or rerouting.")
async def navigation_start(session_id: str) -> dict[str, Any]:
    """Functionality: Start the same GPS/navigation session as the maps start button.
    Inputs: Explicit session_id. Outputs: State and accepted/reason for a single watch request.
    Logic: Dispatch once; browser controller validates route/support/visibility/active session.
    Constraints: Browser owns permission; timeout means unknown completion, never auto-repeat.
    """
    return await browser_call(session_id, "navigation_start")


@register_tool(description="Stop walking navigation and release its GPS watch in an explicit "
               "maps tab. Retains the planned route for an explicit restart. Returns accepted "
               "and state; stop_failed means native cancellation could not be confirmed.")
async def navigation_stop(session_id: str) -> dict[str, Any]:
    """Functionality: Cancel shared walking guidance, including pending location acquisition.
    Inputs: Explicit session_id. Outputs: Structured cancellation acknowledgement.
    Logic: One browser command invalidates callbacks and clears watch/deadline.
    Constraints: No restart, page change, voice cancellation or database mutation.
    """
    return await browser_call(session_id, "navigation_stop")


@register_tool(
    description="Read vibration support, enable switch, visibility and user activation in an "
    "explicit PRS browser session. Open the camera page first. This does not vibrate the device."
)
async def vibration_status(session_id: str) -> dict[str, Any]:
    """Functionality: Read the chosen browser's live vibration prerequisites.
    Inputs: Explicit session_id. Outputs: available/supported/enabled/visible/user_activated,
    request_pending and hardware_verified=false; pages without the UI report page_unavailable.
    Logic: One correlated browser command reads the existing vibration controller.
    Constraints: No native request, enabling, navigation, retry or physical motor observation;
    timeout/disconnection remain MCP errors with unknown command completion.
    """
    return await browser_call(session_id, "vibration_status")


@register_tool(
    description="Request one short (200ms), long (800ms) or double (150/100/150ms) vibration "
    "in the selected camera tab. The user must enable testing and interact with the visible "
    "page first. Returns accepted/reason; browser acceptance does not verify phone hardware. "
    "No automatic retry, switch enabling or browser permission bypass."
)
async def vibration_test(
    session_id: str, pattern: Literal["short", "long", "double"] = "short"
) -> dict[str, Any]:
    """Functionality: Invoke the same fixed-pattern test used by the frontend buttons.
    Inputs: Explicit session_id and pattern, default short. Outputs: State, operation, pattern,
    accepted and machine-readable refusal reason; hardware_verified is always false.
    Logic: Send one command to the existing controller; device refusals are explicit result data,
    while invalid arguments, lost sessions and uncertain transport completion are MCP errors.
    Constraints: Does not create trusted user activation or enable the switch. No arbitrary
    duration, repeated pattern, inference dependency, alternative reminder or automatic retry.
    """
    return await browser_call(session_id, "vibration_test", {"pattern": pattern})


@register_tool(
    description="Send one vibration cancellation request to an explicit PRS camera tab. "
    "Works independently of the enable switch; returns accepted/reason. No hardware verification."
)
async def vibration_stop(session_id: str) -> dict[str, Any]:
    """Functionality: Request cancellation through the existing browser vibration controller.
    Inputs: Explicit session_id. Outputs: State, operation=stop, accepted and refusal reason.
    Logic: One navigator.vibrate(0) request when supported; clear the accepted-request record
    only if the browser accepts cancellation, preserving native rejection/exception diagnosis.
    Constraints: No enabling or visibility/activation bypass for new tests; the browser may reject
    cancellation. Timeout/disconnection indicate uncertain completion and must not trigger retry.
    """
    return await browser_call(session_id, "vibration_stop")


@register_tool(
    description="Observe speech support, user prerequisites and queued/speaking/completed/"
    "stopped/error state in an explicit camera tab. Does not submit speech."
)
async def speech_status(session_id: str) -> dict[str, Any]:
    """Functionality: Read the selected page's actual speech controller and native flags.
    Inputs: session_id. Outputs: Live state, prerequisites, last request metadata/error;
    audio_output_verified=false. Logic: One browser query without native playback.
    Constraints: No text echo, sensor, enabling or retry. Engine completion is not audible proof;
    lost sessions and uncertain transport completion remain explicit MCP errors.
    """
    return await browser_call(session_id, "speech_status")


@register_tool(
    description="List browser speech voices by exact id, language and local/remote service. "
    "Use a returned id explicitly; no playback or automatic voice substitution."
)
async def speech_voices(session_id: str) -> dict[str, Any]:
    """Functionality: Query the device's current voice inventory for explicit selection.
    Inputs: session_id. Outputs: Voice identifiers/names/languages/local_service/default and state.
    Logic: One getVoices call through the shared controller; query errors are structured outcomes.
    Constraints: Empty lists may still be loading; no synthesis, permission grant or retry.
    Remote services may require network access, as reported by the browser's localService field.
    """
    return await browser_call(session_id, "speech_voices")


@register_tool(description="Submit one text utterance (1..1000 Unicode characters) to a selected "
          "visible camera tab after the user enables speech and clicks the page. language is "
          "zh-CN/en-US, rate 0.5..2, volume 0..1. voice_id omitted/null explicitly uses browser "
          "default. A specified missing voice fails. Submission does not mean speech started "
          "or became audible; query speech_status. Busy requests fail instead of queuing.")
async def speech_speak(
    session_id: str,
    text: SpeechText,
    language: Literal["zh-CN", "en-US"] = "zh-CN",
    voice_id: SpeechVoice | None = None,
    rate: SpeechRate = 1.0,
    volume: SpeechVolume = 1.0,
) -> dict[str, Any]:
    """Functionality: Submit one validated text request through the common browser interface.
    Inputs: session_id/text, language(default zh-CN), voice_id(default browser-selected), rate(1),
    volume(1). SDK validates Unicode length, finite numeric ranges and bounded voice identity;
    whitespace-only text is rejected here before dispatch.
    Outputs: Submission acknowledgement, request metadata and current state; no text echo.
    Logic: One correlated speech_speak command; frontend revalidates the same contract and user
    prerequisites, retains one utterance and tracks asynchronous start/end/error events.
    Constraints: No queue, fallback voice, automatic enabling/retry, audio file or sensor access;
    local/remote synthesis is selected by the caller/browser, not by a server TTS service.
    """
    if not text.strip():
        raise ToolError("text must contain a non-whitespace character")
    return await browser_call(session_id, "speech_speak", {
        "text": text, "language": language, "voice_id": voice_id, "rate": rate, "volume": volume,
    })


@register_tool(description="Cancel this selected page's speech output once, independently of its "
          "enable switch. Returns cancellation submission state; does not verify actual sound.")
async def speech_stop(session_id: str) -> dict[str, Any]:
    """Functionality: Cancel current/queued speech through the shared page controller.
    Inputs: session_id. Outputs: Cancellation acknowledgement/state or explicit failure.
    Logic: One native cancel attempt, preserving active request metadata if the call throws.
    Constraints: No enabling or retry; native completion events from old/canceled requests are
    isolated. Cancellation return does not prove physical audio has stopped.
    """
    return await browser_call(session_id, "speech_stop")


@register_tool(
    description="Inspect actual database tables/columns and ORM model/field identifiers."
)
async def database_schema() -> dict[str, Any]:
    """Functionality: Expose configured database structure.
    Inputs: Default database and Django registry. Outputs: Vendor, tables and model fields.
    Logic: Delegate introspection on Django's thread-sensitive connection.
    Constraints: Does not return connection passwords or table data.
    """
    return await database_call("schema", ai_database.database_schema)


@register_tool(
    description="Query an app.Model using Django filters (e.g. id__gte), explicit fields, "
    "ordering and pagination. Read model labels and attname fields from database_schema."
)
async def database_query(
    model: str,
    filters: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    order_by: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Functionality: Return ORM rows and total matching count.
    Inputs: model, optional filters/fields/order_by, limit/offset. Outputs: Paginated result.
    Logic: Delegate validated read; None filters means all rows.
    Constraints: Cap 1000; default primary-key ordering; database exceptions become MCP errors.
    """
    return await database_call(
        "query", ai_database.query_rows, model, filters or {}, fields, order_by, limit, offset
    )


@register_tool(
    description="Create/update/delete ORM rows in one transaction. update/delete require "
    "filters; explicit {} affects all matching rows. Foreign keys use *_id. full_clean/save "
    "run for create/update; delete follows Django cascades. Passwords are not auto-hashed."
)
async def database_write(
    action: Literal["create", "update", "delete"],
    model: str,
    values: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Functionality: Mutate ORM rows with atomic completion and explicit affected counts.
    Inputs: action/model, optional values/filters. Outputs: Mutation result.
    Logic: Delegate model validation and persistence to ai_database.
    Constraints: No retries or implicit authorization checks; values default to an empty dict.
    """
    return await database_call(
        "write", ai_database.write_rows, action, model, values or {}, filters
    )


@register_tool(
    description="Execute one unrestricted SQL statement, including SELECT/INSERT/UPDATE/"
    "DELETE/DDL. Use %s bound placeholders and parameters. limit only caps returned rows."
)
async def database_sql(
    sql: str, parameters: list[Any] | None = None, limit: int = 100
) -> dict[str, Any]:
    """Functionality: Run explicit raw SQL against the same configured application database.
    Inputs: sql, optional parameters list, limit. Outputs: Columns, rows, rowcount, truncation flag.
    Logic: Thread-sensitive atomic execution once; bound parameters default to empty list.
    Constraints: No SQL filtering or retries; statement/backend errors propagate to MCP.
    """
    return await database_call("sql", ai_database.execute_sql, sql, parameters or [], limit)


mcp_application = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
