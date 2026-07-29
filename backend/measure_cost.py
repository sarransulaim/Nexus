"""
measure_cost.py — what does Nexus actually cost to run?
=======================================================
Runs a realistic mix of commands through the REAL orchestrator and records
the token usage the API reports for every call, then prices it with the
same table the admin board uses.

This measures rather than estimates, and it separates the two regimes that
dominate the bill:
  • COLD  — first command of a session; the ~8k-token system prompt + tool
            schemas are written to cache (billed at 1.25x)
  • WARM  — every command after; that prefix is read from cache at 0.1x

Steady-state cost per seat is the WARM number. Run:  python measure_cost.py
"""
import sys
import time

import event_bus
from event_bus import estimate_cost

# ── capture every priced API call ────────────────────────────────
CALLS = []
_orig_emit_cost = event_bus.emit_cost


def _spy(model, input_tokens, output_tokens, actor=None,
         cache_read_tokens=0, cache_write_tokens=0, **kw):
    CALLS.append({
        "model": model, "in": input_tokens, "out": output_tokens,
        "cache_read": cache_read_tokens, "cache_write": cache_write_tokens,
        "cost": estimate_cost(model, input_tokens, output_tokens,
                              cache_read_tokens, cache_write_tokens),
    })
    return _orig_emit_cost(model, input_tokens, output_tokens, actor=actor,
                           cache_read_tokens=cache_read_tokens,
                           cache_write_tokens=cache_write_tokens, **kw)


event_bus.emit_cost = _spy
import api.claude_orchestrator as co   # noqa: E402  (must import after the spy)
co.emit_cost = _spy                    # module imported it by name

# ── a realistic day's mix ────────────────────────────────────────
MANAGER = "Manager_1"

SCENARIOS = [
    ("simple read",   MANAGER, "how many tasks are overdue?"),
    ("simple read",   MANAGER, "who is on the team?"),
    ("multi-tool",    MANAGER, "how is the team doing right now?"),
    ("multi-tool",    MANAGER, "what should I focus on today?"),
    ("heavy",         MANAGER, "give me a full update on overdue work and team workload"),
]


def run(employee_agent: str = None):
    if employee_agent:
        SCENARIOS.append(("employee read", employee_agent, "what are my tasks?"))

    print(f"Running {len(SCENARIOS)} commands through the real orchestrator...\n")
    rows = []
    for i, (kind, agent, cmd) in enumerate(SCENARIOS, 1):
        before = len(CALLS)
        t0 = time.time()
        try:
            co.run_orchestrator(agent, cmd)
        except Exception as e:
            print(f"  {i}. [{kind}] FAILED: {str(e)[:80]}")
            continue
        secs = time.time() - t0
        mine = CALLS[before:]
        cost = sum(c["cost"] for c in mine)
        cr = sum(c["cache_read"] for c in mine)
        cw = sum(c["cache_write"] for c in mine)
        regime = "COLD" if i == 1 else "warm"
        rows.append({"kind": kind, "cost": cost, "calls": len(mine),
                     "regime": regime, "secs": secs})
        print(f"  {i}. [{regime:4}] {kind:14} ${cost:.4f}  "
              f"({len(mine)} api calls, {secs:4.1f}s, cache_read={cr}, cache_write={cw})")

    total = sum(r["cost"] for r in rows)
    warm = [r for r in rows if r["regime"] == "warm"]
    warm_avg = sum(r["cost"] for r in warm) / len(warm) if warm else 0
    cold = rows[0]["cost"] if rows else 0

    print("\n" + "=" * 62)
    print(f"MEASURED — this run cost ${total:.4f} across {len(rows)} commands")
    print(f"  first command of a session (cold cache): ${cold:.4f}")
    print(f"  every command after (warm cache), avg  : ${warm_avg:.4f}")
    print("=" * 62)

    # Steady state: one cold start per working session + N warm commands.
    print("\nPROJECTED COST PER PERSON PER MONTH (22 working days)")
    print(f"{'profile':28} {'cmds/day':>9} {'$/month':>9}")
    for label, per_day in [("light employee", 3), ("active employee", 8),
                           ("team lead", 15), ("manager (heavy)", 30)]:
        # 1 cold start/day + the rest warm
        daily = cold + max(0, per_day - 1) * warm_avg
        print(f"{label:28} {per_day:>9} {daily * 22:>9.2f}")

    print("\nNote: background jobs (briefings, digests, drift scans) are extra —")
    print("they run per company, not per seat. Measured separately below if run.")
    return total


if __name__ == "__main__":
    emp = sys.argv[1] if len(sys.argv) > 1 else None
    run(emp)
