"""Responsibilities: Run bounded, skill-driven Qwen conversations through real MCP.
Implementation: Catalog intent routing before MCP, mapped-skill enforcement, registry discovery,
skill-declared dependencies/limits/result-count and confirmation boundaries;
one active task per tab,
complete-turn memory, correlated visible events,
no automatic retries; tasks survive page handoffs until completion/cancellation/deadline.
Related Modules: transport, provider, intents, selection, skills, tools; frontend/agent-client.js.
Declaration Index: Conversation; Conversation.emit; Conversation.subscribe; Conversation.submit;
Conversation.run; Conversation.execute; Conversation.cancel; Conversation.clear;
Conversation.task_finished;
prune; public_error; history_context; selection_reply.
Variable Index: logger; conversations — bounded process-local registry; MAX_TURNS/MAX_CALLS/
MAX_TOOLS/TURN_SECONDS/TTL_SECONDS/MAX_CONVERSATIONS — explicit memory/execution limits.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from functools import partial

from mcp.client import Client

from .intents import classify, general_answer, static_reply
from .provider import AgentError, completion, transcribe
from .selection import selected_index
from .skills import fingerprint, instructions, tool_policy
from .tools import create_server

logger = logging.getLogger(__name__)
MAX_TURNS, MAX_CALLS, MAX_TOOLS = 8, 12, 30
TURN_SECONDS, TTL_SECONDS, MAX_CONVERSATIONS = 180, 1800, 16
conversations = {}


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
        Logic: Classify against the trusted intent catalog; ambiguous/unmatched/help requests
        use an explicit non-action route. System requests discover MCP schemas, then load only
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
        characters; errors are explicit,
        mutation transport errors end the turn rather than provoking an automatic replay.
        """
        turn = [{"role": "user", "content": text}]
        self.emit("working", request_id=request_id, stage="routing")
        async with asyncio.timeout(40):
            decision = await classify(text, self.turns)
        target_skills = decision.skills if decision.route == "system" else set()
        self.emit(
            "intent", request_id=request_id, route=decision.route,
            intents=list(decision.ids), labels=decision.labels,
            skills=sorted(target_skills), unknown_policy=decision.catalog["unknown_policy"],
        )
        logger.info(
            "Agent intent routed id=%s route=%s intents=%s skills=%s unknown_policy=%s",
            request_id, decision.route, ",".join(decision.ids), ",".join(sorted(target_skills)),
            decision.catalog["unknown_policy"],
        )
        reply = static_reply(decision)
        if decision.route == "general" and reply is None:
            async with asyncio.timeout(40):
                reply = await general_answer(text, self.turns)
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
            used = 0
            for _ in range(MAX_CALLS - 1):
                messages = [{"role": "system", "content": instructions() + (
                    "\nThis turn's validated intent IDs: " + json.dumps(decision.ids)
                    + ". Only these mapped skills may be loaded: "
                    + json.dumps(sorted(target_skills))
                    + ". Load every mapped skill before actions. Complete only the user's "
                    "requested operations; route selection does not authorize unrelated actions."
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
                            + ". Report observations; further actions require user input. "
                            "Follow the loaded skill to ask for the missing input or selection. "
                            "Do not automatically choose, retry or promise "
                                "background work. Reply in the user's language."
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
                async with asyncio.timeout(40):
                    message = await completion(
                        messages, visible_schemas,
                        required_tool=required,
                    )
                turn.append(message)
                calls = message.get("tool_calls", [])
                if not calls:
                    if required:
                        raise AgentError(
                            "discovery_required", "模型未完成工具发现与技能加载，已停止。"
                        )
                    self.turns.append(turn)
                    self.emit("reply", request_id=request_id, text=message["content"])
                    return
                for call in calls:
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
                    async with asyncio.timeout(35):
                        result = await client.call_tool(name, arguments)
                    if result.is_error:
                        logger.warning("Agent MCP failed id=%s name=%s", request_id, name)
                        raise AgentError(
                            "tool_failed", f"{name} 执行失败；请检查页面状态，未自动重试。"
                        )
                    observed = result.structured_content
                    if observed is None:
                        raise AgentError("invalid_tool_result", "工具未返回结构化结果。")
                    completed.add(name)
                    if bound_selection:
                        if observed.get("accepted") is True:
                            self.selection = None
                        else:
                            pause_reason = {"tool": name, "selected_candidate_action_failed": True}
                            permitted.clear()
                    if name == "discover_tools":
                        discovered = True
                        allowed.discard("discover_tools")
                        permitted.discard("discover_tools")
                        allowed.add("read_skill")
                        logger.info(
                            "Agent catalog discovered id=%s tools=%d", request_id, len(names)
                        )
                    elif name == "read_skill":
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
                    if name in result_counts:
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
                if selection_text is not None:
                    turn.append({"role": "assistant", "content": selection_text})
                    self.turns.append(turn)
                    self.emit("reply", request_id=request_id, text=selection_text)
                    return
            raise AgentError("model_step_limit", "本次处理达到步骤上限，请缩小请求范围。")

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
