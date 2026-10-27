"""Responsibilities: Verify dedicated vibration tools through actual MCP Streamable HTTP.
Implementation: One official client, one explicit browser session and one selected scenario;
record structured acknowledgements without database, camera, inference or navigation calls.
Related Modules: core.ai_mcp; SystemCode/clips/frontend/tools/check_vibration_mcp_browser.mjs;
MCP.md.
Declaration Index: call; run; main.
Variable Index: None; args/report/history are invocation-local configuration and evidence.
Constraints: Scenario expectations require a dedicated controlled browser; motor verification
remains false. No implicit tab selection, permission changes, retries or alternative reminders.
"""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from mcp.client import Client


async def call(client, name, session_id, history, **arguments):
    """Functionality: Issue one actual MCP call and retain its structured acknowledgement.
    Inputs: client/name, explicit session_id, history list and fixed tool arguments.
    Outputs: Browser result or AssertionError; append non-media result to history.
    Logic: A transport/schema/tool error fails immediately; device accepted=false remains data.
    Constraints: Calls only vibration tools; no retry, database access or physical motor claim.
    """
    result = await client.call_tool(name, {"session_id": session_id, **arguments})
    if result.is_error:
        raise AssertionError(f"{name} failed: {result.content}")
    value = result.structured_content
    assert value["hardware_verified"] is False
    history.append({"tool": name, "arguments": arguments, "result": value})
    return value


async def run(args, report):
    """Functionality: Verify one controlled device/prerequisite scenario over real MCP HTTP.
    Inputs: args.url/session/scenario and mutable report. Outputs: complete flag and call history.
    Logic: Discover typed tools, read current browser state, then issue each selected request once.
    Constraints: The caller prepares UI/activation/native outcomes. This checker does not create
    a tab, enable testing, mutate a database, allocate a GPU or infer hardware state.
    """
    history = report["calls"]
    async with Client(args.url) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"vibration_status", "vibration_test", "vibration_stop"} <= names
        status = await call(client, "vibration_status", args.session, history)
        if args.scenario == "patterns":
            assert status["enabled"] is True and status["user_activated"] is True
            for pattern in ("short", "long", "double"):
                result = await call(
                    client, "vibration_test", args.session, history, pattern=pattern
                )
                assert result["accepted"] is True and result["reason"] is None
                assert result["pattern"] == pattern
            stopped = await call(client, "vibration_stop", args.session, history)
            assert stopped["accepted"] is True and stopped["request_pending"] is False
        else:
            reason = {
                "disabled": "disabled", "activation": "activation_required",
                "rejected": "native_rejected", "exception": "native_error",
                "unsupported": "unsupported",
            }[args.scenario]
            if args.scenario == "disabled":
                assert status["enabled"] is False
            elif args.scenario == "activation":
                assert status["enabled"] is True and status["user_activated"] is False
            elif args.scenario == "unsupported":
                assert status["supported"] is False
            result = await call(client, "vibration_test", args.session, history, pattern="short")
            assert result["accepted"] is False and result["reason"] == reason
            if args.scenario in {"rejected", "exception", "unsupported"}:
                stopped = await call(client, "vibration_stop", args.session, history)
                assert stopped["accepted"] is False and stopped["reason"] == reason
        report["complete"] = True


def main():
    """Functionality: Run a selected dedicated-tab check and persist evidence on failure.
    Inputs: Required --url/--session/--scenario/--output; existing MCP service and browser.
    Outputs: JSON evidence and success/failure exit code; exceptions retain their traceback.
    Logic: Accept credential-free HTTPS or loopback HTTP; asyncio drives one client session.
    Constraints: No service startup, database mutation, browser permission grant or retry.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--scenario", required=True, choices=(
        "disabled", "activation", "patterns", "rejected", "exception", "unsupported",
    ))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            not (parsed.scheme == "https" or parsed.scheme == "http" and
                 parsed.hostname in {"127.0.0.1", "localhost"})):
        raise ValueError("Use a credential-free HTTPS or explicit loopback MCP URL")
    report = {"complete": False, "scenario": args.scenario, "calls": [],
              "hardware_verified": False, "scope": "Real MCP HTTP/WS; controlled browser API"}
    try:
        asyncio.run(run(args, report))
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"complete": report["complete"], "scenario": args.scenario,
                          "calls": len(report["calls"]), "hardware_verified": False}))


if __name__ == "__main__":
    main()
