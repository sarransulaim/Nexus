"""
measure_background.py — what the schedulers cost per company per day.
=====================================================================
Per-seat command cost is only half the bill: briefings, digests, drift
scans and dependency mapping run on timers whether anyone logs in or not.

Instruments the Anthropic SDK itself (not event_bus), because the cost
telemetry only fires from the orchestrator — every ai_router call
(briefings, digest narration, preference extraction) is invisible to it.

These jobs prefer local Ollama (free), but fall back to PAID Haiku when
Ollama is unreachable — which is always the case on the cloud deployment,
where there is no Ollama sidecar. So this measures real cloud cost.
"""
import time

import anthropic
from anthropic.resources.messages import Messages
from event_bus import estimate_cost

CALLS = []

_orig_create = Messages.create


def _spy_create(self, *args, **kwargs):
    resp = _orig_create(self, *args, **kwargs)
    try:
        u = resp.usage
        model = kwargs.get("model", "claude-sonnet-4-6")
        CALLS.append({
            "model": model,
            "cost": estimate_cost(
                model, u.input_tokens, u.output_tokens,
                getattr(u, "cache_read_input_tokens", 0) or 0,
                getattr(u, "cache_creation_input_tokens", 0) or 0),
        })
    except Exception:
        pass
    return resp


Messages.create = _spy_create


def measure(label, fn, per_day):
    before = len(CALLS)
    t0 = time.time()
    try:
        result = fn()
    except Exception as e:
        print(f"  {label:26} FAILED: {str(e)[:70]}")
        return 0.0
    mine = CALLS[before:]
    cost = sum(c["cost"] for c in mine)
    print(f"  {label:26} ${cost:.4f}/run ({len(mine)} api calls, {time.time()-t0:4.1f}s)"
          f"  x{per_day}/day = ${cost * per_day:.4f}/day")
    if isinstance(result, dict):
        print(f"      -> {str(result)[:100]}")
    return cost * per_day


if __name__ == "__main__":
    print("Measuring scheduled background jobs (SDK-level instrumentation)...\n")
    daily = 0.0

    import autonomous_briefings as ab
    import project_digest as pd
    import dependency_inference as di

    daily += measure("morning briefings (all)", lambda: ab.run_all_briefings(force=True), 1)
    daily += measure("project digests (all)", lambda: pd.run_all_digests(force=True), 1)
    daily += measure("drift alerts", lambda: pd.run_drift_alerts(), 1)
    daily += measure("dependency auto-map", lambda: di.auto_map_unmapped_projects(), 48)

    print("\n" + "=" * 66)
    print(f"BACKGROUND TOTAL: ${daily:.4f}/day  =  ${daily * 30:.2f}/month per company")
    print("=" * 66)
