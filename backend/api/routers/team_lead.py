"""
team_lead.py — deterministic team overview for TEAM LEADS.
==========================================================
Backs the lead's "My Team" dashboard: one cheap SQL snapshot per load, so the
lead sees workload/overdue/escalations at a glance instead of burning AI calls
on questions a table answers. Strictly scoped to the lead's own team — same
boundary the AI tool tier enforces.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import Employee, Task, Escalation, Meeting
from api.security import get_current_user

router = APIRouter()


def _from_agent_name(agent_id: str, names: dict) -> str:
    s = str(agent_id or "")
    if s.startswith("Employee_"):
        try:
            return names.get(int(s.split("_", 1)[1]), s)
        except ValueError:
            pass
    return s


@router.get("/overview")
def team_overview(db: Session = Depends(get_db),
                  current_user: Employee = Depends(get_current_user)):
    if current_user.system_role != "team_lead" or not current_user.team_id:
        raise HTTPException(status_code=403, detail="Team leads only.")

    members = db.query(Employee).filter(
        Employee.team_id == current_user.team_id,
        Employee.is_active == True,          # noqa: E712
        Employee.system_role != "manager",
    ).all()
    ids = [m.id for m in members]
    names = {m.id: m.name for m in members}
    today = date.today()
    soon = today + timedelta(days=7)

    tasks = db.query(Task).filter(Task.owner_id.in_(ids)).all() if ids else []

    def _overdue(t):
        return (not t.is_completed) and t.due_date and t.due_date < today

    def _due_soon(t):
        return (not t.is_completed) and t.due_date and today <= t.due_date <= soon

    per_member = []
    for m in members:
        mine = [t for t in tasks if t.owner_id == m.id]
        per_member.append({
            "id": m.id, "name": m.name, "role": m.role,
            "is_lead": m.id == current_user.id,
            "open": sum(1 for t in mine if not t.is_completed),
            "overdue": sum(1 for t in mine if _overdue(t)),
            "done": sum(1 for t in mine if t.is_completed),
        })
    per_member.sort(key=lambda r: (-r["open"], r["name"]))

    total = len(tasks)
    done = sum(1 for t in tasks if t.is_completed)
    overdue_tasks = sorted((t for t in tasks if _overdue(t)), key=lambda t: t.due_date)
    due_soon_tasks = sorted((t for t in tasks if _due_soon(t)), key=lambda t: t.due_date)

    escalations = (db.query(Escalation).filter(
        Escalation.status == "pending",
        Escalation.from_agent_id.in_([f"Employee_{i}" for i in ids]),
    ).order_by(Escalation.created_at.desc()).limit(5).all()) if ids else []

    meetings = [m for m in db.query(Meeting)
                .filter(Meeting.scheduled_date.isnot(None), Meeting.scheduled_date >= today)
                .order_by(Meeting.scheduled_date).all()
                if any(a.id in ids for a in m.attendees)][:5]

    return {
        "team": current_user.team or "My team",
        "stats": {
            "open": total - done,
            "overdue": len(overdue_tasks),
            "due_soon": len(due_soon_tasks),
            "completion_pct": round(done * 100 / total, 1) if total else 0.0,
        },
        "members": per_member,
        "overdue": [{"id": t.id, "title": t.title, "owner": names.get(t.owner_id, "?"),
                     "due": str(t.due_date), "priority": t.priority} for t in overdue_tasks[:8]],
        "due_soon": [{"id": t.id, "title": t.title, "owner": names.get(t.owner_id, "?"),
                      "due": str(t.due_date), "priority": t.priority} for t in due_soon_tasks[:8]],
        "escalations": [{"id": e.id, "from": _from_agent_name(e.from_agent_id, names),
                         "reason": (e.reason or "")[:160]} for e in escalations],
        # `location` can hold a Google Meet link — a joinable credential. A lead
        # sees the link only for meetings they're actually in; for the rest of
        # the team's meetings they see that it exists, not how to join.
        "meetings": [{"id": m.id, "topic": m.topic, "date": str(m.scheduled_date),
                      "time": m.scheduled_time,
                      "location": (m.location
                                   if any(a.id == current_user.id for a in m.attendees)
                                   or m.created_by == current_user.id
                                   else None)} for m in meetings],
    }
