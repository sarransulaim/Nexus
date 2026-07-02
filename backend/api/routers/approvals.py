"""
approvals.py — Agent action approvals REST API (manager-only)
=============================================================
Backs the Approvals page. Agents create ApprovalRequest rows for high-impact
actions; a manager reviews them here. Approving/rejecting records the reviewer
and timestamp (executing the stored payload is a separate, later step).
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import ApprovalRequest, Employee, Notification
from api.security import require_manager

router = APIRouter()


class ReviewNote(BaseModel):
    note: str = ""


# ── Outward-action execution ─────────────────────────────────────
# send_email / create_calendar_event are HARD-GATED: the AI only queues them
# as ApprovalRequests; the real side effect happens HERE, when a human clicks
# Approve. This endpoint is the single place gated actions execute.

def _execute_outward(a: ApprovalRequest, db: Session) -> tuple[bool, str]:
    """Executes an approved outward action. Returns (ok, result_message)."""
    p = a.payload or {}
    try:
        if a.action_type == "send_email":
            from api.google_services import send_email
            result = send_email(p["employee_id"], p["to"], p["subject"], p["body"], db)
            return ("✅" in result, result)
        if a.action_type == "create_calendar_event":
            from api.google_services import create_calendar_event
            result = create_calendar_event(
                employee_id=p["employee_id"],
                title=p["title"],
                start_time=p["start_time"],
                end_time=p["end_time"],
                description=p.get("description", ""),
                attendee_emails=p.get("attendee_emails", []),
                db=db,
            )
            return ("✅" in result, result)
        return (True, "")   # non-outward action types: approval is just a status flip
    except Exception as e:
        return (False, f"Execution error: {type(e).__name__}")


def _notify_requester(a: ApprovalRequest, message: str, db: Session):
    """Tells the requesting user what happened to their queued action."""
    recipient_id = None
    requested_by = a.requested_by or ""
    if requested_by.startswith("Employee_"):
        try:
            recipient_id = int(requested_by.split("_", 1)[1])
        except ValueError:
            pass
    elif requested_by.startswith("Manager"):
        mgr = db.query(Employee).filter(
            Employee.company_id == a.company_id,
            Employee.system_role == "manager",
        ).first()
        recipient_id = mgr.id if mgr else None
    if recipient_id is None:
        return
    db.add(Notification(
        company_id=a.company_id, recipient_id=recipient_id, type="approval",
        title="Approval update", message=message,
        entity_type="approval", entity_id=a.id,
    ))


@router.get("/")
def list_approvals(status: str = "pending", db: Session = Depends(get_db),
                   current_user: Employee = Depends(require_manager)):
    q = db.query(ApprovalRequest).filter(ApprovalRequest.company_id == current_user.company_id)
    if status != "all":
        q = q.filter(ApprovalRequest.status == status)
    rows = q.order_by(ApprovalRequest.created_at.desc()).all()
    return {"approvals": [{
        "id": a.id,
        "action_type": a.action_type,
        "requested_by": a.requested_by,
        "payload": a.payload,
        "status": a.status,
        "reviewer_note": a.reviewer_note,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in rows]}


def _review(approval_id: int, new_status: str, note: str, db: Session, reviewer: Employee):
    a = db.query(ApprovalRequest).filter(
        ApprovalRequest.id == approval_id,
        ApprovalRequest.company_id == reviewer.company_id,
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    if a.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {a.status}.")

    result_msg = ""
    if new_status == "approved":
        ok, result_msg = _execute_outward(a, db)
        if not ok:
            # Execution failed — record it honestly. The action did NOT happen.
            a.status = "failed"
            a.reviewer_id = reviewer.id
            a.reviewer_note = (note + " | " if note else "") + result_msg
            a.reviewed_at = datetime.now(timezone.utc)
            _notify_requester(a, f"Your queued {a.action_type} (#{a.id}) was approved but FAILED to execute: {result_msg}", db)
            db.commit()
            return {"id": a.id, "status": a.status, "result": result_msg}

    a.status = new_status
    a.reviewer_id = reviewer.id
    a.reviewer_note = (note + " | " if note and result_msg else note) + (result_msg or "")
    a.reviewed_at = datetime.now(timezone.utc)
    if new_status == "approved" and a.action_type in ("send_email", "create_calendar_event"):
        _notify_requester(a, f"Approved & sent: your queued {a.action_type} (#{a.id}) went out.", db)
    elif new_status == "rejected":
        _notify_requester(a, f"Rejected: your queued {a.action_type} (#{a.id}) was not executed."
                             + (f" Note: {note}" if note else ""), db)
    db.commit()

    # Nudge connected dashboards so the requester's notification badge updates live.
    try:
        from api.claude_orchestrator import _broadcast_sync
        _broadcast_sync()
    except Exception:
        pass
    return {"id": a.id, "status": a.status, "result": result_msg}


@router.post("/{approval_id}/approve")
def approve(approval_id: int, payload: ReviewNote, db: Session = Depends(get_db),
            current_user: Employee = Depends(require_manager)):
    return _review(approval_id, "approved", payload.note, db, current_user)


@router.post("/{approval_id}/reject")
def reject(approval_id: int, payload: ReviewNote, db: Session = Depends(get_db),
           current_user: Employee = Depends(require_manager)):
    return _review(approval_id, "rejected", payload.note, db, current_user)
