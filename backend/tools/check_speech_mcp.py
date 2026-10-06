"""Responsibilities: Verify speech output tools through actual MCP HTTP and a selected tab.
Implementation: One client/session/scenario, exact shared-interface parameters and structured
state assertions. The browser driver controls native fixtures or a separate real engine attempt.
Related Modules: core.ai_mcp; tools.check_mcp.call; frontend/tools/check_speech_browser.mjs.
Declaration Index: run; main.
Variable Index: None; report/args/status are invocation-local evidence and configuration.
Constraints: No database, sensor, navigation, voice fallback, automatic enabling or retry;
audio_output_verified remains false even when the engine reports completion.
"""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from mcp.client import Client

from tools.check_mcp import call


async def run(args, report):
    """Functionality: Verify one controlled speech stage via the official HTTP client.
    Inputs: URL, explicit session, scenario and optional exact native voice identity.
    Outputs: Complete flag, state and non-text call results; failure propagates.
    Logic: Discover required tools, query status, then dispatch only this scenario's operation.
    Constraints: Submit is not completion; event progression/activation belong to the driver.
    Native scenario is an explicit local voice/language attempt, never an implicit fallback.
    """
    async with Client(args.url) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"speech_status", "speech_voices", "speech_speak", "speech_stop"} <= names
        status = await call(client, "speech_status", session_id=args.session)
        report["status"] = status
        assert status["audio_output_verified"] is False
        if args.scenario in {"queued", "speaking", "completed", "engine_error"}:
            expected = {"engine_error": "error"}.get(args.scenario, args.scenario)
            assert status["state"] == expected
            if args.scenario == "engine_error":
                assert status["last_error"] == "not-allowed"
        elif args.scenario == "stop":
            result = await call(client, "speech_stop", session_id=args.session)
            assert result["accepted"] and result["state"] == "stopped"
            report["result"] = result
        elif args.scenario == "submit" or args.scenario == "native":
            language = args.language if args.scenario == "native" else "en-US"
            text = ("这是 PRS 语音输出测试。" if language == "zh-CN" else
                    "This is a PRS speech output test.")
            result = await call(client, "speech_speak", session_id=args.session,
                                text=text, language=language,
                                voice_id=args.voice or "fixture-en",
                                rate=1.0 if args.scenario == "native" else 1.4,
                                volume=0.3 if args.scenario == "native" else 0.6)
            report["result"] = result
            assert result["accepted"] and result["audio_output_verified"] is False
            if args.scenario == "submit":
                assert result["state"] == "queued"
                busy = await call(
                    client, "speech_speak", session_id=args.session, text="Second request"
                )
                assert not busy["accepted"] and busy["reason"] == "busy"
                report["busy"] = busy
        else:
            expected = {"disabled": "disabled", "activation": "activation_required",
                        "voice_missing": "voice_unavailable", "native_error": "native_error",
                        "unsupported": "unsupported"}[args.scenario]
            if args.scenario == "activation":
                assert status["enabled"] and status["user_activated"] is False
            voices = await call(client, "speech_voices", session_id=args.session)
            report["voices"] = voices
            result = await call(
                client, "speech_speak", session_id=args.session,
                text="This is a PRS speech output test.", language="en-US",
                voice_id="missing-voice" if args.scenario == "voice_missing" else None,
            )
            assert not result["accepted"] and result["reason"] == expected
            report["result"] = result
        report["complete"] = True


def main():
    """Functionality: Run an explicit speech scenario and persist real protocol evidence.
    Inputs: Required url/session/scenario/output, optional native voice id/language.
    Outputs: JSON, compact summary and failing traceback on errors.
    Logic: Validate credential-free HTTPS/loopback URL; run one official client.
    Constraints: No service startup or implicit fallback; native mode requires an explicit voice.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--scenario", required=True, choices=(
        "disabled", "activation", "submit", "queued", "speaking", "completed", "stop",
        "voice_missing", "native_error", "engine_error", "unsupported", "native",
    ))
    parser.add_argument("--voice")
    parser.add_argument("--language", choices=("zh-CN", "en-US"), default="en-US")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            not (parsed.scheme == "https" or parsed.scheme == "http" and
                 parsed.hostname in {"127.0.0.1", "localhost"})):
        raise ValueError("Use a credential-free HTTPS or explicit loopback URL")
    if args.scenario == "native" and not args.voice:
        raise ValueError("Native verification requires an explicit local voice")
    report = {"complete": False, "scenario": args.scenario, "audio_output_verified": False,
              "scope": "Real MCP HTTP/WS; controlled browser or explicit native engine scenario"}
    try:
        asyncio.run(run(args, report))
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"complete": report["complete"], "scenario": args.scenario,
                          "audio_output_verified": False}))


if __name__ == "__main__":
    main()
