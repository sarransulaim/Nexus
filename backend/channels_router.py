"""
channels_router.py — Omnichannel Routing
==========================================
Endpoints:
  POST   /channels/whatsapp/inbound  → Twilio webhook (no auth, signature-verified)
  POST   /channels/link              → start linking a phone (logged-in user)
  POST   /channels/verify            → confirm verification code
  GET    /channels/my                → list my connected channels
  DELETE /channels/{id}              → unlink a channel

Matches the actual schema in models.py:
  ChannelConnection: platform, platform_user_id, platform_phone, verified, ...
  ChannelMessageLog: platform, direction, content, platform_message_id, ...

Architecture:
  - Twilio webhook acks immediately (within 15s timeout)
  - run_orchestrator runs in BackgroundTask
  - Response sent via Twilio API when AI completes
  - Same agent_id as web → conversation continues across channels
"""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.core import SessionLocal, get_db
from database.models import (
    Employee, ChannelConnection, ChannelMessageLog, AuditLog,
)
from api.security import get_current_user
import twilio_client as tw
from event_bus import event_bus

log = logging.getLogger("nexus.channels")

router = APIRouter()


def _now():
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════

class LinkRequest(BaseModel):
    platform:   str = Field(..., description="'whatsapp', 'telegram', 'slack'")
    identifier: str = Field(..., description="phone number / chat ID / etc")


class VerifyRequest(BaseModel):
    platform:   str
    identifier: str
    code:       str


def _agent_id_for(emp: Employee) -> str:
    return "Manager_1" if emp.system_role == "manager" else f"Employee_{emp.id}"


# ═══════════════════════════════════════════════════════════════
# POST /channels/link
# ═══════════════════════════════════════════════════════════════

@router.post("/link")
def link_channel(
    payload: LinkRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start linking. Creates unverified connection, sends a code via the channel."""
    platform = payload.platform.lower()
    if platform not in ("whatsapp", "telegram", "slack"):
        raise HTTPException(status_code=400, detail="Unsupported platform.")

    if platform == "whatsapp":
        # Require an explicit country code: "2605550123" would otherwise be
        # normalized to +260... — Zambia, not Indiana. (Seen in the wild.)
        if not payload.identifier.strip().startswith("+"):
            raise HTTPException(status_code=400,
                                detail="Include the country code with a leading + "
                                       "(e.g. +1 260 555 0123 for a US number).")
        identifier = tw.normalize_phone(payload.identifier)
        if not identifier or len(identifier) < 8:
            raise HTTPException(status_code=400, detail="Invalid phone number.")
    else:
        identifier = payload.identifier.strip()

    existing = db.query(ChannelConnection).filter(
        ChannelConnection.platform         == platform,
        ChannelConnection.platform_user_id == identifier,
    ).first()
    if existing and existing.verified:
        raise HTTPException(status_code=409, detail="This identifier is already linked.")

    code    = tw.generate_verification_code()
    expires = _now() + timedelta(minutes=10)

    if existing:
        existing.verification_code       = code
        existing.verification_expires_at = expires
        existing.employee_id             = current_user.id
        existing.company_id              = current_user.company_id
        connection = existing
    else:
        connection = ChannelConnection(
            company_id              = current_user.company_id,
            employee_id             = current_user.id,
            platform                = platform,
            platform_user_id        = identifier,
            platform_phone          = identifier if platform == "whatsapp" else None,
            verified                = False,
            verification_code       = code,
            verification_expires_at = expires,
            is_active               = True,
        )
        db.add(connection)

    db.commit()
    db.refresh(connection)

    if platform == "whatsapp":
        if not tw.is_configured():
            raise HTTPException(status_code=503, detail="Twilio not configured on server.")
        result = tw.send_verification_code(identifier, code, employee_name=current_user.name)
        if not result.get("success"):
            raise HTTPException(status_code=502,
                                detail=f"Failed to send WhatsApp code: {result.get('error')}")
    elif platform == "telegram":
        import telegram_client as tg
        if not tg.is_configured():
            raise HTTPException(status_code=503, detail="Telegram bot not configured on server.")
        result = tg.send_verification_code(identifier, code, employee_name=current_user.name)
        if not result.get("success"):
            raise HTTPException(status_code=502,
                                detail=f"Failed to send Telegram code: {result.get('error')}. "
                                       f"Make sure you've started the bot with /start first.")

    db.add(AuditLog(
        company_id=current_user.company_id, actor_id=current_user.id,
        action="channel_link_started", entity_type="channel_connection",
        entity_id=connection.id, new_value={"platform": platform, "tail": identifier[-4:]},
    ))
    db.commit()

    return {
        "status": "code_sent",
        "connection_id": connection.id,
        "expires_at": expires.isoformat(),
        "message": f"Verification code sent. Check your {platform}.",
    }


# ═══════════════════════════════════════════════════════════════
# POST /channels/verify
# ═══════════════════════════════════════════════════════════════

@router.post("/verify")
def verify_channel(
    payload: VerifyRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    platform = payload.platform.lower()
    identifier = (tw.normalize_phone(payload.identifier)
                  if platform == "whatsapp" else payload.identifier.strip())

    connection = db.query(ChannelConnection).filter(
        ChannelConnection.platform         == platform,
        ChannelConnection.platform_user_id == identifier,
        ChannelConnection.employee_id      == current_user.id,
    ).first()

    if not connection:
        raise HTTPException(status_code=404, detail="No pending verification.")
    if connection.verified:
        return {"status": "already_verified"}
    if not connection.verification_code or connection.verification_code != payload.code.strip():
        raise HTTPException(status_code=400, detail="Invalid code.")
    if connection.verification_expires_at and connection.verification_expires_at < _now():
        raise HTTPException(status_code=400, detail="Code expired. Request a new one.")

    connection.verified                = True
    connection.verification_code       = None
    connection.verification_expires_at = None
    db.add(AuditLog(
        company_id=current_user.company_id, actor_id=current_user.id,
        action="channel_verified", entity_type="channel_connection",
        entity_id=connection.id, new_value={"platform": platform},
    ))
    db.commit()

    if platform == "whatsapp" and tw.is_configured():
        tw.send_whatsapp(identifier,
                         "Your phone is now linked to Nexus. Send any message to chat with your AI.")
    elif platform == "telegram":
        import telegram_client as tg
        if tg.is_configured():
            tg.send_message(identifier,
                            "Your Telegram is now linked to Nexus. Send any message to chat with your AI.")

    return {"status": "verified", "connection_id": connection.id}


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
# POST /channels/whatsapp/inbound — TWILIO WEBHOOK
# ═══════════════════════════════════════════════════════════════

@router.post("/whatsapp/inbound")
async def whatsapp_inbound(request: Request, background_tasks: BackgroundTasks):
    """
    Twilio POSTs incoming WhatsApp messages here.
    Verifies signature, finds the employee, runs AI in background, replies.
    """
    form = await request.form()
    params = {key: form[key] for key in form.keys()}

    # Verify Twilio signature against every plausible public URL: the one derived
    # from the actual request (honoring proxy/ngrok forwarding headers) AND the
    # configured TWILIO_WEBHOOK_BASE. Twilio signs the exact public URL, which
    # behind a proxy differs from what the app sees — checking a single guessed
    # URL silently rejects every real webhook. Accepting extra candidates is safe
    # (each still needs a valid HMAC signature an attacker can't forge). (#16)
    signature = request.headers.get("X-Twilio-Signature", "")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host  = request.headers.get("x-forwarded-host") or request.headers.get("host")
    query = ("?" + request.url.query) if request.url.query else ""
    candidate_urls = []
    if host:
        candidate_urls.append(f"{proto}://{host}{request.url.path}{query}")
    candidate_urls.append(str(request.url))
    candidate_urls.append(tw.build_webhook_url("/api/v1/channels/whatsapp/inbound"))
    if not tw.verify_signature_multi(candidate_urls, params, signature):
        log.warning(f"⚠️  Invalid Twilio signature. URLs tried: {candidate_urls}")
        raise HTTPException(status_code=403, detail="Invalid signature.")

    from_phone  = tw.normalize_phone(params.get("From", ""))
    body        = params.get("Body", "").strip()
    message_sid = params.get("MessageSid", "")

    log.info(f"📱 WhatsApp inbound from {from_phone}: {body[:80]}")

    if not from_phone or not body:
        return {"status": "ignored"}

    db = SessionLocal()
    try:
        # Idempotency
        if message_sid:
            dup = db.query(ChannelMessageLog).filter(
                ChannelMessageLog.platform_message_id == message_sid,
                ChannelMessageLog.direction           == "inbound",
            ).first()
            if dup:
                return {"status": "duplicate"}

        connection = db.query(ChannelConnection).filter(
            ChannelConnection.platform         == "whatsapp",
            ChannelConnection.platform_user_id == from_phone,
            ChannelConnection.verified         == True,
        ).first()

        if not connection:
            log.info(f"Unknown phone {from_phone} — sending registration prompt")
            tw.send_whatsapp(from_phone,
                             "This number isn't linked to a Nexus account. "
                             "Please link your phone in the Nexus app first.")
            return {"status": "unknown_sender"}

        employee = db.query(Employee).filter(
            Employee.id == connection.employee_id,
            Employee.is_active == True,
        ).first()
        if not employee:
            return {"status": "no_employee"}

        # Log inbound
        db.add(ChannelMessageLog(
            company_id          = connection.company_id,
            employee_id         = connection.employee_id,
            platform            = "whatsapp",
            direction           = "inbound",
            content             = body,
            platform_message_id = message_sid,
            status              = "received",
        ))
        connection.last_message_at = _now()
        db.commit()

        agent_id = _agent_id_for(employee)
        background_tasks.add_task(
            _process_whatsapp_message,
            agent_id=agent_id,
            employee_id=employee.id,
            company_id=connection.company_id,
            from_phone=from_phone,
            body=body,
        )

        try:
            event_bus.emit("message_sent", actor=agent_id,
                           to="orchestrator", kind="whatsapp_inbound")
        except Exception:
            pass

        return {"status": "queued"}
    finally:
        db.close()


def _process_whatsapp_message(agent_id, employee_id, company_id, from_phone, body):
    """Background: run orchestrator, send reply via Twilio, log outbound."""
    from api.claude_orchestrator import run_orchestrator

    log.info(f"🤖 Orchestrator for {agent_id} (WhatsApp)...")
    try:
        ai_response = run_orchestrator(agent_id, body)
    except Exception as e:
        log.error(f"Orchestrator failed: {e}")
        ai_response = "I hit a snag processing that. Try again in a moment."

    if not ai_response:
        ai_response = "Done."

    send_result = tw.send_whatsapp(from_phone, ai_response)

    db = SessionLocal()
    try:
        db.add(ChannelMessageLog(
            company_id          = company_id,
            employee_id         = employee_id,
            platform            = "whatsapp",
            direction           = "outbound",
            content             = ai_response,
            platform_message_id = send_result.get("sid"),
            status              = "sent" if send_result.get("success") else "failed",
        ))
        db.commit()
    except Exception as e:
        log.error(f"Failed to log outbound: {e}")
    finally:
        db.close()

    try:
        event_bus.emit("message_sent", actor=agent_id,
                       to="ws_broadcast", kind="whatsapp_outbound")
    except Exception:
        pass

    log.info(f"✅ WhatsApp roundtrip complete for {agent_id}")


# ═══════════════════════════════════════════════════════════════
# POST /channels/slack/start-link — generate a code to DM the bot
# ═══════════════════════════════════════════════════════════════

@router.post("/slack/start-link")
def slack_start_link(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Slack linking is reversed vs WhatsApp: the user can't type their
    Slack ID (they don't know it). So we generate a code, the user DMs
    it to the bot, and the bot reads their Slack ID automatically.

    This creates a pending connection with a placeholder identifier
    that the bot fills in when it sees the code.
    """
    expires = _now() + timedelta(minutes=10)
    # Pick a code that doesn't collide with any other LIVE pending slack code, so
    # completing a link (matched by the 6-digit code alone) can never resolve to
    # the wrong person's pending row on a collision.
    code = tw.generate_verification_code()
    for _ in range(20):
        clash = db.query(ChannelConnection).filter(
            ChannelConnection.platform               == "slack",
            ChannelConnection.verification_code      == code,
            ChannelConnection.verified               == False,  # noqa: E712
            ChannelConnection.verification_expires_at > _now(),
        ).first()
        if not clash:
            break
        code = tw.generate_verification_code()
    placeholder = f"pending_{code}"

    # Clear any old pending slack links for this user
    db.query(ChannelConnection).filter(
        ChannelConnection.employee_id == current_user.id,
        ChannelConnection.platform    == "slack",
        ChannelConnection.verified    == False,
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


# Helper the Slack bot calls to complete a link from an incoming DM
def complete_slack_link(slack_user_id: str, code: str) -> dict:
    """
    Called by slack_bot when it receives a DM that looks like a code.
    If a pending slack link with this code exists, fill in the real
    Slack user ID and mark verified. Returns {linked: bool, ...}.
    """
    db = SessionLocal()
    try:
        pending = db.query(ChannelConnection).filter(
            ChannelConnection.platform          == "slack",
            ChannelConnection.verification_code == code,
            ChannelConnection.verified          == False,
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
        pending.verification_code       = None
        pending.verification_expires_at = None
        db.commit()

        emp = db.query(Employee).filter(Employee.id == pending.employee_id).first()
        return {"linked": True, "employee_id": pending.employee_id,
                "employee_name": emp.name if emp else ""}
    finally:
        db.close()


def slack_employee_lookup(slack_user_id: str):
    """
    DB-first lookup: find the employee linked to this Slack user ID
    via channel_connections. Returns Employee or None.
    """
    db = SessionLocal()
    try:
        conn = db.query(ChannelConnection).filter(
            ChannelConnection.platform         == "slack",
            ChannelConnection.platform_user_id == slack_user_id,
            ChannelConnection.verified         == True,
        ).first()
        if not conn:
            return None
        return db.query(Employee).filter(Employee.id == conn.employee_id).first()
    finally:
        db.close()

@router.post("/telegram/inbound")
async def telegram_inbound(request: Request, background_tasks: BackgroundTasks):
    """
    Telegram POSTs incoming messages here.
    Verifies the secret token header, finds the employee by chat_id,
    runs the AI in background, replies via Telegram.
    """
    import telegram_client as tg

    # Verify secret token (Telegram sends it in a header)
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not tg.verify_webhook_secret(header_secret):
        log.warning("⚠️  Invalid Telegram webhook secret — rejecting")
        raise HTTPException(status_code=403, detail="Invalid secret.")

    payload = await request.json()

    # Telegram update structure: { update_id, message: { chat: {id}, text, ... } }
    message = payload.get("message") or {}
    chat    = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    body    = (message.get("text") or "").strip()
    update_id = str(payload.get("update_id", ""))

    log.info(f"📨 Telegram inbound from chat {chat_id}: {body[:80]}")

    if not chat_id or not body:
        return {"status": "ignored"}

    db = SessionLocal()
    try:
        # Dedupe on update_id
        if update_id:
            dup = db.query(ChannelMessageLog).filter(
                ChannelMessageLog.platform_message_id == update_id,
                ChannelMessageLog.direction           == "inbound",
                ChannelMessageLog.platform             == "telegram",
            ).first()
            if dup:
                return {"status": "duplicate"}

        connection = db.query(ChannelConnection).filter(
            ChannelConnection.platform         == "telegram",
            ChannelConnection.platform_user_id == chat_id,
            ChannelConnection.verified         == True,
        ).first()

        if not connection:
            log.info(f"Unknown Telegram chat {chat_id} — sending registration prompt")
            tg.send_message(
                chat_id,
                "This Telegram isn't linked to a Nexus account. "
                "Please link it in the Nexus app first.",
            )
            return {"status": "unknown_sender"}

        employee = db.query(Employee).filter(
            Employee.id == connection.employee_id,
            Employee.is_active == True,
        ).first()
        if not employee:
            return {"status": "no_employee"}

        db.add(ChannelMessageLog(
            company_id          = connection.company_id,
            employee_id         = connection.employee_id,
            platform            = "telegram",
            direction           = "inbound",
            content             = body,
            platform_message_id = update_id,
            status              = "received",
        ))
        connection.last_message_at = _now()
        db.commit()

        agent_id = _agent_id_for(employee)
        background_tasks.add_task(
            _process_telegram_message,
            agent_id=agent_id,
            employee_id=employee.id,
            company_id=connection.company_id,
            chat_id=chat_id,
            body=body,
        )

        try:
            event_bus.emit("message_sent", actor=agent_id,
                           to="orchestrator", kind="telegram_inbound")
        except Exception:
            pass

        return {"status": "queued"}
    finally:
        db.close()


def _process_telegram_message(agent_id, employee_id, company_id, chat_id, body):
    """Background: run orchestrator, send reply via Telegram, log outbound."""
    import telegram_client as tg
    from api.claude_orchestrator import run_orchestrator

    log.info(f"🤖 Orchestrator for {agent_id} (Telegram)...")
    try:
        ai_response = run_orchestrator(agent_id, body)
    except Exception as e:
        log.error(f"Orchestrator failed: {e}")
        ai_response = "I hit a snag processing that. Try again in a moment."

    if not ai_response:
        ai_response = "Done."

    send_result = tg.send_message(chat_id, ai_response)

    db = SessionLocal()
    try:
        db.add(ChannelMessageLog(
            company_id          = company_id,
            employee_id         = employee_id,
            platform            = "telegram",
            direction           = "outbound",
            content             = ai_response,
            platform_message_id = str(send_result.get("message_id", "")),
            status              = "sent" if send_result.get("success") else "failed",
        ))
        db.commit()
    except Exception as e:
        log.error(f"Failed to log outbound: {e}")
    finally:
        db.close()

    try:
        event_bus.emit("message_sent", actor=agent_id,
                       to="ws_broadcast", kind="telegram_outbound")
    except Exception:
        pass

    log.info(f"✅ Telegram roundtrip complete for {agent_id}")


# ═══════════════════════════════════════════════════════════════
# POST /channels/telegram/set-webhook — admin helper
# ═══════════════════════════════════════════════════════════════

@router.post("/telegram/set-webhook")
def telegram_set_webhook(
    current_user: Employee = Depends(get_current_user),
):
    """
    Manager calls this once (after starting ngrok) to point Telegram
    at the current webhook URL. Saves doing it via curl.
    """
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager only.")

    import telegram_client as tg
    result = tg.set_webhook()
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=f"setWebhook failed: {result.get('error')}")
    return result