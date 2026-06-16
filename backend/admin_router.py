"""
admin_router.py — Admin Dashboard API
=======================================
Endpoints (all manager-only):

  GET   /admin/metrics       → snapshot of counters + recent events
  GET   /admin/health        → DB, WS, queue health
  GET   /admin/agents        → list active agents + their state
  GET   /admin/recent-errors → recent error events
  WS    /admin/stream        → live event stream (for the circuit board)
"""

import asyncio
import json
import queue
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from database.core import SessionLocal, get_db
from database.models import (
    Employee, Task, AgentMemory, AuditLog, Notification, Meeting,
    Project, Escalation, PeerRequest, UploadedFile,
)
from api.security import get_current_user
from api.ws_manager import notifier
from event_bus import event_bus
import os
import jwt as pyjwt   # PyJWT — already in your stack via security.py

# Single source of truth — use the SAME secret/algorithm as security.py so a
# token issued there always validates here. (These defaults previously diverged,
# which silently broke the admin WS whenever JWT_SECRET was left unset.)
from api.security import JWT_SECRET, JWT_ALGORITHM


def _decode_token(token: str) -> dict:
    """Inline JWT decode — manager-only WS auth for admin stream."""
    return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

log = logging.getLogger("nexus.admin")

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# REST endpoints
# ═══════════════════════════════════════════════════════════════

@router.get("/metrics")
def get_metrics(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """High-level system snapshot. Manager only."""
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")

    snapshot = event_bus.get_snapshot()

    # DB counts
    db_stats = {
        "employees":        db.query(Employee).filter(Employee.company_id == current_user.company_id).count(),
        "active_tasks":     db.query(Task).filter(Task.company_id == current_user.company_id, Task.is_completed == False).count(),
        "completed_tasks":  db.query(Task).filter(Task.company_id == current_user.company_id, Task.is_completed == True).count(),
        "projects":         db.query(Project).filter(Project.company_id == current_user.company_id).count(),
        "meetings":         db.query(Meeting).filter(Meeting.company_id == current_user.company_id).count(),
        "files":            db.query(UploadedFile).filter(UploadedFile.company_id == current_user.company_id).count(),
        "open_escalations": db.query(Escalation).filter(
            Escalation.company_id == current_user.company_id,
            Escalation.status     == "pending",
        ).count(),
        "pending_peer_requests": db.query(PeerRequest).filter(
            PeerRequest.company_id == current_user.company_id,
            PeerRequest.status     == "Pending",
        ).count(),
    }

    # Today's cost from audit log of cost events (best-effort)
    # We rely on the in-memory counter from event_bus
    return {
        "counters":     snapshot["counters"],
        "db":           db_stats,
        "ws":           {
            "active_connections":  notifier.connection_count,
            "connected_employees": notifier.connected_employee_ids,
            "admin_subscribers":   snapshot["subscribers"],
        },
        "recent_events": snapshot["recent_events"][-30:],
    }


@router.get("/health")
def admin_health(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detailed health check. Manager only."""
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")

    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    return {
        "db":              "ok" if db_ok else "error",
        "ws_connections":  notifier.connection_count,
        "admin_subs":      event_bus.get_snapshot()["subscribers"],
        "version":         "3.0.0",
    }


@router.get("/agents")
def list_active_agents(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Show all agents (manager + employees) with last activity."""
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")

    employees = db.query(Employee).filter(
        Employee.company_id == current_user.company_id,
        Employee.is_active  == True,
    ).all()

    result = []
    for emp in employees:
        agent_id = f"Employee_{emp.id}" if emp.system_role != "manager" else "Manager_1"
        memory   = db.query(AgentMemory).filter(
            AgentMemory.agent_id   == agent_id,
            AgentMemory.company_id == current_user.company_id,
        ).first()

        result.append({
            "agent_id":      agent_id,
            "employee_id":   emp.id,
            "name":          emp.name,
            "role":          emp.role,
            "system_role":   emp.system_role,
            "last_login":    emp.last_login.isoformat() if emp.last_login else None,
            "message_count": memory.message_count if memory else 0,
            "last_active":   memory.last_updated.isoformat() if memory and memory.last_updated else None,
            "ws_connected":  emp.id in notifier.connected_employee_ids,
        })

    return result


@router.get("/recent-errors")
def get_recent_errors(
    limit: int = Query(20, le=100),
    current_user: Employee = Depends(get_current_user),
):
    """Recent error events from the bus."""
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")

    snapshot = event_bus.get_snapshot()
    errors   = [e for e in snapshot["recent_events"] if e["type"] == "error"]
    return errors[-limit:]


# ═══════════════════════════════════════════════════════════════
# WebSocket — live event stream
# ═══════════════════════════════════════════════════════════════

@router.websocket("/stream")
async def admin_event_stream(websocket: WebSocket, token: str = Query(...)):
    """
    Live event stream for the admin dashboard.
    Manager auth done via ?token=... since WS can't send headers easily.

    Client sends "ping" periodically, server replies with "pong".
    Server streams every event in real time.
    """
    # Validate token + role before accepting
    try:
        payload = _decode_token(token)
        if payload.get("type") not in (None, "access"):
            await websocket.close(code=4001)
            return
        emp_id = int(payload.get("sub", 0))
    except Exception:
        await websocket.close(code=4001)
        return

    # Lookup employee — must be manager
    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.id == emp_id).first()
        if not emp or emp.system_role != "manager":
            await websocket.close(code=4003)
            return
    finally:
        db.close()

    await websocket.accept()
    q = event_bus.subscribe()

    log.info(f"Admin stream connected for {emp.name} (manager)")

    async def heartbeat():
        """Keeps connection alive — every 25s send a ping."""
        try:
            while True:
                await asyncio.sleep(25)
                try:
                    await websocket.send_text(json.dumps({"type": "ping", "ts": datetime.now(timezone.utc).isoformat()}))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def drain_events():
        """Reads from the bus queue and sends to the WebSocket."""
        try:
            while True:
                try:
                    event = q.get_nowait()
                    await websocket.send_text(json.dumps(event, default=str))
                except queue.Empty:
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"drain_events error: {e}")

    heartbeat_task = asyncio.create_task(heartbeat())
    drain_task     = asyncio.create_task(drain_events())

    try:
        while True:
            # Wait for any client message (we ignore the content)
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"Admin stream error: {e}")
    finally:
        heartbeat_task.cancel()
        drain_task.cancel()
        event_bus.unsubscribe(q)
        log.info(f"Admin stream disconnected for {emp.name}")