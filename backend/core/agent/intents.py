"""Responsibilities: Route natural language against a repository-owned intent catalog.
Implementation: One structured Qwen classification, strict schema/mapping validation, explicit
unmatched policy; general answers receive no MCP client or tools. No keyword or place-name table.
Related Modules: skills owns trusted bundle paths; runtime enforces mapped skills.
Declaration Index: IntentDecision; IntentDecision.skills; IntentDecision.labels; load_catalog;
dialogue; reply_language_instruction; classify; static_reply; general_answer.
Variable Index: None; policy is loaded from agent_skills/intents.json on each turn.
"""

import json
import re
from dataclasses import dataclass

from . import skills
from .provider import AgentError, completion


@dataclass(frozen=True)
class IntentDecision:
    """Functionality: Carry a validated route and the exact catalog used for classification.
    Logic: IDs select trusted skill names/labels; model output never supplies either directly.
    Constraints: Configuration is read-only by convention; no confidence or device-state claim.
    State: route/ids/language are validated model fields; catalog is validated repository policy.
    """

    route: str
    ids: tuple
    language: str
    catalog: dict

    @property
    def skills(self):
        """Functionality: Resolve authorized workflows. Inputs: IDs/catalog; Outputs: set.
        Logic: Union configured mappings; Constraints: empty help mapping authorizes no tools.
        """
        return {name for item in self.catalog["intents"] if item["id"] in self.ids
                for name in item["skills"]}

    @property
    def labels(self):
        """Functionality: Resolve display labels. Inputs: IDs/catalog; Outputs: bilingual list.
        Logic: Preserve catalog order; Constraints: no unvalidated model prose in activity labels.
        """
        return [item["labels"] for item in self.catalog["intents"] if item["id"] in self.ids]


def load_catalog():
    """Functionality: Load bounded intent definitions and the explicit unmatched policy.
    Inputs: trusted skills.ROOT/intents.json and installed skill names; Outputs: validated dict.
    Logic: Reject extra fields, duplicate IDs, missing mappings and malformed metadata up front.
    Constraints: No model-selected files, reload fallback or executable configuration.
    """
    source = (skills.ROOT / "intents.json").read_text(encoding="utf-8")
    if len(source) > 24000:
        raise ValueError("Intent catalog exceeds size limit")
    config = json.loads(source)
    if (not isinstance(config, dict)
            or set(config) != {"version", "unknown_policy", "max_intents", "intents"}
            or type(config["version"]) is not int or config["version"] != 1
            or config["unknown_policy"] not in ("reject", "general")
            or type(config["max_intents"]) is not int or not 1 <= config["max_intents"] <= 3
            or not isinstance(config["intents"], list) or not 1 <= len(config["intents"]) <= 64):
        raise ValueError("Invalid intent catalog configuration")
    known_skills, seen = skills.catalog(), set()
    for item in config["intents"]:
        if (not isinstance(item, dict)
                or set(item) != {"id", "labels", "description", "examples", "skills"}
                or not isinstance(item["id"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", item["id"])
                or item["id"] in seen
                or not isinstance(item["labels"], dict) or set(item["labels"]) != {"zh", "en"}
                or not isinstance(item["description"], str)
                or not 1 <= len(item["description"]) <= 600
                or not isinstance(item["examples"], list) or not 1 <= len(item["examples"]) <= 8
                or not isinstance(item["skills"], list)
                or any(not isinstance(name, str) or name not in known_skills
                       for name in item["skills"])
                or len(item["skills"]) != len(set(item["skills"]))):
            raise ValueError("Invalid intent entry or skill mapping")
        for value in [*item["labels"].values(), *item["examples"]]:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 160:
                raise ValueError("Invalid intent label/example")
        seen.add(item["id"])
    return config


def dialogue(turns, text, prompt=None):
    """Functionality: Supply conversational context without execution data or tool schemas.
    Inputs: completed turns, current text, optional trusted prompt; Outputs: model messages.
    Logic: Preserve natural-language follow-ups, exclude tool calls/results and empty messages.
    Constraints: Prior dialogue is untrusted evidence; 120000-character bound fails explicitly.
    """
    messages = [{"role": item["role"], "content": item["content"]}
                for turn in turns for item in turn
                if item["role"] in {"user", "assistant"} and item.get("content")
                and not item.get("tool_calls")]
    messages.append({"role": "user", "content": text})
    if prompt is not None:
        messages.insert(0, {"role": "system", "content": prompt})
    if len(json.dumps(messages, ensure_ascii=False)) > 120000:
        raise AgentError("context_limit", "对话上下文达到上限，请清空对话后重新开始。")
    return messages


def reply_language_instruction(language):
    """Functionality: Give response-generation models an explicit, bounded language policy.
    Inputs: Validated current-turn language code, either en or zh; Outputs: English system
    instruction with a required response language. Logic: Treat the router's current-turn result
    as authoritative and exclude stale dialogue and interface locale.
    Constraints: Supports English or Simplified Chinese; the router resolves short follow-ups.
    """
    if language not in {"en", "zh"}:
        raise ValueError("Unsupported response language")
    name = "English" if language == "en" else "Simplified Chinese"
    return (
        f"Required response language for this turn: {name} ({language}). The intent router "
        "selected this language from the current user message. Respond only in the required "
        "language for all user-facing prose, including questions and failure explanations. "
        "Do not switch languages based on older user turns, assistant messages, tool output, "
        "or page/device locale. The router resolves short answers to its pending request."
    )


async def classify(text, turns):
    """Functionality: Classify one request before any MCP discovery or skill execution.
    Inputs: text, prior turns and trusted catalog; Outputs: IntentDecision or explicit error.
    Logic: Force one route_intent structured response; the current message controls its language
    estimate; validate enum, multiplicity and routes.
    Constraints: No retry, action invocation, guessed skill, or general-mode switch on failure.
    """
    config = load_catalog()
    ids = [item["id"] for item in config["intents"]]
    schema = {"type": "function", "function": {
        "name": "route_intent", "description": "Classify only; this never performs an action.",
        "parameters": {"type": "object", "additionalProperties": False,
                       "required": ["route", "intent_ids", "language"], "properties": {
                           "route": {"type": "string", "enum": [
                               "system", "clarify", "general", "unsupported"]},
                           "intent_ids": {"type": "array", "uniqueItems": True,
                                          "maxItems": config["max_intents"],
                                          "items": {"type": "string", "enum": ids}},
                           "language": {"type": "string", "enum": ["zh", "en"]}}}}}
    prompt = (skills.ROOT / "intent_router.md").read_text(encoding="utf-8")
    message = await completion(
        dialogue(turns, text, prompt + "\nCatalog:\n" + json.dumps(config)),
        [schema], required_tool="route_intent",
    )
    try:
        calls = message.get("tool_calls", [])
        if len(calls) != 1 or calls[0]["function"]["name"] != "route_intent":
            raise ValueError("One classifier result required")
        result = json.loads(calls[0]["function"]["arguments"])
        if (not isinstance(result, dict) or set(result) != {"route", "intent_ids", "language"}
                or result["route"] not in {"system", "clarify", "general", "unsupported"}
                or result["language"] not in {"zh", "en"}
                or not isinstance(result["intent_ids"], list)
                or len(result["intent_ids"]) > config["max_intents"]
                or any(not isinstance(item, str) or item not in ids
                       for item in result["intent_ids"])
                or len(set(result["intent_ids"])) != len(result["intent_ids"])
                or (result["route"] == "system" and not result["intent_ids"])
                or (result["route"] in {"general", "unsupported"} and result["intent_ids"])):
            raise ValueError("Invalid classifier result")
    except (KeyError, TypeError, ValueError) as error:
        raise AgentError("invalid_intent_decision", "意图识别结果无效，未执行系统操作。") from error
    return IntentDecision(result["route"], tuple(result["intent_ids"]), result["language"], config)


def static_reply(decision):
    """Functionality: Resolve clarification, rejection and catalog-help without model actions.
    Inputs: validated decision; Outputs: localized text, or None for skill/general execution.
    Logic: General questions only proceed when explicitly enabled; unsupported actions never do.
    Constraints: No dynamic device claims; empty skill mappings mean catalog-help only.
    """
    zh = decision.language == "zh"
    limit = decision.catalog["max_intents"]
    if decision.route == "clarify":
        options = "、".join(item[decision.language] for item in decision.labels)
        if options:
            return (f"请明确要执行哪项操作：{options}；一次最多{limit}项。" if zh else
                    f"Please specify the operation: {options}. Up to {limit} per request.")
        return (f"请明确要操作的功能，例如导航、摄像头、朗读或震动；一次最多{limit}项。" if zh else
                f"Please specify navigation, camera, speech or vibration; "
                f"up to {limit} per request.")
    if decision.route == "system" and decision.skills:
        return None
    if decision.route == "general" and decision.catalog["unknown_policy"] == "general":
        return None
    support = ("支持的操作：" if zh else "Supported operations: ") + (
        "、" if zh else ", "
    ).join(item["labels"][decision.language] for item in decision.catalog["intents"]
           if item["skills"])
    if decision.route == "system":
        return support + ("。说出操作和必要信息即可。" if zh else ". Say what you want to do.")
    prefix = ("当前请求不属于已启用的能力范围，未执行系统操作。" if zh else
              "This request is outside the enabled capabilities. No system action was taken. ")
    return prefix + ("可说“你能做什么”查看支持的操作。" if zh else
                     'Ask "What can you do?" to list supported operations.')


async def general_answer(text, turns, language):
    """Functionality: Answer an explicitly routed general question without system tools.
    Inputs: current text, dialogue and validated language estimate; Outputs: nonempty assistant
    text or AgentError. Logic: Separate trusted prompt, explicit reply-language policy, no MCP
    creation/schema; reject unsolicited tool requests.
    Constraints: One call, no live-state claims, no fallback on provider or router failure.
    """
    message = await completion(dialogue(
        turns, text, (skills.ROOT / "general_assistant.md").read_text(encoding="utf-8")
        + "\n\n" + reply_language_instruction(language),
    ))
    if message.get("tool_calls") or not isinstance(message.get("content"), str) or not message[
        "content"
    ].strip():
        raise AgentError("invalid_general_answer", "通用问答返回无效结果，未执行系统操作。")
    return message["content"]
