"""Responsibilities: Evaluate catalog routing against real Qwen with explicit synthetic cases.
Implementation: Bounded independent requests; persist expected/observed decisions and failures.
Related Modules: core.agent.intents/provider/skills; fixture JSON supplied by the operator.
Declaration Index: main; main.evaluate.
Variable Index: None; CLI paths, concurrency semaphore and evidence belong to main.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import django

from core.agent.intents import classify, general_answer
from core.agent.provider import configuration
from core.agent.skills import fingerprint


async def main():
    """Functionality: Run an explicit paid-service classification evaluation without MCP.
    Inputs: --cases/--output paths, --live-qwen consent and Django environment configuration.
    Outputs: JSON report, terminal counts, nonzero status on failed expectations.
    Logic: At most three concurrent independent cases, one attempt each, exact expected IDs.
    Constraints: Synthetic dialogue only; no GPS/action/hardware proof or confidence calibration.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-qwen", action="store_true", required=True)
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    semaphore = asyncio.Semaphore(3)

    async def evaluate(case):
        """Functionality: Compare a single unmodified fixture with one real classifier result.
        Inputs: fixture ID/text/history/expected route/IDs; Outputs: evidence dict.
        Logic: 40s deadline and exact set comparison; retain failures without retry.
        Constraints: Logs/reports contain only synthetic fixture content, never credentials.
        """
        async with semaphore:
            start = time.monotonic()
            try:
                async with asyncio.timeout(40):
                    decision = await classify(case["text"], case.get("turns", []))
                actual = {"route": decision.route, "ids": sorted(decision.ids)}
                return {**case, "actual": actual, "passed": actual == case["expected"],
                        "elapsed_ms": round((time.monotonic() - start) * 1000)}
            except Exception as error:
                return {**case, "passed": False, "error_type": type(error).__name__,
                        "error_code": getattr(error, "code", None)}

    results = await asyncio.gather(*(evaluate(case) for case in cases))
    general = {"input": "What is a triangle?", "policy": "explicit isolated general-mode probe"}
    try:
        async with asyncio.timeout(40):
            general["reply"] = await general_answer(general["input"], [])
        general["passed"] = bool(general["reply"].strip())
    except Exception as error:
        general.update(passed=False, error_type=type(error).__name__)
    report = {"scope": "Real Qwen, synthetic bilingual intent fixtures; no MCP or device actions",
              "model": configuration()["model"], "temperature": 0, "bundle": fingerprint(),
              "cases": results, "general_question": general,
              "passed": sum(case["passed"] for case in results), "total": len(results)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"],
                      "general": general["passed"]}))
    if report["passed"] != report["total"] or not general["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
