"""
channels_router.py — Slack account linking
==========================================
Endpoints:
  POST   /channels/slack/start-link  → get a 6-digit code to DM the bot
  GET    /channels/my                → list my linked channels
  DELETE /channels/{id}              → unlink

WhatsApp and Telegram were removed (2026-07-09): the WhatsApp sandbox needed
a re-join every 72h and Telegram was never more than a stub, so neither was
usable for a real pilot. Slack is the single messaging channel — the bot
already lives in DMs and channels, and delivery goes through
`api/channel_delivery.py`.

Linking is reversed vs a phone number: a user can't type their Slack ID
(they don't know it), so Nexus issues a code, the user DMs it to the bot, and
the bot fills in their real Slack ID automatically.
"""

import logging
import secrets
import string
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.core import SessionLocal, get_db
from database.models import Employee, ChannelConnection, AuditLog
from api.security import get_current_user

log = logging.getLogger("nexus.channels")

router = APIRouter()


def _now():
    return datetime.now(timezone.utc)


def _generate_code(length: int = 6) -> str:
    """Numeric linking code. (Lived in twilio_client until that module was
    deleted — Slack linking must not depend on a messaging provider.)"""
    return "".join(secrets.choice(string.digits) for _ in range(length))


# ═══════════════════════════════════════════════════════════════
# GET /channels/my
# ═══════════════════════════════════════════════════════════════

@router.get("/my")
def list_my_channels(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conns = db.query(ChannelConnection).filter(
        ChannelConnection.employee_id == current_user.id,
    ).order_by(ChannelConnection.connected_at.desc()).all()

    return [{
        "id":         c.id,
        "platform":   c.platform,
        "identifier": c.platform_user_id,
        "verified":   c.verified,
        "is_primary": c.is_primary,
        "is_active":  c.is_active,
        "connected_at": c.connected_at.isoformat() if c.connected_at else None,
    } for c in conns]


# ═══════════════════════════════════════════════════════════════
# DELETE /channels/{id}
# ═══════════════════════════════════════════════════════════════

@router.delete("/{connection_id}")
def unlink_channel(
    connection_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.query(ChannelConnection).filter(
        ChannelConnection.id          == connection_id,
        ChannelConnection.employee_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found.")

    db.add(AuditLog(
        company_id=current_user.company_id, actor_id=current_user.id,
        action="channel_unlinked", entity_type="channel_connection",
        entity_id=conn.id, old_value={"platform": conn.platform},
    ))
    db.delete(conn)
    db.commit()
    return {"status": "unlinked"}


# ═══════════════════════════════════════════════════════════════
# POST /channels/slack/start-link
# ═══════════════════════════════════════════════════════════════

@router.post("/slack/start-link")
def slack_start_link(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Issue a one-time code; the user DMs it to the Nexus bot, which then
    knows their Slack ID. Creates a pending connection with a placeholder
    identifier that the bot fills in when it sees the code."""
    expires = _now() + timedelta(minutes=10)
    # Pick a code that doesn't collide with any other LIVE pending code, so
    # completing a link (matched by the 6-digit code alone) can never resolve
    # to the wrong person's pending row.
    code = _generate_code()
    for _ in range(20):
        clash = db.query(ChannelConnection).filter(
            ChannelConnection.platform               == "slack",
            ChannelConnection.verification_code      == code,
            ChannelConnection.verified               == False,  # noqa: E712
            ChannelConnection.verification_expires_at > _now(),
        ).first()
        if not clash:
            break
        code = _generate_code()
    placeholder = f"pending_{code}"

    # Clear any old pending slack links for this user
    db.query(ChannelConnection).filter(
        ChannelConnection.employee_id == current_user.id,
        ChannelConnection.platform    == "slack",
        ChannelConnection.verified    == False,  # noqa: E712
    ).delete()

    connection = ChannelConnection(
        company_id              = current_user.company_id,
        employee_id             = current_user.id,
        platform                = "slack",
        platform_user_id        = placeholder,
        verified                = False,
        verification_code       = code,
        verification_expires_at = expires,
        is_active               = True,
    )
    db.add(connection)
    db.commit()

    return {
        "status": "code_generated",
        "code": code,
        "instructions": "DM this code to the Nexus bot in Slack to link your account.",
        "expires_at": expires.isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# Helpers the Slack bot calls
# ═══════════════════════════════════════════════════════════════

def complete_slack_link(slack_user_id: str, code: str) -> dict:
    """Called by slack_bot when a DM looks like a code. Fills in the real
    Slack user ID and marks the connection verified."""
    db = SessionLocal()
    try:
        pending = db.query(ChannelConnection).filter(
            ChannelConnection.platform          == "slack",
            ChannelConnection.verification_code == code,
            ChannelConnection.verified          == False,  # noqa: E712
        ).first()

        if not pending:
            return {"linked": False, "reason": "no_pending_code"}

        if pending.verification_expires_at and pending.verification_expires_at < _now():
            return {"linked": False, "reason": "expired"}

        # If this slack id is already linked elsewhere, unlink it first
        db.query(ChannelConnection).filter(
            ChannelConnection.platform         == "slack",
            ChannelConnection.platform_user_id == slack_user_id,
        ).delete()

        pending.platform_user_id        = slack_user_id
        pending.verified                = True
        # Slack is the only messaging channel, so a fresh link is the primary
        # one — nothing ever set this before, leaving delivery to fall back on
        # arbitrary ordering.
        pending.is_primary              = True
        pending.verification_code       = None
        pending.verification_expires_at = None
        db.commit()

        emp = db.query(Employee).filter(Employee.id == pending.employee_id).first()
        return {"linked": True, "employee_id": pending.employee_id,
                "employee_name": emp.name if emp else ""}
    finally:
        db.close()


def slack_employee_lookup(slack_user_id: str):
    """DB-first lookup: the employee linked to this Slack user ID."""
    db = SessionLocal()
    try:
        conn = db.query(ChannelConnection).filter(
            ChannelConnection.platform         == "slack",
            ChannelConnection.platform_user_id == slack_user_id,
            ChannelConnection.verified         == True,  # noqa: E712
        ).first()
        if not conn:
            return None
        return db.query(Employee).filter(Employee.id == conn.employee_id).first()
    finally:
        db.close()
