"""Responsibilities: Build a per-conversation, browser-bound MCP tool server.
Implementation: Official SDK in-process transport, semantic adapters and existing UI/output tools;
the model never supplies a session ID. Tool schemas come from the same MCP registry as execution.
Related Modules: core.ai_mcp/ai_bridge; frontend/agent-device.js;
agent skills/places/place_resolution/runtime.
Declaration Index: BrowserTools; BrowserTools.__init__; BrowserTools.browser;
BrowserTools.discover_tools; BrowserTools.places_categories; BrowserTools.places_nearby;
BrowserTools.read_skill; BrowserTools.ui_snapshot; BrowserTools.ui_navigate; BrowserTools.ui_action;
BrowserTools.maps_status; BrowserTools.maps_locate; BrowserTools.maps_plan;
BrowserTools.places_search;
BrowserTools.navigation_status; BrowserTools.navigation_start; BrowserTools.navigation_stop;
BrowserTools.camera_status; BrowserTools.camera_start; BrowserTools.camera_stop;
BrowserTools.speech_status; BrowserTools.speech_voices; BrowserTools.speech_speak;
BrowserTools.speech_stop; BrowserTools.vibration_status; BrowserTools.vibration_test;
BrowserTools.vibration_stop; create_server.
Variable Index: DESCRIPTIONS — tool catalog descriptions; Wait — bounded observation schema.
"""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from core import ai_mcp

from . import poi
from .place_resolution import resolve_places
from .places import search_context
from .provider import AgentError
from .skills import catalog, read_skill, tool_policy

Wait = Annotated[float, Field(ge=0, le=12)]
DESCRIPTIONS = {
    "discover_tools": "Discover the actual bound MCP tool catalog and skill tool scopes before "
                      "executing mapped workflows. Registration is not successful execution.",
    "read_skill": "Load one named workflow from the supplied skill catalog before operating.",
    "ui_snapshot": "Inspect the bound tab's current URL, controls, options and visible state.",
    "ui_navigate": "Focus the embedded camera panel on unified assist pages, or navigate this tab "
                    "to maps/camera on standard pages and verify its resulting state.",
    "ui_action": "Operate an observed application control; no assistant UI or permissions bypass.",
    "maps_status": "Read current map origin/destination, configuration, busy and route state.",
    "maps_locate": "Obtain actual device GPS and set map origin; browser permission required.",
    "maps_plan": "Plan and render ORS walking route using current form or explicit coordinates.",
    "places_search": "Search a specific name/address in the configured query_language from "
                     "read_skill (preserve brand/branch/address); use observed "
                     "GPS as focus. original_text must preserve the user's place wording exactly, "
                     "before any translation/spelling/phonetic correction. Rewritten names require "
                     "confirmation. One invocation per turn. For uncertain pronunciation, "
                     "keep the heard place text in text and provide at most two distinct "
                     "sound-alike pronunciation_queries; all such results require confirmation. "
                     "Default country is deployment scope, not the user's location.",
    "places_categories": "Discover live map-provider POI category IDs/names. "
                         "Never invent IDs or use name search as a nearby category search.",
    "places_nearby": "Find nearby places using category IDs returned by places_categories and "
                     "observed GPS. Defaults to 2000m; returns up to five points by straight-line "
                     "distance, not shortest walking route. Empty results do not prove absence.",
    "navigation_status": "Read actual navigation state; optionally wait up to 12s while locating.",
    "navigation_start": "Start existing route and observe GPS for up to12s; returns actual state.",
    "navigation_stop": "Stop navigation and release GPS; verify actual state.",
    "camera_status": "Read media track, processing mode, errors and decoded frame count.",
    "camera_start": "Start current mode or echo/hf; observe up to12s; permission required.",
    "camera_stop": "Stop camera and release media/processing using the shared controller.",
    "speech_status": "Read speech prerequisites and engine state; never proves audible sound.",
    "speech_voices": "List available browser voices with exact identifiers.",
    "speech_speak": "Speak requested text; user must personally enable output and activate page.",
    "speech_stop": "Cancel current speech on this page.",
    "vibration_status": "Read vibration support, user-enabled state and activation.",
    "vibration_test": "Request one short/long/double vibration; never proves hardware output.",
    "vibration_stop": "Cancel vibration on this page.",
}


class BrowserTools:
    """Functionality: Adapt conversational tools to one verified tab.
    Logic: Immutable session binding prevents model-selected cross-tab calls.
    Constraints: Does not expose database, filesystem, arbitrary HTTP or tool discovery of peers.
    """

    def __init__(self, session_id, server=None):
        """Functionality: Bind tab and discovery source. Inputs: verified session_id, MCP server.
        Outputs: instance; Logic: retain server for actual public SDK listing and per-turn
        category_ids for discovery enforcement. Constraints: no other-tab or private SDK access.
        """
        self.session_id = session_id
        self.server = server
        self.category_ids = set()

    async def discover_tools(self) -> dict[str, Any]:
        """Functionality: Describe the tools really registered in this bound server.
        Inputs: server registry and trusted skill metadata; Outputs: descriptions and available/
        missing workflow tools. Logic: public SDK list_tools is the source; selected schemas are
        supplied by runtime after skill loading rather than duplicated in conversation history.
        Registry-only
        discovery performs no external calls. Constraints: never exposes unrestricted system,
        database or other-tab capabilities; registration does not verify service/device readiness.
        """
        registered = await self.server.list_tools()
        names = {tool.name for tool in registered}
        return {
            "tools": [{"name": tool.name, "description": tool.description} for tool in registered],
            "skills": [{"name": name, "description": description,
                        "tools": [tool for tool in tool_policy(name)["tools"] if tool in names],
                        "missing_tools": [tool for tool in tool_policy(name)["tools"]
                                          if tool not in names]}
                       for name, description in catalog().items()],
            "scope": "this authenticated browser tab", "readiness_verified": False,
        }

    async def browser(self, action, arguments=None, timeout=30):
        """Functionality: Dispatch one browser command. Inputs: action/arguments/timeout.
        Outputs: observed result or MCP error; Logic: existing hub correlation.
        Constraints: No mutation replay; timeouts mean completion is unknown.
        """
        return await ai_mcp.browser_call(self.session_id, action, arguments, timeout)

    def read_skill(self, name: str) -> dict[str, Any]:
        """Functionality: Load workflow/references and relevant non-secret configuration.
        Inputs: catalog name; Outputs: trusted Markdown, plus navigation search context.
        Logic: Trusted loader, then actual configured region; Constraints: no inferred GPS,
        user profile, arbitrary file access or automatic knowledge retrieval from the web.
        """
        result = {"name": name, "instructions": read_skill(name), **tool_policy(name)}
        if name == "navigation":
            result["environment_context"] = search_context()
        return result

    async def ui_snapshot(self) -> dict[str, Any]:
        """Functionality: Observe UI. Inputs: bound tab; Outputs: current snapshot.
        Logic: Existing MCP adapter; Constraints: browser text is untrusted data.
        """
        return await ai_mcp.ui_snapshot(self.session_id)

    async def ui_navigate(self, page: Literal["camera", "maps"]) -> dict[str, Any]:
        """Functionality: Focus an embedded camera panel or change page. Inputs: fixed page alias.
        Outputs: new snapshot or explicit unavailable result. Logic: Same-page focus skips handoff;
        standard routes use the existing verified handoff. Constraints: leaving a page stops work.
        """
        return await ai_mcp.ui_navigate(self.session_id, page)

    async def ui_action(
        self,
        action: Literal["click", "fill", "select", "check", "submit", "wait"],
        selector: str,
        value: str | bool | None = None,
        state: Literal["visible", "hidden", "enabled"] = "visible",
        text: str = "",
        timeout: Annotated[float, Field(gt=0, le=30)] = 20,
    ) -> dict[str, Any]:
        """Functionality: Operate requested observed controls. Inputs: action/selector/value,
        state/text/timeout; Outputs: actual snapshot. Logic: existing control validation plus
        agent-only protected-control check; Constraints: cannot operate assistant/output switches.
        """
        return await self.browser(
            "agent_ui_action",
            {
                "action": action,
                "selector": selector,
                "value": value,
                "state": state,
                "text": text,
                "timeout_ms": timeout * 1000,
            },
            timeout + 2,
        )

    async def maps_status(self) -> dict[str, Any]:
        """Functionality: Read map state. Inputs: bound tab; Outputs: form/route state.
        Logic: Shared map adapter; Constraints: no location request.
        """
        return await self.browser("maps_status")

    async def maps_locate(self) -> dict[str, Any]:
        """Functionality: Acquire origin. Inputs: bound tab/browser permission; Outputs: GPS state.
        Logic: Shared locate controller; Constraints: no samples, permission bypass or retries.
        """
        return await self.browser("maps_locate", timeout=16)

    async def maps_plan(
        self, destination: list[float] | None = None, origin: list[float] | None = None
    ) -> dict[str, Any]:
        """Functionality: Plan visible route. Inputs: optional destination/origin longitude-first.
        Outputs: rendered route state; Logic: shared form/controller; Constraints: no GPS invention.
        """
        return await self.browser("maps_plan", {"destination": destination, "origin": origin})

    async def places_search(
        self, text: str, original_text: str, focus: list[float] | None = None,
        country: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None,
        pronunciation_queries: Annotated[list[str], Field(max_length=2)] | None = None,
    ) -> dict[str, Any]:
        """Functionality: Search destinations within an explicit geographic scope.
        Inputs: search text, exact user original_text, observed focus, optional country and
        pronunciation_queries.
        Outputs: real candidates, applied queries/scope and phonetic-mode confirmation marker.
        Logic: Normal lookup or at most two explicit sound-alike hypotheses against the same ORS
        service. Constraints: no GPS inference, alias-certainty claim or automatic selection.
        """
        return await resolve_places(text, original_text, focus, country, pronunciation_queries)

    async def places_categories(self) -> dict[str, Any]:
        """Functionality: Discover current provider categories before nearby queries.
        Inputs: configured provider; Outputs: actual catalog. Logic: store only verified IDs
        for this turn's MCP server. Constraints: no fixed category map or invented identifiers.
        """
        result = await poi.categories()
        self.category_ids = {item["id"] for item in result["categories"]}
        return result

    async def places_nearby(
        self, focus: list[float], category_ids: list[int],
        radius_m: Annotated[int, Field(ge=100, le=2000)] = 2000,
    ) -> dict[str, Any]:
        """Functionality: Execute one category query with previously discovered identifiers.
        Inputs: observed/explicit focus, selected category_ids, radius_m; Outputs: real candidates.
        Logic: require discovery membership before the external request. Constraints: empty
        results remain empty; never substitute a name query or widen radius automatically.
        """
        if not category_ids or not set(category_ids).issubset(self.category_ids):
            raise AgentError("category_not_discovered", "请先读取服务类别目录并使用返回的类别。")
        return await poi.nearby(focus, category_ids, radius_m)

    async def navigation_status(self, wait_seconds: Wait = 0) -> dict[str, Any]:
        """Functionality: Observe navigation. Inputs: wait_seconds; Outputs: actual state.
        Logic: Browser waits only while locating; Constraints: read-only, no retry of start.
        """
        return await self.browser("agent_navigation_status", {"wait_seconds": wait_seconds})

    async def navigation_start(self) -> dict[str, Any]:
        """Functionality: Start existing route and observe actual acquisition.
        Inputs: bound tab; Outputs: acknowledgement, navigation and optional speech adapter state.
        Logic: Dispatch once, then read for up to12s while locating.
        Constraints: Does not retry start, alter GPS policy, enable speech or fabricate a fix.
        """
        result = await ai_mcp.navigation_start(self.session_id)
        if result.get("accepted"):
            result.update(await self.navigation_status(wait_seconds=12))
        return {
            **result,
            "vibration_guidance": False,
            "verified": result.get("state") == "navigating",
        }

    async def navigation_stop(self) -> dict[str, Any]:
        """Functionality: Stop navigation. Inputs: bound tab; Outputs: cancellation state.
        Logic: Existing MCP tool; Constraints: cancellation failure remains explicit.
        """
        return await ai_mcp.navigation_stop(self.session_id)

    async def camera_status(self, wait_seconds: Wait = 0) -> dict[str, Any]:
        """Functionality: Inspect media. Inputs: wait_seconds; Outputs: track/frame state.
        Logic: Wait only during startup; Constraints: never starts camera or changes mode.
        """
        return await self.browser("camera_status", {"wait_seconds": wait_seconds})

    async def camera_start(self, processing: Literal["echo", "hf"] | None = None) -> dict[str, Any]:
        """Functionality: Start camera and observe actual track/frame state.
        Inputs: optional processing mode; Outputs: acknowledgement/observed state.
        Logic: Shared CameraApp once, then read up to12s during startup.
        Constraints: No mode fallback, restart or assertion that permission was granted.
        """
        result = await self.browser("camera_start", {"processing": processing})
        if result.get("accepted"):
            result.update(await self.camera_status(wait_seconds=12))
        return {**result, "verified": result.get("state") == "streaming"}

    async def camera_stop(self) -> dict[str, Any]:
        """Functionality: Release camera. Inputs: bound tab; Outputs: observed state.
        Logic: shared CameraApp.stop; Constraints: late permission callbacks stay cancelled.
        """
        return await self.browser("camera_stop")

    async def speech_status(self) -> dict[str, Any]:
        """Functionality: Read speech state. Inputs: tab; Outputs: engine/prerequisites.
        Logic: existing tool; Constraints: not hardware verification.
        """
        return await ai_mcp.speech_status(self.session_id)

    async def speech_voices(self) -> dict[str, Any]:
        """Functionality: Enumerate voices. Inputs: tab; Outputs: identifiers.
        Logic: existing tool; Constraints: no voice substitution.
        """
        return await ai_mcp.speech_voices(self.session_id)

    async def speech_speak(
        self,
        text: ai_mcp.SpeechText,
        language: Literal["zh-CN", "en-US"] = "zh-CN",
        voice_id: ai_mcp.SpeechVoice = "",
        rate: ai_mcp.SpeechRate = 1.0,
        volume: ai_mcp.SpeechVolume = 1.0,
    ) -> dict[str, Any]:
        """Functionality: Submit speech. Inputs: text/language/voice_id/rate/volume.
        Outputs: acknowledgement; Logic: existing tool; Constraints: user-enabled output required.
        """
        return await ai_mcp.speech_speak(self.session_id, text, language, voice_id, rate, volume)

    async def speech_stop(self) -> dict[str, Any]:
        """Functionality: Cancel speech. Inputs: tab; Outputs: state.
        Logic: existing tool; Constraints: errors remain visible.
        """
        return await ai_mcp.speech_stop(self.session_id)

    async def vibration_status(self) -> dict[str, Any]:
        """Functionality: Read vibration support. Inputs: tab; Outputs: state.
        Logic: existing tool; Constraints: no activation changes.
        """
        return await ai_mcp.vibration_status(self.session_id)

    async def vibration_test(
        self, pattern: Literal["short", "long", "double"] = "short"
    ) -> dict[str, Any]:
        """Functionality: Request vibration. Inputs: pattern; Outputs: acknowledgement.
        Logic: existing tool; Constraints: no automatic enable, retry or hardware claim.
        """
        return await ai_mcp.vibration_test(self.session_id, pattern)

    async def vibration_stop(self) -> dict[str, Any]:
        """Functionality: Cancel vibration. Inputs: tab; Outputs: state.
        Logic: existing tool; Constraints: native rejection preserved.
        """
        return await ai_mcp.vibration_stop(self.session_id)


def create_server(session_id):
    """Functionality: Create a scoped MCP server for official in-memory client transport.
    Inputs: verified session ID; Outputs: MCPServer instance with explicit tools.
    Logic: Register bound methods; runtime discovers schemas instead of duplicating them.
    Constraints: Not mounted on a public HTTP path; does not change full/outputs profiles.
    """
    server = MCPServer("PRS Conversation Tools", log_level="WARNING")
    adapter = BrowserTools(session_id, server)
    for name, description in DESCRIPTIONS.items():
        server.add_tool(
            getattr(adapter, name), name=name, description=description, structured_output=True
        )
    return server
