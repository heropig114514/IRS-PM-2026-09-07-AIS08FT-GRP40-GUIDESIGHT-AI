"""Responsibilities: Enforce the included ZeroGPU seconds and free-run budget before dispatch.
Implementation: Strict, finite quota-schema validation; no network or GPU side effects.
Related Modules: core.hf_video_relay, tools.check_zerogpu_quota, tools.benchmark_zerogpu_live.
Declaration Index: quota_decision.
Variable Index: QUOTA_URL — official authenticated read-only quota endpoint;
DEFAULT_GPU_DURATION_SECONDS — unchanged decorator duration; BLACKWELL_DURATION_FACTOR — pinned
spaces0.51.3 scheduler factor; LIVE_GPU_QUOTA_SECONDS — actual90s reservation, not video duration.
"""

import math

QUOTA_URL = "https://huggingface.co/api/spaces/zero-gpu/quota"
DEFAULT_GPU_DURATION_SECONDS = 60
BLACKWELL_DURATION_FACTOR = 1.5
# Functionality: Match the owned Space's effective admission reservation, not its55s video loop.
# Logic: spaces0.51.3 configs.json/get_duration_seconds multiplies the default60s on Blackwell;
# Constraints: confirmed by HF's90s-requested rejection; keep model/task/GPU size unchanged.
LIVE_GPU_QUOTA_SECONDS = DEFAULT_GPU_DURATION_SECONDS * BLACKWELL_DURATION_FACTOR


def quota_decision(data, required_seconds):
    """Functionality: Determine whether one default-size/default-duration task is covered.
    Inputs: official quota JSON and explicit quota reservation within1–90 seconds.
    Outputs: allow/reason and safe quota/reset fields; Logic: require enough seconds and a run.
    Constraints: unknown fields fail closed; this snapshot cannot reserve quota against other
    clients; no credit extension, account switching, network, inference or hardware requests.
    """
    if (type(required_seconds) not in (int, float) or not math.isfinite(required_seconds)
            or not 1 <= required_seconds <= LIVE_GPU_QUOTA_SECONDS):
        raise ValueError("Reserve1–90 quota seconds for the unchanged default GPU task")
    remaining = data.get("current")
    runs = data.get("runs", {})
    count = runs.get("remaining") if isinstance(runs, dict) else None
    known = (type(remaining) in (int, float) and math.isfinite(remaining) and remaining >= 0
             and type(count) is int and count >= 0)
    allowed = known and remaining >= required_seconds and count > 0
    return {"allowed": bool(allowed), "required_gpu_seconds": required_seconds,
            "reason": "covered_by_free_quota" if allowed else (
                "unknown_free_quota" if not known else "free_run_limit_exhausted"
                if count == 0 else "insufficient_free_seconds"),
            "free_gpu_seconds": remaining if known else None,
            "free_runs_remaining": count if known else None,
            "seconds_reset_utc": data.get("resetsAt"),
            "runs_reset_utc": runs.get("resetsAt") if isinstance(runs, dict) else None}
