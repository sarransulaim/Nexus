"""
attention.py — "Needs your decision": the manager's action queue.
=================================================================
One SQL snapshot of everything waiting on a human right now — drifted
interface contracts, queued outward actions, open escalations, work blocked
by an unfinished dependency, and unassigned tasks.

Deliberately ZERO AI calls: these are facts a query answers, so checking the
org costs nothing. The AI is for reasoning ("who should take this?"), not for
counting. Manager sees the company; a team lead sees only their own team.
"""
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import (
    Employee, Task, Contract, ApprovalRequest, Escalation, TaskDependency,
)
from api.security import get_current_user

router = APIRouter()


@router.get("/")
def attention(db: Session = Depends(get_db),
              current_user: Employee = Depends(get_current_user)):
    company_id = current_user.company_id
    is_manager = current_user.system_role == "manager"

    # Scope: managers see the whole company; team leads only their own team.
    scope_ids = None
    if not is_manager:
        if current_user.system_role != "team_lead" or not current_user.team_id:
            return {"scope": "none", "items": {}, "total": 0}
        scope_ids = {e.id for e in db.query(Employee).filter(
            Employee.team_id == current_user.team_id,
            Employee.is_active == True).all()}      # noqa: E712
        scope_ids.add(current_user.id)

    names = {e.id: e.name for e in db.query(Employee).filter(
        Employee.company_id == company_id).all()}

    def in_scope(owner_id):
        return scope_ids is None or owner_id in scope_ids

    # ── 1. Interface contracts that drifted (the coordination layer's output)
    drift = []
    for c in db.query(Contract).filter(
            Contract.company_id == company_id,
            Contract.status.in_(["at_risk", "broken"])).all():
        producer, consumer = c.producer, c.consumer
        if not producer or not consumer:
            continue
        if not (in_scope(producer.owner_id) or in_scope(consumer.owner_id)):
            continue
        drift.append({
            "id": c.id, "name": c.name, "status": c.status,
            "producer": producer.title, "producer_owner": names.get(producer.owner_id, "Unassigned"),
            "consumer": consumer.title, "consumer_owner": names.get(consumer.owner_id, "Unassigned"),
        })

    # ── 2. Queued outward actions + other approvals (manager-only gate)
    approvals = []
    if is_manager:
        for a in db.query(ApprovalRequest).filter(
                ApprovalRequest.company_id == company_id,
                ApprovalRequest.status == "pending"
        ).order_by(ApprovalRequest.created_at.desc()).limit(8).all():
            p = a.payload or {}
            if a.action_type == "send_email":
                detail = f"to {p.get('to', '?')} — {p.get('subject', '')}"
            elif a.action_type == "create_calendar_event":
                detail = f"{p.get('title', 'event')} — invites {', '.join(p.get('attendee_emails', []) or [])}"
            else:
                detail = str(p.get("summary") or p.get("contract_name") or "")[:120]
            approvals.append({"id": a.id, "type": a.action_type,
                              "by": a.requested_by, "detail": detail})

    # ── 3. Open escalations
    escalations = []
    for e in db.query(Escalation).filter(
            Escalation.company_id == company_id,
            Escalation.status == "pending"
    ).order_by(Escalation.created_at.desc()).limit(8).all():
        frm = str(e.from_agent_id or "")
        fid = None
        if frm.startswith("Employee_"):
            try:
                fid = int(frm.split("_", 1)[1])
            except ValueError:
                pass
        if scope_ids is not None and fid not in scope_ids:
            continue
        escalations.append({"id": e.id, "from": names.get(fid, frm),
                            "reason": (e.reason or "")[:180]})

    # ── 4. Work blocked by an unfinished dependency
    blocked = []
    deps = db.query(TaskDependency).all()
    if deps:
        ids = {d.task_id for d in deps} | {d.depends_on_id for d in deps}
        tasks = {t.id: t for t in db.query(Task).filter(Task.id.in_(ids)).all()}
        for d in deps:
            t, up = tasks.get(d.task_id), tasks.get(d.depends_on_id)
            if not t or not up or t.is_completed or up.is_completed:
                continue
            if not in_scope(t.owner_id):
                continue
            blocked.append({
                "id": t.id, "title": t.title, "owner": names.get(t.owner_id, "Unassigned"),
                "waiting_on": up.title, "waiting_owner": names.get(up.owner_id, "Unassigned"),
            })
    blocked = blocked[:8]

    # ── 5. Unassigned open work (nobody owns it → nobody does it)
    unassigned = []
    if is_manager:
        for t in db.query(Task).filter(
                Task.company_id == company_id,
                Task.owner_id.is_(None),
                Task.is_completed == False        # noqa: E712
        ).limit(8).all():
            unassigned.append({"id": t.id, "title": t.title,
                               "due": str(t.due_date) if t.due_date else None,
                               "priority": t.priority})

    items = {"drift": drift[:8], "approvals": approvals, "escalations": escalations,
             "blocked": blocked, "unassigned": unassigned}
    return {
        "scope": "company" if is_manager else "team",
        "items": items,
        "total": sum(len(v) for v in items.values()),
        "as_of": str(date.today()),
    }
