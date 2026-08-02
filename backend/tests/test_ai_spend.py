"""
AI spend tracking and the daily budget cap.

Two failure modes matter equally here and both are tested: a cap that doesn't
stop a runaway (the bill), and a cap that stops legitimate work (the outage).
"""
from datetime import datetime, timedelta, timezone

import pytest

from database.core import SessionLocal
from database.models import AiSpend, Employee
from api.security import create_access_token
from api import spend as spend_mod


@pytest.fixture()
def actors():
    s = SessionLocal()
    try:
        mgr = s.query(Employee).filter(
            Employee.system_role == "manager", Employee.is_active == True).first()
        emp = s.query(Employee).filter(
            Employee.system_role == "employee", Employee.is_active == True).first()
        assert mgr and emp
        return {"mgr_id": mgr.id, "emp_id": emp.id,
                "mgr_tok": create_access_token(mgr.id, mgr.system_role, mgr.name),
                "emp_tok": create_access_token(emp.id, emp.system_role, emp.name)}
    finally:
        s.close()


@pytest.fixture()
def clean_spend():
    """Remove test rows before and after so a real day's usage isn't disturbed."""
    def _purge():
        s = SessionLocal()
        s.query(AiSpend).filter(AiSpend.model == "TESTMODEL").delete()
        s.commit(); s.close()
    _purge()
    yield
    _purge()


def _add_spend(employee_id, usd, company_id=1, when=None):
    s = SessionLocal()
    try:
        row = AiSpend(company_id=company_id, employee_id=employee_id,
                      agent_id=f"Employee_{employee_id}" if employee_id else None,
                      model="TESTMODEL", cost_usd=usd)
        if when is not None:
            row.created_at = when
        s.add(row); s.commit()
    finally:
        s.close()


# ── recording ────────────────────────────────────────────────────
def test_each_call_is_recorded_with_a_cost(clean_spend, actors):
    before = spend_mod.spent_today(employee_id=actors["emp_id"])
    cost = spend_mod.record(f"Employee_{actors['emp_id']}", "claude-sonnet-4-6",
                            input_tokens=10_000, output_tokens=2_000)
    assert cost > 0, "a call with real token counts recorded zero cost"
    after = spend_mod.spent_today(employee_id=actors["emp_id"])
    assert after > before


def test_background_work_is_not_billed_to_a_person(clean_spend):
    """A shared channel and scheduler-driven work have no user behind them —
    they count toward the company total but never against a personal budget."""
    assert spend_mod._employee_id_from_agent("Team_C123") is None
    assert spend_mod._employee_id_from_agent(None) is None
    spend_mod.record("Team_C123", "claude-sonnet-4-6", 5_000, 500)
    s = SessionLocal()
    try:
        row = (s.query(AiSpend).filter(AiSpend.agent_id == "Team_C123")
                 .order_by(AiSpend.id.desc()).first())
        assert row is not None, "the call was not recorded at all"
        assert row.employee_id is None, "background spend was billed to a person"
        assert row.cost_usd > 0, "background spend recorded as free"
        s.query(AiSpend).filter(AiSpend.id == row.id).delete()
        s.commit()
    finally:
        s.close()


def test_recording_never_raises_on_a_broken_call():
    """Accounting must not be able to break a turn."""
    assert spend_mod.record(None, None, None, None) == 0.0


# ── enforcement ──────────────────────────────────────────────────
def test_user_over_budget_is_refused(clean_spend, actors, monkeypatch):
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 1.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 1.50)
    with pytest.raises(spend_mod.BudgetExceeded) as e:
        spend_mod.check(f"Employee_{actors['emp_id']}")
    assert "daily AI budget" in str(e.value)


def test_user_under_budget_is_allowed(clean_spend, actors, monkeypatch):
    """Positive control — the cap must not block ordinary use."""
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 10.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 0.25)
    spend_mod.check(f"Employee_{actors['emp_id']}")   # must not raise


def test_one_users_overspend_does_not_block_another(clean_spend, actors, monkeypatch):
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 1.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 5.0)
    spend_mod.check(f"Employee_{actors['mgr_id']}")   # a different person: fine


def test_personal_cap_does_not_block_background_work(clean_spend, actors, monkeypatch):
    """A user burning their budget must not stop the nightly digest."""
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 0.01)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 5.0)
    spend_mod.check("Team_C123")     # background agent — company cap only
    spend_mod.check("Manager_1")


def test_company_cap_stops_everyone_including_background(clean_spend, actors, monkeypatch):
    """The backstop for a runaway loop in background work, which no per-user
    cap would ever see."""
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 1000.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 2.0)
    _add_spend(None, 3.0)
    for agent in (f"Employee_{actors['emp_id']}", "Manager_1", "Team_C123"):
        with pytest.raises(spend_mod.BudgetExceeded):
            spend_mod.check(agent)


def test_yesterdays_spend_does_not_count_against_today(clean_spend, actors, monkeypatch):
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 1.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 99.0,
               when=datetime.now(timezone.utc) - timedelta(days=1, hours=2))
    spend_mod.check(f"Employee_{actors['emp_id']}")   # must not raise


def test_zero_limit_disables_the_personal_cap(clean_spend, actors, monkeypatch):
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 0.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 500.0)
    spend_mod.check(f"Employee_{actors['emp_id']}")


def test_budget_check_fails_open(monkeypatch, actors):
    """A bookkeeping error must not take the assistant offline — that outage
    would be worse than one turn over budget."""
    def _boom(*a, **k):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(spend_mod, "spent_today", _boom)
    spend_mod.check(f"Employee_{actors['emp_id']}")   # must not raise


# ── the orchestrator actually honours it ─────────────────────────
def test_orchestrator_refuses_when_over_budget(clean_spend, actors, monkeypatch):
    """End-to-end: the cap has to stop a real turn before any paid call, not
    merely exist as a helper."""
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 0.5)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["emp_id"], 2.0)

    called = {"model": False}
    import api.claude_orchestrator as co
    original = co.claude_client

    class _Tripwire:
        def __getattr__(self, _name):
            called["model"] = True
            raise AssertionError("a paid model call was made while over budget")

    monkeypatch.setattr(co, "claude_client", _Tripwire())
    try:
        out = co.run_orchestrator(f"Employee_{actors['emp_id']}", "what are my tasks?")
    finally:
        monkeypatch.setattr(co, "claude_client", original)

    assert not called["model"], "over-budget turn still reached the model"
    assert "budget" in out.lower(), f"unexpected reply: {out[:150]}"


# ── manager visibility ───────────────────────────────────────────
def test_spend_endpoint_is_manager_only(client, actors):
    r = client.get("/api/v1/analytics/ai-spend",
                   headers={"Authorization": f"Bearer {actors['emp_tok']}"})
    assert r.status_code == 403, "per-person usage data exposed to an employee"


def test_spend_endpoint_reports_limits_and_totals(client, actors, clean_spend):
    _add_spend(actors["emp_id"], 1.25)
    r = client.get("/api/v1/analytics/ai-spend",
                   headers={"Authorization": f"Bearer {actors['mgr_tok']}"})
    assert r.status_code == 200
    body = r.json()
    assert "limits" in body and "today_usd" in body and "by_person" in body
    assert body["limits"]["per_user_daily"] == spend_mod.DAILY_USD_PER_USER


# ── the budget only works if every model we call is priced ───────
def test_every_model_the_code_calls_has_a_price():
    """estimate_cost returns 0.0 for a model it has no price for, so an
    unpriced model records as free and never counts toward a budget — the
    first sign of that would be the invoice. Scan the source for the models
    actually referenced and assert each one is priced."""
    import pathlib
    import re
    from event_bus import PRICING

    backend = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"[\"'](claude-[A-Za-z0-9.\-]+)[\"']")
    referenced = set()
    for name in ("api/claude_orchestrator.py", "ai_router.py", "resolution_engine.py",
                 "dependency_inference.py", "negotiation_engine.py"):
        path = backend / name
        if path.exists():
            referenced |= set(pattern.findall(path.read_text(encoding="utf-8")))

    assert referenced, "found no model names to check — has the scan broken?"
    unpriced = sorted(m for m in referenced if m not in PRICING)
    assert not unpriced, (
        f"these models are called but have no entry in event_bus.PRICING, so their "
        f"cost records as $0 and never counts toward any budget: {unpriced}"
    )


def test_manager_spend_is_attributed_to_the_manager(actors):
    """"Manager_1" is a ROLE label, not an employee id. Left unresolved, the
    heaviest user of the product would have no personal budget and their usage
    would appear in the report as "background jobs"."""
    resolved = spend_mod._employee_id_from_agent("Manager_1")
    assert resolved == actors["mgr_id"], (
        f"manager agent resolved to {resolved}, expected the manager row "
        f"{actors['mgr_id']} — manager usage is unattributed and uncapped"
    )


def test_manager_is_subject_to_the_personal_cap(clean_spend, actors, monkeypatch):
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_USER", 1.0)
    monkeypatch.setattr(spend_mod, "DAILY_USD_PER_COMPANY", 1000.0)
    _add_spend(actors["mgr_id"], 2.0)
    with pytest.raises(spend_mod.BudgetExceeded):
        spend_mod.check("Manager_1")
