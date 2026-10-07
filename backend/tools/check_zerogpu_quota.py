"""Responsibilities: Refuse GPU experiments when included ZeroGPU quota cannot cover one task.
Implementation: Read authenticated free seconds/run limits; strict validation, no inference call.
Related Modules: .env.hf private token; HF official zero-gpu/quota API; deploy/README.md.
Declaration Index: main.
Variable Index: ROOT — private configuration root; quota policy/URL imported from core.hf_quota.
"""

import argparse
import json
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

from core.hf_quota import LIVE_GPU_QUOTA_SECONDS, QUOTA_URL, quota_decision

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Functionality: Save a read-only free-budget gate before any separate ZeroGPU experiment.
    Inputs: budget/output CLI and private .env.hf HF_TOKEN; Outputs: JSON, exit2 if denied.
    Logic: single authenticated quota GET, strict policy, record no credential or sensitive headers.
    Constraints: no installs, retries, paid hardware request or actual inference; API failures raise
    before allowing work. This CLI does not itself dispatch or automatically schedule GPU tasks.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-budget-seconds", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.gpu_budget_seconds <= LIVE_GPU_QUOTA_SECONDS:
        parser.error("Budget must be1–90 quota seconds; the default GPU task remains60s")
    token = dotenv_values(ROOT / ".env.hf").get("HF_TOKEN", "")
    if not token or not token.startswith("hf_") or any(char.isspace() for char in token):
        raise ValueError("Configure a valid private HF_TOKEN with read-billing permission")
    request = urllib.request.Request(QUOTA_URL, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        result = quota_decision(json.load(response), args.gpu_budget_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if not result["allowed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
