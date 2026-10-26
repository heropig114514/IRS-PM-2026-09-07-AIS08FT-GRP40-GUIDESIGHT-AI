"""Responsibilities: Run bounded, skill-driven Qwen conversations through real MCP.
Implementation: Catalog intent routing before MCP, mapped-skill enforcement, registry discovery,
skill-declared dependencies/limits/result-count and confirmation boundaries;
one active task per tab,
complete-turn memory, correlated visible events,
no automatic retries; tasks survive page handoffs until completion/cancellation/deadline.
Related Modules: transport, provider, intents, selection, skills, tools; frontend/agent-client.js.
Declaration Index: Conversation; Conversation.emit; Conversation.subscribe; Conversation.submit;
Conversation.run; Conversation.execute; Conversation.cancel; Conversation.clear;
Conversation.task_finished; append_failure_results; tool_exception; tool_failure;
observed_failure; failure_reply; reason_code;
prune; public_error; history_context; selection_reply.
Variable Index: logger; conversations — bounded process-local registry;
REASON_CODES/REASON_ALIASES — accepted failure categories and browser aliases;
ACTION_LABELS/REASON_LABELS — localized safe failure presentation;
READ_ONLY_TOOLS — calls that cannot dispatch mutations;
MAX_TURNS/MAX_CALLS/MAX_TOOLS/TURN_SECONDS/TTL_SECONDS/MAX_CONVERSATIONS — explicit limits.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import partial

from mcp.client import Client

from .intents import classify, general_answer, reply_language_instruction, static_reply
from .provider import AgentError, completion, transcribe
from .selection import selected_index
from .skills import fingerprint, instructions, tool_policy
from .tools import create_server

logger = logging.getLogger(__name__)
MAX_TURNS, MAX_CALLS, MAX_TOOLS = 8, 12, 30
TURN_SECONDS, TTL_SECONDS, MAX_CONVERSATIONS = 180, 1800, 16
conversations = {}
REASON_CODES = {
    "route_required", "permission_denied", "position_unavailable", "position_timeout",
    "position_stale", "accuracy_insufficient", "off_route", "invalid_position",
    "position_error", "watch_failed", "stop_failed", "page_hidden", "already_active",
    "activation_required", "disabled", "unsupported", "busy", "map_not_ready",
    "unknown_pattern", "voice_query_failed", "voice_unavailable", "voice_ambiguous",
    "voice_language_mismatch", "local_voice_required", "invalid_text", "invalid_parameters",
    "native_rejected", "native_error", "cancel_failed", "user_activation_required",
    "camera_not_found",
    "camera_permission_denied", "camera_security_denied", "camera_busy",
    "camera_interrupted", "camera_constraint_unavailable", "camera_failed",
    "camera_panel_hidden", "page_unavailable", "route_unavailable", "operation_timeout",
    "operation_failed", "action_declined",
}
REASON_ALIASES = {
    "not_found": "route_unavailable",
    "NotFoundError": "camera_not_found",
    "OverconstrainedError": "camera_constraint_unavailable",
    "NotAllowedError": "camera_permission_denied",
    "SecurityError": "camera_security_denied",
    "NotReadableError": "camera_busy",
    "AbortError": "camera_interrupted",
}
READ_ONLY_TOOLS = {
    "discover_tools", "read_skill", "ui_snapshot", "ui_sessions", "system_status",
    "system_routes", "maps_status", "navigation_status", "agent_navigation_status", "camera_status",
    "speech_status", "speech_voices", "vibration_status", "places_categories",
    "places_search", "places_nearby", "database_schema", "database_query",
}
ACTION_LABELS = {
    "camera_start": ("the camera", "摄像头"),
    "camera_status": ("the camera status", "摄像头状态"),
    "camera_stop": ("the camera", "摄像头"),
    "ui_navigate": ("the requested page", "请求的页面"),
    "maps_locate": ("the device location", "设备位置"),
    "maps_plan": ("the route", "路线"),
    "navigation_start": ("walking navigation", "步行导航"),
    "navigation_stop": ("walking navigation", "步行导航"),
    "speech_speak": ("speech output", "语音播报"),
    "vibration_test": ("the vibration request", "震动请求"),
}
REASON_LABELS = {
    "camera_not_found": (
        "No matching camera was found on this device.",
        "此设备上没有找到符合条件的摄像头。",
    ),
    "camera_failed": (
        "The browser could not start the camera for an unspecified reason.",
        "浏览器无法启动摄像头，但没有提供具体原因。",
    ),
    "camera_constraint_unavailable": (
        "The requested camera selection is unavailable.",
        "请求的摄像头选项不可用。",
    ),
    "camera_permission_denied": (
        "The browser denied camera permission.",
        "浏览器拒绝了摄像头权限。",
    ),
    "camera_security_denied": (
        "The browser security context does not allow camera access.",
        "当前浏览器安全环境不允许访问摄像头。",
    ),
    "camera_busy": (
        "The camera could not be read; it may be unavailable or in use.",
        "无法读取摄像头；设备可能不可用或正在被占用。",
    ),
    "camera_interrupted": (
        "The camera operation was interrupted.", "摄像头操作被中断。",
    ),
    "camera_panel_hidden": (
        "The camera panel is hidden by the current page selection.",
        "当前页面选择隐藏了摄像头区域。",
    ),
    "page_unavailable": (
        "This page does not provide the requested control.", "当前页面没有提供所请求的功能。",
    ),
    "route_unavailable": (
        "The requested page or route is not available in this app.",
        "此应用中没有提供请求的页面或路由。",
    ),
    "route_required": (
        "A route must be planned before navigation can start.", "需要先规划路线才能开始导航。",
    ),
    "permission_denied": (
        "The browser denied the required device permission.", "浏览器拒绝了所需的设备权限。",
    ),
    "position_unavailable": (
        "The device did not provide a location.", "设备没有提供位置信息。",
    ),
    "position_timeout": ("The location request timed out.", "获取位置超时。"),
    "position_stale": (
        "No fresh location fix arrived before the navigation deadline.",
        "导航等待期间没有收到新的有效位置。",
    ),
    "accuracy_insufficient": (
        "The current location accuracy is too low for reliable guidance.",
        "当前位置精度不足，无法提供可靠导航。",
    ),
    "off_route": (
        "The current location is outside the planned route tolerance.",
        "当前位置偏离规划路线范围。",
    ),
    "invalid_position": (
        "The device returned invalid location data.", "设备返回的位置数据无效。",
    ),
    "position_error": (
        "The browser could not obtain a device location.", "浏览器无法获取设备位置。",
    ),
    "watch_failed": (
        "The browser could not start continuous location updates.", "浏览器无法启动持续定位。",
    ),
    "stop_failed": (
        "The application could not confirm that navigation stopped.", "应用无法确认导航已停止。",
    ),
    "page_hidden": ("The page is not in the foreground.", "页面当前不在前台。"),
    "activation_required": (
        "This output requires a direct user activation in the page.",
        "此输出需要用户直接在页面中操作以启用。",
    ),
    "already_active": (
        "The requested operation is already active.", "所请求的操作已经处于运行状态。",
    ),
    "disabled": (
        "The required output is disabled in the current page settings.",
        "当前页面设置中未启用所需输出。",
    ),
    "unsupported": (
        "This browser does not support the requested operation.", "当前浏览器不支持所请求的操作。",
    ),
    "busy": ("Another operation is already in progress.", "另一个操作仍在进行中。"),
    "map_not_ready": (
        "The map is not ready to accept this request.", "地图尚未准备好，无法接受此请求。",
    ),
    "unknown_pattern": (
        "The requested vibration pattern is not supported.", "不支持所请求的震动模式。",
    ),
    "voice_query_failed": (
        "The browser could not read its available voice list.", "浏览器无法读取可用语音列表。",
    ),
    "voice_unavailable": ("The selected voice is not available.", "所选语音不可用。"),
    "voice_ambiguous": ("The selected voice identifier is not unique.", "所选语音标识不唯一。"),
    "voice_language_mismatch": (
        "The selected voice does not match the requested language.", "所选语音与请求的语言不匹配。",
    ),
    "local_voice_required": (
        "A local voice must be selected on this page.", "此页面需要明确选择本地语音。",
    ),
    "invalid_text": (
        "The speech text is empty or exceeds its length limit.", "朗读文本为空或超过长度限制。",
    ),
    "invalid_parameters": (
        "The speech settings are outside their supported ranges.", "语音设置超出支持范围。",
    ),
    "native_rejected": (
        "The browser rejected the native output request.", "浏览器拒绝了原生输出请求。",
    ),
    "native_error": (
        "The browser reported an error while requesting native output.",
        "浏览器请求原生输出时报告错误。",
    ),
    "cancel_failed": (
        "The browser could not confirm cancellation.", "浏览器无法确认已取消输出。",
    ),
    "operation_timeout": (
        "The request timed out, so its final state could not be confirmed.",
        "请求超时，因此无法确认最终状态。",
    ),
    "operation_failed": (
        "The application reported a failure but did not identify its cause.",
        "应用报告操作失败，但没有说明具体原因。",
    ),
    "action_declined": (
        "The application did not accept the requested action.", "应用未接受所请求的操作。",
    ),
}


def reason_code(value, default="operation_failed"):
    """Functionality: Restrict failure categories to stable, user-safe reason codes.
    Inputs: optional value returned by a semantic tool and a known default category.
    Outputs: allowlisted reason code. Logic: map browser exception names then enforce the set.
    Constraints: arbitrary provider text and raw exception messages never enter UI explanations.
    """
    candidate = REASON_ALIASES.get(value, value) if isinstance(value, str) else default
    return candidate if candidate in REASON_CODES else default


def tool_failure(name, result):
    """Functionality: Normalize an MCP error without exposing its raw payload to the UI/model.
    Inputs: tool name and official MCP CallToolResult. Outputs: bounded failure evidence.
    Logic: recognize only explicit JSON error codes and timeout markers; unknown details remain
    unknown. Constraints: never log or return provider exception strings or credentials; a
    failed mutation remains unknown unless an explicit not_found code proves failure.
    """
    texts = [getattr(block, "text", "") for block in (getattr(result, "content", None) or [])]
    raw = "\n".join(text for text in texts if isinstance(text, str))[:2000]
    payload = None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        pass
    code = payload.get("code") if isinstance(payload, dict) else None
    if code is None and re.search(r'"code"\s*:\s*"not_found"', raw):
        code = "not_found"
    if code == "not_found":
        reason = "route_unavailable"
    elif isinstance(code, str) and (code in REASON_CODES or code in REASON_ALIASES):
        reason = reason_code(code)
    elif "timeout" in raw.lower() or "timed out" in raw.lower():
        reason = "operation_timeout"
    else:
        reason = "operation_failed"
    completion = "failed" if reason == "route_unavailable" else (
        "not_started" if name in READ_ONLY_TOOLS else "unknown"
    )
    return {
        "outcome": "failed", "tool": name, "reason": reason,
        "completion": completion,
    }


def tool_exception(name, error):
    """Functionality: Normalize a raised MCP invocation failure at the dispatch boundary.
    Inputs: tool name and caught exception. Outputs: bounded failure evidence.
    Logic: classify timeouts separately and treat mutation completion as unknown; read-only calls
    are known not to have dispatched an application mutation. Constraints: exception text and
    provider details are never returned or logged.
    """
    reason = "operation_timeout" if isinstance(error, TimeoutError) else "operation_failed"
    completion = "not_started" if name in READ_ONLY_TOOLS else "unknown"
    return {"outcome": "failed", "tool": name, "reason": reason, "completion": completion}


def append_failure_results(turn, calls, call_index, call_id, failure):
    """Functionality: Pair a failed model tool batch with safe results for every call ID.
    Inputs: current turn, assistant calls, failed index/ID, and normalized failure evidence.
    Outputs: appended tool messages. Logic: preserve the actual failure and mark later batched
    calls as not executed so the next model request remains protocol-valid.
    Constraints: never dispatch pending calls or include their arguments in the result.
    """
    turn.append({
        "role": "tool", "tool_call_id": call_id,
        "content": json.dumps(failure, ensure_ascii=False),
    })
    for pending in calls[call_index + 1:]:
        pending_id = pending.get("id") if isinstance(pending, dict) else None
        if pending_id:
            turn.append({
                "role": "tool", "tool_call_id": pending_id,
                "content": json.dumps({
                    "outcome": "not_executed", "reason": "previous_action_failed",
                }),
            })


def observed_failure(name, observed):
    """Functionality: Detect explicit negative outcomes from structured semantic tools.
    Inputs: tool name and structured result dictionary. Outputs: normalized failure or None.
    Logic: unavailable, declined, and error/failed states stop subsequent actions and carry only
    stable fields needed to explain the observation. Constraints: never infer failure from a
    missing field, false verification flag, empty result list, or nested optional capability.
    """
    if not isinstance(observed, dict):
        return None
    if observed.get("available") is False:
        outcome, completion = "unavailable", "not_started"
        default_reason = "page_unavailable"
    elif observed.get("accepted") is False:
        outcome, completion = "declined", "not_accepted"
        default_reason = "action_declined"
    elif isinstance(observed.get("state"), str) and observed["state"] in {"error", "failed"}:
        outcome, completion = "failed", "failed"
        default_reason = "operation_failed"
    else:
        return None
    failure = {
        "outcome": outcome, "tool": name,
        "reason": reason_code(observed.get("reason"), default_reason),
        "completion": completion,
    }
    state = observed.get("state")
    if isinstance(state, str) and state in {
        "error", "failed", "idle", "ready", "locating", "navigating", "stopped",
    }:
        failure["state"] = state
    if type(observed.get("active")) is bool:
        failure["active"] = observed["active"]
    if type(observed.get("frames_received")) is int:
        failure["frames_received"] = observed["frames_received"]
    return failure


def failure_reply(failure, language):
    """Functionality: Render a concise localized explanation if model completion is unavailable.
    Inputs: normalized failure and validated user language code. Outputs: safe user-facing prose.
    Logic: describe the affected operation, allowlisted cause, direct camera gesture requirement and
    whether completion is known.
    Constraints: no raw JSON, invented cause, success claim, retry, or background promise.
    """
    language_index = 0 if language == "en" else 1
    tool = failure.get("tool")
    action = ACTION_LABELS.get(tool, ("the requested operation", "所请求的操作"))[language_index]
    reason = reason_code(failure.get("reason"))
    detail = REASON_LABELS.get(reason, REASON_LABELS["operation_failed"])[language_index]
    if tool == "camera_start" and reason == "user_activation_required":
        detail = (
            "Tap Start environment awareness on this page to request camera access "
            "and begin detection."
            if language_index == 0 else
            "请直接点击本页的“开启环境感知”，以请求摄像头权限并开始检测。"
        )
    completion = failure.get("completion")
    if language_index == 0:
        if completion == "unknown":
            return (f"I couldn't confirm that {action} completed. {detail} Its final state is "
                    "unknown, and I did not retry it; please check the current page status.")
        if completion == "not_started":
            return f"I couldn't check or start {action}. {detail} No action was started."
        if completion == "not_accepted":
            return f"The app did not accept {action}. {detail} No further actions were taken."
        return f"I couldn't complete {action}. {detail} No further actions were taken."
    if completion == "unknown":
        return f"我无法确认{action}是否完成。{detail}最终状态未知，我没有重试；请检查当前页面状态。"
    if completion == "not_started":
        return f"我无法检查或启动{action}。{detail}本次没有启动操作。"
    if completion == "not_accepted":
        return f"应用没有接受{action}。{detail}我已停止后续操作。"
    return f"{action}未能完成。{detail}我已停止后续操作。"


def selection_reply(items, policy, language):
    """Functionality: Render actual candidate labels without model renaming or type substitution.
    Inputs: nonempty tool items, validated Skill presentation policy and routed zh/en language.
    Outputs: localized selection prompt with numbered exact labels.
    Logic: Validate label types, display a configured prefix of results; retain complete tool data
    in history. Constraints: No alias translation, relevance claim, coordinates or auto-selection.
    """
    labels = [item.get(policy["label_field"]) if isinstance(item, dict) else None for item in items]
    if any(not isinstance(label, str) or not 1 <= len(label) <= 500 for label in labels):
        raise AgentError("invalid_tool_result", "候选地点缺少有效名称，未继续操作。")
    return policy["prompt"][language] + "\n" + "\n".join(
        f"{index}. {label}" for index, label in enumerate(labels[:policy["max_display"]], start=1)
    )


def history_context(turns):
    """Functionality: Omit repeatedly reloadable discovery/skill bodies from model history.
    Inputs: complete saved turns; Outputs: new message list with exact action/search evidence.
    Logic: Identify control-tool result IDs from their assistant calls, replace only their
    catalog/workflow prose with explicit loaded metadata. Original stored turns stay untouched.
    Constraints: Every new turn must rediscover and reload skills; candidates, user input,
    device state, failures and mutation results are never summarized or silently discarded.
    """
    result = []
    for turn in turns:
        names = {
            call["id"]: call["function"]["name"]
            for message in turn
            for call in message.get("tool_calls", [])
        }
        for message in turn:
            name = names.get(message.get("tool_call_id"))
            if message["role"] == "tool" and name in {"discover_tools", "read_skill"}:
                data = json.loads(message["content"])
                metadata = {"previously_loaded": name, "refresh_required_this_turn": True}
                if name == "read_skill":
                    metadata["skill"] = data["name"]
                result.append({**message, "content": json.dumps(metadata)})
            else:
                result.append(message)
    return result


def public_error(error):
    """Functionality: Preserve expected errors across MCP task-group exception boundaries.
    Inputs: exception or nested ExceptionGroup; Outputs: safe AgentError.
    Logic: Unwrap single failures; ambiguous/mixed groups stay internal errors.
    Constraints: Never echo external exception strings or disguise a failure as success.
    """
    while isinstance(error, ExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    if isinstance(error, AgentError):
        return error
    if isinstance(error, TimeoutError):
        return AgentError("agent_timeout", "本次处理超时；已发出的操作完成情况未知，未自动重试。")
    return AgentError(
        "agent_internal_error", "执行链路发生错误，请查看页面状态；操作完成情况未知。"
    )


@dataclass
class Conversation:
    """Functionality: Own one browser-bound conversation and its task lifecycle.
    Logic: Completed turns keep tool-call groups intact; subscribers receive replayable events.
    State: session_id/token bind ownership; turns/events retain bounded dialogue/evidence;
    subscribers holds socket queues; request_ids deduplicates submissions; task owns current work;
    sequence orders events and touched timestamps idle eviction; selection retains displayed
    candidates and explicit confirmation until planning succeeds, a new search replaces it or clear.
    Constraints: In memory only, one ASGI worker; no audio persistence or model-selected tabs.
    """

    session_id: str
    token: str
    turns: list = field(default_factory=list)
    events: list = field(default_factory=list)
    subscribers: set = field(default_factory=set)
    request_ids: list = field(default_factory=list)
    task: asyncio.Task | None = None
    sequence: int = 0
    touched: float = field(default_factory=time.monotonic)
    selection: dict | None = None

    def emit(self, kind, **data):
        """Functionality: Publish ordered user-facing evidence. Inputs: kind and safe data.
        Outputs: event stored/replayed and queued; Logic: monotonically increasing sequence.
        Constraints: 160 events retained; tool arguments are visible only to the owning tab,
        never logged; no raw audio, credentials or model reasoning emitted.
        """
        self.sequence += 1
        self.touched = time.monotonic()
        event = {"type": kind, "seq": self.sequence, **data}
        self.events.append(event)
        self.events = self.events[-160:]
        for queue in self.subscribers:
            queue.put_nowait(event)

    def subscribe(self):
        """Functionality: Attach a fresh page socket. Inputs: self state; Outputs: event queue.
        Logic: Replay bounded history atomically on the event loop before new events.
        Constraints: Transport removes queue on disconnect; no browser localStorage of dialogue.
        """
        queue = asyncio.Queue()
        queue.put_nowait(
            {
                "type": "ready",
                "busy": bool(self.task and not self.task.done()),
                "events": list(self.events),
            }
        )
        self.subscribers.add(queue)
        return queue

    def submit(self, request_id, text=None, audio=None):
        """Functionality: Admit one validated user turn. Inputs: UUID request_id, text or WAV.
        Outputs: background task or AgentError; Logic: reject busy/duplicate inputs before work.
        Constraints: Dedup last 100 submissions, no queuing or automatic replay.
        """
        if request_id in self.request_ids:
            raise AgentError("duplicate_request", "此请求已提交，不会重复执行。")
        if self.task and not self.task.done():
            raise AgentError("agent_busy", "当前指令仍在执行，请等待或取消。")
        self.request_ids = (self.request_ids + [request_id])[-100:]
        self.task = asyncio.create_task(self.run(request_id, text, audio))
        self.task.add_done_callback(partial(self.task_finished, request_id))

    def task_finished(self, request_id, task):
        """Functionality: Finalize a task cancelled before its coroutine entered run().
        Inputs: request_id and completed task; Outputs: cancelled/idle events when needed.
        Logic: run() consumes cancellation after entry; only never-entered tasks remain cancelled.
        Constraints: Does not reset a newer task, schedule devices or replay input.
        """
        if task is self.task and task.cancelled():
            self.emit("cancelled", request_id=request_id, detail="已取消尚未开始的指令。")
            self.emit("idle", request_id=request_id)

    async def run(self, request_id, text, audio):
        """Functionality: Bound and report one full ASR/agent turn.
        Inputs: request ID, text or audio; Outputs: terminal event and completed-turn memory.
        Logic: Deadline covers ASR, model calls and tools; cancellation halts future scheduling.
        Constraints: Already dispatched browser operations cannot be rolled back; failure memory
        records uncertain completion so a subsequent user turn cannot assume nothing happened.
        Internal cancellation/unexpected-failure memory uses English; UI events retain localized
        text.
        """
        started = time.monotonic()
        logger.info(
            "Agent turn started id=%s session=%s skills=%s",
            request_id,
            self.session_id,
            fingerprint(),
        )
        self.emit("working", request_id=request_id, stage="transcribing" if audio else "thinking")
        try:
            async with asyncio.timeout(TURN_SECONDS):
                if audio:
                    async with asyncio.timeout(40):
                        text = await transcribe(audio)
                self.emit("user", request_id=request_id, text=text)
                await self.execute(text, request_id)
        except asyncio.CancelledError:
            self.emit(
                "cancelled",
                request_id=request_id,
                detail="已取消后续步骤；已发出的操作可能仍在完成，请查看页面状态。",
            )
            self.turns.append(
                [
                    {"role": "user", "content": text or "[voice input]"},
                    {
                        "role": "assistant",
                        "content": (
                            "Execution was cancelled. Dispatched actions have unknown completion; "
                            "inspect actual state before continuing."
                        ),
                    },
                ]
            )
        except (AgentError, TimeoutError, ExceptionGroup) as error:
            diagnostic = public_error(error)
            code, detail = diagnostic.code, diagnostic.detail
            logger.warning("Agent turn failed id=%s code=%s", request_id, code)
            self.emit("error", request_id=request_id, code=code, detail=detail)
            self.turns.append(
                [
                    {"role": "user", "content": text or "[voice input]"},
                    {"role": "assistant", "content": detail},
                ]
            )
        except Exception as error:
            logger.error(
                "Agent unexpected failure id=%s exception_type=%s", request_id, type(error).__name__
            )
            self.emit(
                "error",
                request_id=request_id,
                code="agent_internal_error",
                detail="执行链路发生错误；已发出的操作完成情况未知，请查看页面状态。",
            )
            self.turns.append(
                [
                    {"role": "user", "content": text or "[voice input]"},
                    {
                        "role": "assistant",
                        "content": (
                            "Execution failed unexpectedly. Action completion is unknown; "
                            "inspect actual state before continuing."
                        ),
                    },
                ]
            )
        finally:
            self.turns = self.turns[-MAX_TURNS:]
            self.emit("idle", request_id=request_id)
            logger.info(
                "Agent turn ended id=%s elapsed_ms=%d",
                request_id,
                (time.monotonic() - started) * 1000,
            )

    async def execute(self, text, request_id):
        """Functionality: Route requests before bounded, skill-mapped MCP execution.
        Inputs: text, request ID, complete prior turns; Outputs: reply and one saved turn.
        Logic: Classify against the trusted intent catalog and carry its language estimate into
        response generation; ambiguous/unmatched/help requests use an explicit non-action route.
        System requests discover MCP schemas, then load only
        mapped skills before actions;
        intersect declared tools with the real registry, completed prerequisites and remaining
        per-turn limits before sequential invocation; result-count constraints can end actions
        or explicit confirmation flags require a clarification reply. Candidate-selection
        templates render service labels verbatim, preventing model entity substitution. Feed every
        response back to the model. Intent mappings are JSON; workflows remain in Markdown.
        Pending selections survive turns: unconfirmed choices hide guarded tools; explicit numeric
        or exact-name selections bind planning arguments to returned coordinates. Accepted planning
        releases the guard; failed planning pauses actions. New searches replace pending choices.
        History omits only rediscovered metadata bodies, retaining actual execution evidence.
        Constraints: 12 total model calls (including classification)/30 tools/120000 context
        characters; any failed or declined tool ends the action phase, is returned as bounded
        evidence for a localized explanation, and cannot trigger subsequent mutations.
        """
        turn = [{"role": "user", "content": text}]
        self.emit("working", request_id=request_id, stage="routing")
        async with asyncio.timeout(40):
            decision = await classify(text, self.turns)
        target_skills = decision.skills if decision.route == "system" else set()
        self.emit(
            "intent", request_id=request_id, route=decision.route,
            intents=list(decision.ids), labels=decision.labels,
            skills=sorted(target_skills), language=decision.language,
            unknown_policy=decision.catalog["unknown_policy"],
        )
        logger.info(
            "Agent intent routed id=%s route=%s language=%s intents=%s skills=%s unknown_policy=%s",
            request_id, decision.route, decision.language, ",".join(decision.ids),
            ",".join(sorted(target_skills)),
            decision.catalog["unknown_policy"],
        )
        reply = static_reply(decision)
        if decision.route == "general" and reply is None:
            async with asyncio.timeout(40):
                reply = await general_answer(text, self.turns, decision.language)
        if reply is not None:
            turn.append({"role": "assistant", "content": reply})
            self.turns.append(turn)
            self.emit("reply", request_id=request_id, text=reply)
            return
        history = history_context(self.turns)
        if self.selection:
            index = selected_index(text, self.selection["items"])
            if index is not None:
                self.selection["index"] = index
        async with Client(create_server(self.session_id), read_timeout_seconds=35) as client:
            available = (await client.list_tools()).tools
            schemas = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in available
            ]
            names = {tool.name for tool in available}
            discovered, loaded = False, set()
            allowed = {"discover_tools"}
            completed, requirements, limits, counts = set(), {}, {}, {}
            result_counts, pause_reason, selection_text = {}, None, None
            failed_outcome = None
            used = 0
            for _ in range(MAX_CALLS - 1):
                messages = [{"role": "system", "content": instructions() + (
                    "\nThis turn's validated intent IDs: " + json.dumps(decision.ids)
                    + ". Only these mapped skills may be loaded: "
                    + json.dumps(sorted(target_skills))
                    + ". Load every mapped skill before actions. Complete only the user's "
                    "requested operations; route selection does not authorize unrelated actions."
                    "\n" + reply_language_instruction(decision.language)
                )}, *history, *turn]
                if self.selection:
                    selected = self.selection.get("index")
                    messages.append({"role": "system", "content": (
                        "Candidate selection is enforced by the executor. "
                        + ("The user has not selected a number or exact full candidate name. "
                           "Do not plan/start or substitute a related entity "
                           "for the requested one. "
                           "Ask for a number and show the actual candidate labels. Do not repeat "
                           "the search merely because the user repeats the same target name. "
                           if selected is None else "The user explicitly selected this candidate: "
                           + json.dumps(self.selection["items"][selected], ensure_ascii=False)
                           + ". Plan using exactly its coordinates before starting navigation. ")
                        + "Displayed choices: " + json.dumps(
                            self.selection["items"], ensure_ascii=False,
                        )
                    )})
                if pause_reason:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The workflow executor has ended the action phase for this turn: "
                                + json.dumps(pause_reason)
                                + ". Report observations; no more tools may run in this turn. "
                            "Explain the recorded failure or missing prerequisite without claiming "
                            "success; ask for input only when it is actually missing. "
                            "Do not expose "
                                "raw error payloads, automatically choose, retry or promise "
                                "background work. "
                                + reply_language_instruction(decision.language)
                            ),
                        }
                    )
                if len(json.dumps(messages, ensure_ascii=False)) > 120000:
                    raise AgentError("context_limit", "对话上下文达到上限，请清空对话后重新开始。")
                self.emit("working", request_id=request_id, stage="thinking")
                required = (
                    "discover_tools" if not discovered
                    else "read_skill" if target_skills - loaded else None
                )
                permitted = {
                    name
                    for name in allowed
                    if requirements.get(name, set()).issubset(completed)
                    and counts.get(name, 0) < limits.get(name, MAX_TOOLS)
                }
                if pause_reason:
                    permitted.clear()
                if required:
                    permitted.intersection_update({required})
                if self.selection:
                    guard = self.selection["policy"]
                    blocked = set(guard["guard_tools"])
                    if self.selection.get("index") is not None:
                        blocked.discard(guard["coordinate_tool"])
                    permitted.difference_update(blocked)
                visible_schemas = []
                for schema in schemas:
                    if schema["function"]["name"] not in permitted:
                        continue
                    if schema["function"]["name"] == "read_skill":
                        schema = json.loads(json.dumps(schema))
                        schema["function"]["parameters"]["properties"]["name"]["enum"] = sorted(
                            target_skills - loaded
                        )
                    visible_schemas.append(schema)
                try:
                    async with asyncio.timeout(40):
                        message = await completion(
                            messages, visible_schemas,
                            required_tool=required if not pause_reason else None,
                        )
                except Exception:
                    if failed_outcome is None:
                        raise
                    logger.warning(
                        "Agent failure explanation fallback id=%s tool=%s",
                        request_id, failed_outcome.get("tool"),
                    )
                    reply = failure_reply(failed_outcome, decision.language)
                    turn.append({"role": "assistant", "content": reply})
                    self.turns.append(turn)
                    self.emit("reply", request_id=request_id, text=reply)
                    return
                turn.append(message)
                calls = message.get("tool_calls", [])
                if not calls:
                    if required and not pause_reason:
                        raise AgentError(
                            "discovery_required", "模型未完成工具发现与技能加载，已停止。"
                        )
                    if failed_outcome and not (message.get("content") or "").strip():
                        message["content"] = failure_reply(failed_outcome, decision.language)
                    self.turns.append(turn)
                    self.emit("reply", request_id=request_id, text=message["content"])
                    return
                if failed_outcome is not None:
                    reply = failure_reply(failed_outcome or pause_reason, decision.language)
                    turn.append({"role": "assistant", "content": reply})
                    self.turns.append(turn)
                    self.emit("reply", request_id=request_id, text=reply)
                    return
                for call_index, call in enumerate(calls):
                    used += 1
                    if used > MAX_TOOLS:
                        raise AgentError("tool_limit", "本次操作步骤达到上限，请缩小请求范围。")
                    try:
                        name, arguments = call["function"]["name"], call["function"]["arguments"]
                        call_id = call["id"]
                        arguments = json.loads(arguments)
                        if (
                            name not in names
                            or name not in permitted
                            or (required and name != required)
                            or not isinstance(arguments, dict)
                            or not call_id
                            or (name == "read_skill"
                                and arguments.get("name") not in target_skills - loaded)
                        ):
                            raise ValueError("Invalid tool")
                    except (KeyError, TypeError, ValueError) as error:
                        raise AgentError(
                            "invalid_tool_call", "模型返回了无效工具调用，已停止。"
                        ) from error
                    bound_selection = self.selection and name == self.selection["policy"][
                        "coordinate_tool"
                    ]
                    if bound_selection:
                        chosen = self.selection["items"][self.selection["index"]]
                        parameter = self.selection["policy"]["coordinate_argument"]
                        if (not isinstance(chosen.get("coordinates"), list)
                                or len(chosen["coordinates"]) != 2
                                or arguments.get(parameter) != chosen["coordinates"]):
                            raise AgentError(
                                "candidate_mismatch", "规划坐标与已确认候选不一致，未执行。",
                            )
                    self.emit("tool_start", request_id=request_id, name=name, arguments=arguments)
                    counts[name] = counts.get(name, 0) + 1
                    if counts[name] >= limits.get(name, MAX_TOOLS):
                        permitted.discard(name)
                    logger.info("Agent tool started id=%s name=%s step=%d", request_id, name, used)
                    try:
                        async with asyncio.timeout(35):
                            result = await client.call_tool(name, arguments)
                    except Exception as error:
                        failed_outcome = tool_exception(name, error)
                        pause_reason = failed_outcome
                        permitted.clear()
                        logger.warning(
                            "Agent MCP invocation raised id=%s name=%s reason=%s type=%s",
                            request_id, name, failed_outcome["reason"], type(error).__name__,
                        )
                        append_failure_results(turn, calls, call_index, call_id, failed_outcome)
                        self.emit(
                            "tool_result", request_id=request_id, name=name,
                            result={key: failed_outcome[key] for key in
                                    ("outcome", "reason", "completion")},
                        )
                        break
                    if result.is_error:
                        failed_outcome = tool_failure(name, result)
                        pause_reason = failed_outcome
                        permitted.clear()
                        logger.warning(
                            "Agent MCP failed id=%s name=%s reason=%s",
                            request_id, name, failed_outcome["reason"],
                        )
                        append_failure_results(turn, calls, call_index, call_id, failed_outcome)
                        self.emit(
                            "tool_result", request_id=request_id, name=name,
                            result={key: failed_outcome[key] for key in
                                    ("outcome", "reason", "completion")},
                        )
                        break
                    observed = result.structured_content
                    if not isinstance(observed, dict):
                        failed_outcome = {
                            "outcome": "failed", "tool": name, "reason": "operation_failed",
                            "completion": "not_started" if name in READ_ONLY_TOOLS else "unknown",
                        }
                        pause_reason = failed_outcome
                        permitted.clear()
                        logger.warning(
                            "Agent MCP returned invalid result id=%s name=%s",
                            request_id, name,
                        )
                        append_failure_results(turn, calls, call_index, call_id, failed_outcome)
                        self.emit(
                            "tool_result", request_id=request_id, name=name,
                            result={key: failed_outcome[key] for key in
                                    ("outcome", "reason", "completion")},
                        )
                        break
                    failure = observed_failure(name, observed)
                    if failure:
                        failed_outcome = failure
                        pause_reason = failure
                        permitted.clear()
                    else:
                        completed.add(name)
                    if not failure and bound_selection:
                        if observed.get("accepted") is True:
                            self.selection = None
                        else:
                            pause_reason = {"tool": name, "selected_candidate_action_failed": True}
                            permitted.clear()
                    if not failure and name == "discover_tools":
                        discovered = True
                        allowed.discard("discover_tools")
                        permitted.discard("discover_tools")
                        allowed.add("read_skill")
                        logger.info(
                            "Agent catalog discovered id=%s tools=%d", request_id, len(names)
                        )
                    elif not failure and name == "read_skill":
                        skill = arguments["name"]
                        policy = tool_policy(skill)
                        declared = set(policy["tools"])
                        loaded.add(skill)
                        if loaded == target_skills:
                            allowed.discard("read_skill")
                            permitted.discard("read_skill")
                        allowed.update(declared & names)
                        for tool, dependencies in policy["requires"].items():
                            requirements.setdefault(tool, set()).update(dependencies)
                        for tool, limit in policy["limits"].items():
                            limits[tool] = min(limit, limits.get(tool, MAX_TOOLS))
                        result_counts.update(policy["result_counts"])
                        logger.info(
                            "Agent skill loaded id=%s skill=%s available=%d missing=%d",
                            request_id,
                            skill,
                            len(declared & names),
                            len(declared - names),
                        )
                    if not failure and name in result_counts:
                        rule = result_counts[name]
                        if "selection" in rule:
                            self.selection = None
                        items = observed.get(rule["field"])
                        confirmation = observed.get(rule.get("confirm_if"), False)
                        if not isinstance(items, list):
                            raise AgentError("invalid_tool_result", "工具结果不符合流程数据契约。")
                        if type(confirmation) is not bool:
                            raise AgentError(
                                "invalid_tool_result", "工具确认标记不符合流程数据契约。",
                            )
                        if not rule["min"] <= len(items) <= rule["max"] or confirmation:
                            pause_reason = {
                                "tool": name,
                                "field": rule["field"],
                                "count": len(items),
                                "requires_confirmation": confirmation,
                            }
                            if items and "selection" in rule:
                                selection_text = selection_reply(
                                    items, rule["selection"], decision.language,
                                )
                                self.selection = {
                                    "items": items[:rule["selection"]["max_display"]],
                                    "policy": rule["selection"], "index": None,
                                }
                            permitted.clear()
                            logger.info(
                                "Agent awaiting input id=%s tool=%s result_count=%d",
                                request_id,
                                name,
                                len(items),
                            )
                    turn.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps(observed, ensure_ascii=False),
                        }
                    )
                    summary = {
                        key: observed[key]
                        for key in (
                            "accepted",
                            "reason",
                            "state",
                            "active",
                            "frames_received",
                            "available",
                            "requires_confirmation",
                        )
                        if key in observed
                    }
                    if name == "read_skill":
                        summary["skill"] = observed["name"]
                    for key in ("tools", "categories", "candidates"):
                        if isinstance(observed.get(key), list):
                            summary[key + "_count"] = len(observed[key])
                    self.emit("tool_result", request_id=request_id, name=name, result=summary)
                    logger.info("Agent tool finished id=%s name=%s", request_id, name)
                    if failure:
                        for pending in calls[call_index + 1:]:
                            pending_id = pending.get("id") if isinstance(pending, dict) else None
                            if pending_id:
                                turn.append({
                                    "role": "tool", "tool_call_id": pending_id,
                                    "content": json.dumps({
                                        "outcome": "not_executed",
                                        "reason": "previous_action_failed",
                                    }),
                                })
                        break
                if selection_text is not None:
                    turn.append({"role": "assistant", "content": selection_text})
                    self.turns.append(turn)
                    self.emit("reply", request_id=request_id, text=selection_text)
                    return
            if failed_outcome is not None:
                reply = failure_reply(failed_outcome, decision.language)
                turn.append({"role": "assistant", "content": reply})
                self.turns.append(turn)
                self.emit("reply", request_id=request_id, text=reply)
                return
            raise AgentError("model_step_limit", "本次处理步骤达到上限，请缩小请求范围。")

    def cancel(self):
        """Functionality: Cancel future work. Inputs: active task; Outputs: cancellation request.
        Logic: Task cancellation propagates through client calls; Constraints: no device rollback.
        """
        if self.task and not self.task.done():
            self.task.cancel()

    def clear(self):
        """Functionality: Forget idle conversation. Inputs: self state; Outputs: reset event.
        Logic: Reject active clear; clear dialogue and candidate confirmation together.
        Constraints: no device changes or hidden in-flight work.
        """
        if self.task and not self.task.done():
            raise AgentError("agent_busy", "请先取消或等待当前指令完成。")
        self.turns.clear()
        self.selection = None
        self.events.clear()
        self.emit("cleared")


def prune():
    """Functionality: Evict expired idle conversations on connection admission.
    Inputs: monotonic time/registry; Outputs: in-memory removal.
    Logic: Active tasks and attached pages are retained; Constraints: no persistence or timers.
    """
    now = time.monotonic()
    for key, conversation in list(conversations.items()):
        if (
            not conversation.subscribers
            and now - conversation.touched > TTL_SECONDS
            and (not conversation.task or conversation.task.done())
        ):
            del conversations[key]
