"""
work_sources.py — where Nexus reads work from
=============================================
The briefing, digest and drift engines were written against Nexus's OWN
tables: `db.query(Task)`, `db.query(Meeting)`. That is the single reason the
product doesn't fit a company that already runs on Jira — using it means
migrating your work into Nexus first, which no corporate team will ever do.

The engines themselves are fine. They were pointed at the wrong data.

This module puts an interface between them and the data, so the same proven
logic can run over work that lives somewhere else entirely. Nothing about the
briefing changes; where the tasks come from does.

    WorkItem      — one piece of work, normalised across systems
    MeetingItem   — one meeting, normalised
    WorkSource    — the interface every backend implements
    NexusSource   — today's behaviour, reading Nexus's own tables

A Jira/GitHub source implements the same interface and slots in beside it. The
consumer never learns which one it got.

Design notes worth keeping:

* `WorkItem` is deliberately NOT the ORM model. Coupling the engines to
  SQLAlchemy rows is what made them unmoveable in the first place, and an
  external item has no row to hand back.
* Identity is per-source. A person is `sulaim` in Slack, `SS-1042` in Jira and
  employee 2 here; each source resolves its own, so no global identity scheme
  has to exist before any of this works.
* Every source must degrade to empty rather than raise. A briefing that is
  missing its Jira section is a bad morning; a briefing that throws is no
  morning at all, and these run unattended on a scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable


@dataclass
class WorkItem:
    """One piece of work, in the shape the engines actually consume.

    Fields exist because a consumer needs them — `priority` and `due_date` are
    here because the briefing groups by them, not for completeness.
    """
    source: str                      # "nexus", "jira", "github" …
    external_id: str                 # native id: task pk, "PROJ-123", issue number
    title: str
    is_done: bool = False
    status: str | None = None        # the source's own word: "In Review", "closed"
    assignee_name: str | None = None
    assignee_ref: str | None = None  # source-native identity, NOT a Nexus id
    due_date: date | None = None
    priority: str | None = None
    project: str | None = None
    url: str | None = None           # deep link back to the system of record
    updated_at: datetime | None = None
    blocked_by: list[str] = field(default_factory=list)

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_date and not self.is_done and self.due_date < date.today())


@dataclass
class MeetingItem:
    source: str
    external_id: str
    title: str
    when: datetime | None = None
    day: date | None = None
    # How the source words the time ("2 PM", "09:30"). Kept as text because
    # that is what people wrote and what the briefing prints; a parsed datetime
    # would force a timezone guess on a string that may not carry one.
    time_label: str | None = None
    attendees: list[str] = field(default_factory=list)
    join_url: str | None = None
    status: str | None = None


@runtime_checkable
class WorkSource(Protocol):
    """Where a person's work lives.

    Implementations must never raise: return an empty list instead. These run
    on a scheduler with nobody watching.
    """

    name: str

    def is_available(self) -> bool:
        """Can this source be queried right now (configured, reachable, authorised)?"""
        ...

    def open_items_for(self, person_ref: str) -> list[WorkItem]:
        """Unfinished work assigned to this person."""
        ...

    def meetings_today(self, person_ref: str) -> list[MeetingItem]:
        """This person's meetings for today."""
        ...


class NexusSource:
    """Today's behaviour: Nexus's own tables.

    Kept as a first-class source rather than a legacy path. A company with no
    external tracker still wants a briefing, and this is the reference
    implementation every other source is checked against.
    """

    name = "nexus"

    def __init__(self, db, company_id: int = 1):
        self._db = db
        self._company_id = company_id

    def is_available(self) -> bool:
        return self._db is not None

    def open_items_for(self, person_ref: str) -> list[WorkItem]:
        from database.models import Task

        try:
            employee_id = int(person_ref)
        except (TypeError, ValueError):
            return []

        try:
            rows = self._db.query(Task).filter(
                Task.owner_id == employee_id,
                Task.is_completed == False,          # noqa: E712
                Task.company_id == self._company_id,
            ).all()
        except Exception:
            return []

        return [self._to_item(t) for t in rows]

    def meetings_today(self, person_ref: str) -> list[MeetingItem]:
        from database.models import Employee

        try:
            employee_id = int(person_ref)
        except (TypeError, ValueError):
            return []

        today = date.today()
        try:
            employee = self._db.query(Employee).filter(Employee.id == employee_id).first()
            if not employee:
                return []
            out = []
            for m in employee.meetings:
                if m.scheduled_date == today and (m.status or "scheduled") != "cancelled":
                    out.append(MeetingItem(
                        source=self.name,
                        external_id=str(m.id),
                        title=m.topic or "Meeting",
                        day=m.scheduled_date,
                        time_label=m.scheduled_time,
                        join_url=m.location,
                        status=m.status,
                    ))
            return out
        except Exception:
            return []

    def _to_item(self, task) -> WorkItem:
        owner_name = None
        try:
            owner_name = task.owner.name if task.owner else None
        except Exception:
            pass
        return WorkItem(
            source=self.name,
            external_id=str(task.id),
            title=task.title or "",
            is_done=bool(task.is_completed),
            status=getattr(task, "status", None),
            assignee_name=owner_name,
            assignee_ref=str(task.owner_id) if task.owner_id else None,
            due_date=task.due_date,
            priority=getattr(task, "priority", None),
            project=(task.project.name if getattr(task, "project", None) else None),
            updated_at=getattr(task, "updated_at", None),
        )


def sources_for(db, company_id: int = 1, employee_id: int | None = None) -> list[WorkSource]:
    """Every source that can answer for this person right now.

    Nexus's own tables always come first so behaviour is unchanged when nothing
    external is connected. External sources append as they're wired up; a
    source that isn't available is simply absent, never an error.
    """
    active: list[WorkSource] = [NexusSource(db, company_id)]

    try:
        from api.mcp_work_source import mcp_sources_for
        active.extend(mcp_sources_for(db, company_id=company_id, employee_id=employee_id))
    except ImportError:
        pass        # adapter not built yet — Nexus-only, exactly as before
    except Exception:
        pass        # a broken connector must not cost someone their briefing

    return [s for s in active if s.is_available()]


def collect_open_items(db, employee_id: int, company_id: int = 1) -> list[WorkItem]:
    """This person's open work, from everywhere Nexus can see."""
    items: list[WorkItem] = []
    for source in sources_for(db, company_id, employee_id):
        try:
            items.extend(source.open_items_for(str(employee_id)))
        except Exception:
            continue
    return items


def collect_meetings_today(db, employee_id: int, company_id: int = 1) -> list[MeetingItem]:
    meetings: list[MeetingItem] = []
    for source in sources_for(db, company_id, employee_id):
        try:
            meetings.extend(source.meetings_today(str(employee_id)))
        except Exception:
            continue
    return meetings
