"""
peer_requests.py — Peer Request Router (HARDENED)
==================================================
Fix (Bug 8): Added authentication.
Additional: Only the recipient of a request can accept/decline it.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import PeerRequest, Employee, Notification, AuditLog
from api.ws_manager import notifier
from api.security import get_current_user

router = APIRouter()


class RespondPayload(BaseModel):
    action: str   # "Accepted" or "Declined"


async def broadcast_db_update():
    await notifier.broadcast("SYNC_REQUIRED")


@router.post("/{req_id}/respond")
def respond_to_request(
    req_id: int,
    payload: RespondPayload,
    background_tasks: BackgroundTasks,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Accept or decline a peer request.

    FIX (Bug 8): Now requires auth — anyone could previously respond to
    any peer request by guessing the ID.
    Additional: Recipient verification — only the person the request was
    sent to (or a manager) can respond to it.
    """
    if payload.action not in ("Accepted", "Declined", "Completed"):
        raise HTTPException(status_code=400, detail="Action must be 'Accepted', 'Declined', or 'Completed'.")

    req = db.query(PeerRequest).filter(
        PeerRequest.id         == req_id,
        PeerRequest.company_id == current_user.company_id,
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="Peer request not found.")

    # Only the recipient (or manager) can respond
    if current_user.system_role != "manager" and req.recipient_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only respond to requests sent to you.")

    # "Completed" is the only valid transition out of an accepted request;
    # Accepted/Declined are only valid from Pending.
    if payload.action == "Completed":
        if req.status != "Accepted":
            raise HTTPException(status_code=400, detail="Only an accepted request can be completed.")
    elif req.status != "Pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status.lower()}.")

    req.status = payload.action

    # Notify the sender
    db.add(Notification(
        company_id=current_user.company_id,
        recipient_id=req.sender_id,
        type="peer_request_response",
        title=f"Peer Request {payload.action}",
        message=f"{current_user.name} {payload.action.lower()} your request: {req.topic[:80]}",
    ))

    # Audit log
    db.add(AuditLog(
        company_id=current_user.company_id,
        actor_id=current_user.id,
        action=f"peer_request_{payload.action.lower()}",
        entity_type="peer_request",
        entity_id=req.id,
    ))

    db.commit()
    background_tasks.add_task(broadcast_db_update)

    return {"status": "success", "message": f"Request {payload.action.lower()}."}


@router.get("/")
def list_my_peer_requests(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all peer requests where the current user is sender or recipient."""
    requests = db.query(PeerRequest).filter(
        PeerRequest.company_id == current_user.company_id,
        (PeerRequest.sender_id == current_user.id) | (PeerRequest.recipient_id == current_user.id),
    ).all()

    return [
        {
            "id":            r.id,
            "task_id":       r.task_id,
            "sender_id":     r.sender_id,
            "recipient_id":  r.recipient_id,
            "topic":         r.topic,
            "status":        r.status,
            "ai_negotiated": r.ai_negotiated,
            "direction":     "sent" if r.sender_id == current_user.id else "received",
            "created_at":    r.created_at,
        }
        for r in requests
    ]