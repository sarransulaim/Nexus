"""
chat_router.py — Team Chat (Phase 1: spine + summary)
======================================================
Project-scoped team chat. People on the same project get a channel and
talk in real time. One AI feature for now: /summarize (local LLM catch-up).

Deliberately scoped: NO personal-agent-on-behalf yet (that comes after
RAG + MCP). This is the foundation everything else sits on.

Routes:
  POST   /chat/ensure-project-channel   → create/get the channel for a project
  GET    /chat/my-channels/{employee_id} → channels this employee is in
  GET    /chat/{channel_id}/messages     → message history (+ marks read)
  POST   /chat/{channel_id}/send         → post a message (broadcasts live)
  POST   /chat/{channel_id}/summarize    → local-LLM TL;DR of recent discussion

Auth follows the app convention: employee_id passed by the caller (the
frontend already holds currentUser.dbId), same as the notifications router.
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc, or_

from database.core import get_db
from database.models import (
    Channel, ChannelMember, Message, Employee, Project, project_members,
)
from api.ws_manager import notifier
from api.security import get_current_user

router = APIRouter()

OLLAMA_MODEL = "qwen2.5:7b"


def _channel_for_member(db: Session, channel_id: int, current_user: Employee) -> Channel:
    """Fetch a channel the caller is allowed to use: same company, and (unless a
    manager) the caller must be a member. Prevents reading/posting in arbitrary
    channels by guessing the id."""
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel or channel.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Channel not found")
    if current_user.system_role != "manager":
        member = db.query(ChannelMember).filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.employee_id == current_user.id,
        ).first()
        if not member:
            raise HTTPException(status_code=403, detail="You are not a member of this channel.")
    return channel


def _broadcast_chat_async(channel_id: int, payload: dict, exclude_id: int = None):
    """Fire-and-forget live broadcast of a chat payload to a channel's room.
    Safe to call from any thread (scheduler job or sync handler) — mirrors the
    orchestrator's _broadcast_sync pattern."""
    import threading

    def _run():
        try:
            import asyncio
            asyncio.run(notifier.send_chat_message(channel_id, payload, sender_id=exclude_id))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def post_ai_message(db: Session, channel, content: str, *, message_type: str = "ai",
                    ai_agent_id: str = "Nexus", reply_to_id: int = None,
                    index: bool = True, broadcast_exclude_id: int = None):
    """
    The single primitive for the AI to *speak* in a channel: persist an
    AI-authored message, broadcast it live to the room, and index it into the
    knowledge base so the AI can recall its own digests later.

    Fixes the gap where AI messages were saved but never broadcast (so only the
    triggering user ever saw them). Used by /summarize and the daily digest.
    """
    msg = Message(
        channel_id=channel.id,
        sender_id=None,
        content=content,
        message_type=message_type,
        ai_agent_id=ai_agent_id,
        reply_to_id=reply_to_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    _broadcast_chat_async(channel.id, _msg_dict(msg, ai_agent_id), exclude_id=broadcast_exclude_id)

    if index and content and len(content) >= 25:
        try:
            import rag
            rag.index_async(channel.company_id, "message", msg.id, content,
                            meta={"channel_id": channel.id, "sender": ai_agent_id})
        except Exception:
            pass
    return msg


# ── helpers ──────────────────────────────────────────────────────────

def _msg_dict(m: Message, sender_name: str = None) -> dict:
    return {
        "id":           m.id,
        "channel_id":   m.channel_id,
        "sender_id":    m.sender_id,
        "sender_name":  sender_name,
        "content":      m.content,
        "message_type": m.message_type,
        "ai_agent_id":  m.ai_agent_id,
        "created_at":   m.created_at.isoformat() if m.created_at else None,
    }


def _name_map(db: Session, company_id: int) -> dict:
    return {e.id: e.name for e in db.query(Employee).filter(Employee.company_id == company_id).all()}


# ── ensure a project has a channel + members synced ──────────────────

@router.post("/ensure-project-channel")
def ensure_project_channel(
    project_id: int = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Get (or create) the chat channel for a project, and make sure every
    project member is a channel member. Idempotent — safe to call on open.
    """
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.company_id == current_user.company_id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    channel = (
        db.query(Channel)
          .filter(Channel.project_id == project_id, Channel.type == "project")
          .first()
    )
    if not channel:
        channel = Channel(
            company_id=project.company_id,
            name=project.name,
            description=f"Team channel for {project.name}",
            type="project",
            project_id=project.id,
            created_by=project.created_by,
        )
        db.add(channel)
        db.flush()

    # Sync members: everyone on the project should be in the channel
    member_rows = db.execute(
        project_members.select().where(project_members.c.project_id == project_id)
    ).fetchall()
    project_member_ids = {r.employee_id for r in member_rows}
    # include the project creator/manager too
    if project.created_by:
        project_member_ids.add(project.created_by)

    existing = {
        cm.employee_id
        for cm in db.query(ChannelMember).filter(ChannelMember.channel_id == channel.id).all()
    }
    for emp_id in project_member_ids - existing:
        db.add(ChannelMember(channel_id=channel.id, employee_id=emp_id))

    db.commit()
    db.refresh(channel)
    return {
        "channel_id":  channel.id,
        "name":        channel.name,
        "project_id":  channel.project_id,
        "member_count": len(project_member_ids),
    }


# ── list channels for an employee ────────────────────────────────────

@router.get("/my-channels/{employee_id}")
def my_channels(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.system_role != "manager" and current_user.id != employee_id:
        raise HTTPException(status_code=403, detail="You can only list your own channels.")
    rows = (
        db.query(Channel)
          .join(ChannelMember, ChannelMember.channel_id == Channel.id)
          .filter(ChannelMember.employee_id == employee_id,
                  Channel.is_archived == False)
          .all()
    )
    # per-channel last_read_at for this member, to compute unread counts
    last_read = {
        m.channel_id: m.last_read_at
        for m in db.query(ChannelMember).filter(ChannelMember.employee_id == employee_id).all()
    }
    out = []
    for c in rows:
        last = (
            db.query(Message)
              .filter(Message.channel_id == c.id, Message.is_deleted == False)
              .order_by(desc(Message.created_at))
              .first()
        )
        # unread = messages from others (incl. the AI, whose sender_id is NULL) since last read
        uq = db.query(Message).filter(
            Message.channel_id == c.id,
            Message.is_deleted == False,
            or_(Message.sender_id != employee_id, Message.sender_id.is_(None)),
        )
        lr = last_read.get(c.id)
        if lr:
            uq = uq.filter(Message.created_at > lr)
        out.append({
            "channel_id":   c.id,
            "name":         c.name,
            "type":         c.type,
            "project_id":   c.project_id,
            "last_message": last.content[:80] if last else None,
            "last_at":      last.created_at.isoformat() if last and last.created_at else None,
            "unread":       uq.count(),
        })
    return {"channels": out}


# ── message history (and mark read) ──────────────────────────────────

@router.get("/{channel_id}/messages")
def get_messages(
    channel_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    channel = _channel_for_member(db, channel_id, current_user)
    employee_id = current_user.id   # mark-read tracked for the authenticated caller

    names = _name_map(db, channel.company_id)
    msgs = (
        db.query(Message)
          .filter(Message.channel_id == channel_id, Message.is_deleted == False)
          .order_by(desc(Message.created_at))
          .limit(limit)
          .all()
    )
    msgs = list(reversed(msgs))  # oldest → newest for display

    # update last_read for this member
    if employee_id:
        from datetime import datetime, timezone
        cm = (
            db.query(ChannelMember)
              .filter(ChannelMember.channel_id == channel_id,
                      ChannelMember.employee_id == employee_id)
              .first()
        )
        if cm:
            cm.last_read_at = datetime.now(timezone.utc)
            db.commit()

    return {
        "channel": {"id": channel.id, "name": channel.name, "type": channel.type},
        "messages": [
            _msg_dict(m, names.get(m.sender_id) if m.sender_id else (m.ai_agent_id or "Nexus"))
            for m in msgs
        ],
    }


# ── send a message ───────────────────────────────────────────────────

@router.post("/{channel_id}/send")
async def send_message(
    channel_id: int,
    content: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    channel = _channel_for_member(db, channel_id, current_user)
    employee_id = current_user.id   # SECURITY: sender derived from token, not the body

    content = (content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Empty message")

    msg = Message(
        channel_id=channel_id,
        sender_id=employee_id,
        content=content,
        message_type="text",
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    sender = db.query(Employee).filter(Employee.id == employee_id).first()
    payload = _msg_dict(msg, sender.name if sender else None)

    # Auto-ingest substantive human messages into the knowledge base (non-blocking),
    # so the AI can recall what the team discussed. Skip short noise ("ok", "thanks").
    try:
        if len(content) >= 25:
            import rag
            rag.index_async(
                channel.company_id, "message", msg.id, content,
                meta={"channel_id": channel_id, "sender": sender.name if sender else None},
            )
    except Exception:
        pass

    # Make sure the sender is in the WS room (so others get it), then broadcast
    await notifier.join_channel(employee_id, channel_id)
    await notifier.send_chat_message(channel_id, payload, sender_id=employee_id)

    return payload


# ── /summarize — local LLM catch-up ──────────────────────────────────

@router.post("/{channel_id}/summarize")
def summarize_channel(
    channel_id: int,
    limit: int = Body(40, embed=True),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Summarize recent channel discussion using the LOCAL LLM (qwen2.5:7b).
    Free, private (chat stays on the machine), good enough for catch-up.
    """
    channel = _channel_for_member(db, channel_id, current_user)

    names = _name_map(db, channel.company_id)
    msgs = (
        db.query(Message)
          .filter(Message.channel_id == channel_id, Message.is_deleted == False)
          .order_by(desc(Message.created_at))
          .limit(limit)
          .all()
    )
    msgs = list(reversed(msgs))
    if len(msgs) < 2:
        return {"summary": "Not enough discussion yet to summarize."}

    transcript = "\n".join(
        f"{names.get(m.sender_id, m.ai_agent_id or 'Nexus')}: {m.content}"
        for m in msgs
    )

    system = (
        "You summarize a team chat for someone catching up. Be brief and concrete. "
        "Give: (1) a 1-2 sentence overview, (2) key points or decisions as short bullets, "
        "(3) any open questions or action items. Do not invent anything not in the chat."
    )
    prompt = f"Summarize this team channel discussion:\n\n{transcript}"

    # Import the SAME client the rest of the app uses (api.ollama_client),
    # falling back to the root module if needed.
    try:
        try:
            from api.ollama_client import OllamaClient
        except ImportError:
            from ollama_client import OllamaClient
        client = OllamaClient()
        # Longer timeout: the 7b model can be slow on first load.
        client.timeout = max(getattr(client, "timeout", 60) or 60, 120)
        summary = client.generate(model=OLLAMA_MODEL, prompt=prompt, system=system, temperature=0.3)
        if not summary:
            return {"summary": "Couldn't generate a summary right now — try again in a moment."}
    except Exception as e:
        # Surface the REAL error to the server log so we can see what failed,
        # but keep the user message clean.
        print(f"⚠️  Chat summary failed: {type(e).__name__}: {e}")
        return {"summary": f"Summary unavailable right now ({type(e).__name__}). Check the backend log for details."}

    # Persist + broadcast the summary as an AI message — teammates now see it
    # live too, and it's indexed into the knowledge base. (The clicker already
    # has it locally, so exclude them from the live echo to avoid a double.)
    post_ai_message(db, channel, summary, ai_agent_id="Nexus",
                    broadcast_exclude_id=current_user.id)

    return {"summary": summary}