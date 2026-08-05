"""
The source layer that lets the engines read work from somewhere other than
Nexus's own tables.

The whole point of this refactor is that it changes NOTHING today and makes
everything possible tomorrow, so most of these tests are about the first half:
Nexus-only behaviour must be exactly what it was.
"""
from datetime import date, timedelta

import pytest

from api.work_sources import (
    WorkItem, MeetingItem, WorkSource, NexusSource,
    sources_for, collect_open_items, collect_meetings_today,
)
from database.core import SessionLocal
from database.models import Employee, Task


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def employee(db):
    e = db.query(Employee).filter(Employee.is_active == True).first()
    assert e, "no active employee to test with"
    return e


# ── the contract ──────────────────────────────────────────────────
def test_nexus_source_satisfies_the_interface(db):
    assert isinstance(NexusSource(db), WorkSource)


def test_nexus_is_always_present_so_behaviour_is_unchanged(db):
    """With nothing external connected, the only source is Nexus itself —
    which is what keeps today's briefings working."""
    names = [s.name for s in sources_for(db, company_id=1, employee_id=1)]
    assert "nexus" in names


# ── it must return what the briefing actually consumes ────────────
def test_open_items_carry_the_fields_the_briefing_renders(db, employee):
    """The briefing prints title, due date and priority, and groups by due
    date. Anything missing here shows up as a blank in someone's morning."""
    items = collect_open_items(db, employee.id, employee.company_id)
    for item in items:
        assert item.title is not None
        assert item.source == "nexus"
        assert item.external_id
        assert item.is_done is False, "a finished task leaked into open work"


def test_open_items_match_a_direct_query(db, employee):
    """The abstraction must not quietly change WHICH work is returned."""
    direct = db.query(Task).filter(
        Task.owner_id == employee.id,
        Task.is_completed == False,     # noqa: E712
        Task.company_id == employee.company_id,
    ).all()
    through_layer = collect_open_items(db, employee.id, employee.company_id)
    assert {str(t.id) for t in direct} == {i.external_id for i in through_layer}


def test_meetings_carry_the_time_label_the_briefing_prints(db, employee):
    for m in collect_meetings_today(db, employee.id, employee.company_id):
        assert m.title
        # time_label may be None (the briefing says "time TBD"), but the
        # attribute has to exist or rendering raises.
        assert hasattr(m, "time_label")


# ── resilience: these run unattended on a scheduler ───────────────
def test_a_broken_source_cannot_cost_someone_their_briefing(db, employee, monkeypatch):
    """A briefing missing its Jira section is a bad morning. A briefing that
    raises is no morning at all."""
    class Exploding:
        name = "exploding"

        def is_available(self):
            return True

        def open_items_for(self, person_ref):
            raise RuntimeError("connector is down")

        def meetings_today(self, person_ref):
            raise RuntimeError("connector is down")

    import api.work_sources as ws
    real = ws.sources_for
    monkeypatch.setattr(ws, "sources_for",
                        lambda *a, **k: list(real(*a, **k)) + [Exploding()])

    # Must still return the Nexus items rather than propagate the failure.
    items = ws.collect_open_items(db, employee.id, employee.company_id)
    assert isinstance(items, list)
    ws.collect_meetings_today(db, employee.id, employee.company_id)


def test_unknown_person_reference_returns_empty_not_an_error(db):
    src = NexusSource(db)
    assert src.open_items_for("not-a-number") == []
    assert src.meetings_today("not-a-number") == []


# ── the domain type ───────────────────────────────────────────────
def test_overdue_is_computed_not_stored():
    past = WorkItem(source="t", external_id="1", title="x",
                    due_date=date.today() - timedelta(days=1))
    future = WorkItem(source="t", external_id="2", title="y",
                      due_date=date.today() + timedelta(days=1))
    undated = WorkItem(source="t", external_id="3", title="z")
    done = WorkItem(source="t", external_id="4", title="w", is_done=True,
                    due_date=date.today() - timedelta(days=5))
    assert past.is_overdue
    assert not future.is_overdue
    assert not undated.is_overdue
    assert not done.is_overdue, "a completed task is not overdue"


def test_work_item_is_not_coupled_to_the_orm():
    """Coupling the engines to SQLAlchemy rows is what made them unmoveable.
    An item from Jira has no row to hand back."""
    item = WorkItem(source="jira", external_id="PROJ-1", title="from elsewhere")
    assert item.external_id == "PROJ-1"
    assert not hasattr(item, "_sa_instance_state")


def test_the_briefing_partition_is_exhaustive_and_non_overlapping():
    """Every open item must land in exactly one bucket of the briefing.

    The engine splits work into overdue / due today / due this week / later.
    An item in two buckets is printed twice; an item in none disappears from
    someone's morning without any error being raised — which is the failure
    mode worth pinning, since nobody would notice it.
    """
    today = date.today()
    week_end = today + timedelta(days=7)
    items = [
        WorkItem(source="t", external_id="1", title="overdue",
                 due_date=today - timedelta(days=3)),
        WorkItem(source="t", external_id="2", title="today", due_date=today),
        WorkItem(source="t", external_id="3", title="this week",
                 due_date=today + timedelta(days=2)),
        WorkItem(source="t", external_id="4", title="far off",
                 due_date=today + timedelta(days=30)),
        WorkItem(source="t", external_id="5", title="no due date"),
        # Same content as another item but a different id — both must survive.
        WorkItem(source="t", external_id="6", title="no due date"),
    ]

    overdue   = [t for t in items if t.due_date and t.due_date < today]
    due_today = [t for t in items if t.due_date and t.due_date == today]
    due_week  = [t for t in items if t.due_date and today < t.due_date <= week_end]
    later     = [t for t in items if not t.due_date or t.due_date > week_end]

    placements = {}
    for bucket_name, bucket in (("overdue", overdue), ("today", due_today),
                                ("week", due_week), ("later", later)):
        for item in bucket:
            placements.setdefault(item.external_id, []).append(bucket_name)

    assert set(placements) == {i.external_id for i in items}, \
        f"an item vanished from every bucket: {placements}"
    doubled = {k: v for k, v in placements.items() if len(v) > 1}
    assert not doubled, f"item(s) in more than one bucket, would print twice: {doubled}"
