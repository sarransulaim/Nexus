"""
spend.py — record what the AI costs, and stop it running away
=============================================================
Cost was measured (event_bus.estimate_cost is cache-aware and accurate) but
never stored, so two things were impossible: answering "what did we spend
yesterday", and putting a ceiling on it. Rate limiting caps how MANY requests
an account makes, not how much each one costs — a single long agentic turn
with tool calls is worth hundreds of trivial ones, so 30 requests/minute is
not a spending limit.

Design notes, because the naive version is wrong in a way that bites later:

* The check runs BEFORE a call, against spend so far. A single call can
  therefore push you past the cap — the cost of a turn isn't knowable in
  advance. This is a circuit breaker, not an accountant.

* Attribution is by PERSON, not by agent label. "Manager_1" is a role, not
  an employee id, and resolving it matters: the manager is usually the
  heaviest user, so leaving it unresolved would file their usage under
  "background jobs" and exempt the busiest account from the personal cap.

* Genuinely person-less work (digests, drift alerts, dependency mapping, the
  shared-channel Team agent) counts toward the COMPANY total but is never
  blamed on a user, and is never blocked by a user's personal cap — otherwise
  one employee burning their budget would stop the nightly digest for
  everyone.

* Failing open is deliberate. If the spend table is unreachable we let the
  call through rather than taking the assistant offline over a bookkeeping
  problem. An availability failure here is worse than an over-spend, and the
  over-spend is bounded by the provider's own limits.
"""

import os
from datetime import datetime, timedelta, timezone

# Per-person daily ceiling. Set from the env so it can be tuned without a
# deploy. 0 disables the personal cap (the company cap still applies).
DAILY_USD_PER_USER = float(os.getenv("NEXUS_DAILY_USD_PER_USER", "5"))

# Whole-instance daily ceiling — the backstop that catches a runaway loop in
# background work, which no per-user cap would ever see.
DAILY_USD_PER_COMPANY = float(os.getenv("NEXUS_DAILY_USD_PER_COMPANY", "50"))


class BudgetExceeded(Exception):
    """Raised with a message intended to be shown to the person who hit it."""


def _employee_id_from_agent(agent_id, company_id=1) -> int | None:
    """The person behind an agent_id, or None if there isn't one.

    "Employee_12" -> 12, straightforwardly.

    "Manager_1" is the manager ROLE, not a row id — the trailing number is not
    an employee id (this is the same trap that once made the Google tools look
    disconnected for the manager). Resolve it to the real manager row instead
    of giving up: the manager is typically the heaviest user, so treating them
    as "not a person" would leave the busiest account with no personal budget
    and file their usage under "background jobs" in the report.

    "Team_C123" is a shared channel, not an individual, and genuinely has no
    personal budget — same for scheduler-driven work.
    """
    text = str(agent_id or "")

    if text.startswith("Employee_"):
        try:
            return int(text.split("_", 1)[1])
        except (IndexError, ValueError):
            return None

    if text.startswith("Manager_"):
        try:
            from database.core import SessionLocal
            from database.models import Employee
            db = SessionLocal()
            try:
                row = db.query(Employee).filter(
                    Employee.company_id == company_id,
                    Employee.system_role == "manager",
                    Employee.is_active == True,      # noqa: E712
                ).order_by(Employee.id).first()
                return row.id if row else None
            finally:
                db.close()
        except Exception:
            return None

    return None


def _midnight_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def record(agent_id, model, input_tokens, output_tokens,
           cache_read_tokens=0, cache_write_tokens=0, company_id=1) -> float:
    """Persist one call's cost. Never raises — accounting must not break a turn."""
    try:
        from event_bus import estimate_cost
        from database.core import SessionLocal
        from database.models import AiSpend

        cost = estimate_cost(model, input_tokens or 0, output_tokens or 0,
                             cache_read_tokens or 0, cache_write_tokens or 0)

        # estimate_cost returns 0.0 for a model it has no price for. That is a
        # silent failure of the budget: swap to a model missing from PRICING
        # and every call records as free, so the cap never fires and the first
        # sign of trouble is the invoice. Say so loudly. (test_ai_spend also
        # asserts every model the code actually calls is priced.)
        if cost == 0.0 and (input_tokens or output_tokens):
            print(f"⚠️  AI spend: no pricing for model {model!r} — this call was "
                  f"recorded as $0 and does NOT count toward any budget. Add it "
                  f"to PRICING in event_bus.py.")
        db = SessionLocal()
        try:
            db.add(AiSpend(
                company_id=company_id,
                employee_id=_employee_id_from_agent(agent_id, company_id),
                agent_id=str(agent_id)[:100] if agent_id else None,
                model=str(model)[:100] if model else None,
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                cache_read_tokens=cache_read_tokens or 0,
                cache_write_tokens=cache_write_tokens or 0,
                cost_usd=cost,
            ))
            db.commit()
        finally:
            db.close()
        return cost
    except Exception:
        return 0.0


def spent_today(employee_id=None, company_id=1) -> float:
    """USD spent since UTC midnight — for one person, or the whole company."""
    from sqlalchemy import func
    from database.core import SessionLocal
    from database.models import AiSpend

    db = SessionLocal()
    try:
        q = db.query(func.coalesce(func.sum(AiSpend.cost_usd), 0.0)).filter(
            AiSpend.company_id == company_id,
            AiSpend.created_at >= _midnight_utc(),
        )
        if employee_id is not None:
            q = q.filter(AiSpend.employee_id == employee_id)
        return float(q.scalar() or 0.0)
    finally:
        db.close()


def check(agent_id, company_id=1) -> None:
    """Raise BudgetExceeded if this caller has run out of budget for the day.

    Fails OPEN on any bookkeeping error: taking the assistant offline because
    a query failed is a worse outcome than one turn over budget.
    """
    try:
        company_spend = spent_today(company_id=company_id)
    except Exception:
        return

    if DAILY_USD_PER_COMPANY > 0 and company_spend >= DAILY_USD_PER_COMPANY:
        raise BudgetExceeded(
            f"Nexus has reached its daily AI budget for this workspace "
            f"(${company_spend:.2f} of ${DAILY_USD_PER_COMPANY:.2f}). It resets at "
            f"midnight UTC. A manager can raise NEXUS_DAILY_USD_PER_COMPANY if this "
            f"is expected."
        )

    employee_id = _employee_id_from_agent(agent_id, company_id)
    if employee_id is None or DAILY_USD_PER_USER <= 0:
        return   # background work and non-personal agents: company cap only

    try:
        personal_spend = spent_today(employee_id=employee_id, company_id=company_id)
    except Exception:
        return

    if personal_spend >= DAILY_USD_PER_USER:
        raise BudgetExceeded(
            f"You've reached your daily AI budget (${personal_spend:.2f} of "
            f"${DAILY_USD_PER_USER:.2f}). It resets at midnight UTC — ask a manager "
            f"if you need more."
        )


def summary(company_id=1, days=7) -> dict:
    """Spend broken down by person and by day. For the manager's dashboard and
    for answering 'why was the bill that size'."""
    from sqlalchemy import func
    from database.core import SessionLocal
    from database.models import AiSpend, Employee

    since = _midnight_utc() - timedelta(days=max(0, days - 1))
    db = SessionLocal()
    try:
        rows = (db.query(AiSpend.employee_id,
                         func.coalesce(func.sum(AiSpend.cost_usd), 0.0),
                         func.count(AiSpend.id))
                  .filter(AiSpend.company_id == company_id, AiSpend.created_at >= since)
                  .group_by(AiSpend.employee_id).all())
        names = {e.id: e.name for e in db.query(Employee).filter(
            Employee.company_id == company_id).all()}
        by_person = sorted(
            [{"employee_id": eid,
              "name": names.get(eid, "background jobs" if eid is None else f"#{eid}"),
              "usd": round(float(total), 4), "calls": int(calls)}
             for eid, total, calls in rows],
            key=lambda r: r["usd"], reverse=True)
        return {
            "days": days,
            "total_usd": round(sum(r["usd"] for r in by_person), 4),
            "today_usd": round(spent_today(company_id=company_id), 4),
            "limits": {"per_user_daily": DAILY_USD_PER_USER,
                       "per_company_daily": DAILY_USD_PER_COMPANY},
            "by_person": by_person,
        }
    finally:
        db.close()
