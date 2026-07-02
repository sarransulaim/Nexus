"""
mcp.py — manage connected enterprise apps / data sources (MCP servers).
Company-scoped; ANY authenticated user can connect/manage (the "anyone can
connect" design). Auth tokens are Fernet-encrypted at rest and are
never returned to clients. These connections are later fed into the orchestrator's
Claude calls (Anthropic's remote MCP connector) so the AI can read real artifacts.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import MCPConnection, Employee
from api.security import get_current_user
from api.token_crypto import encrypt_secret

router = APIRouter()


class MCPConnect(BaseModel):
    app: str
    label: str = ""
    url: str
    auth_token: str = ""


def _public(c: MCPConnection) -> dict:
    """Connection without the secret — never expose the token."""
    return {
        "id":        c.id,
        "app":       c.app,
        "label":     c.label,
        "url":       c.url,
        "enabled":   c.enabled,
        "has_token": bool(c.auth_token_enc),
    }


@router.get("/")
def list_connections(db: Session = Depends(get_db), current_user: Employee = Depends(get_current_user)):
    rows = (db.query(MCPConnection)
              .filter(MCPConnection.company_id == current_user.company_id)
              .order_by(MCPConnection.created_at.desc())
              .all())
    return {"connections": [_public(c) for c in rows]}


@router.post("/")
def connect(payload: MCPConnect, db: Session = Depends(get_db),
            current_user: Employee = Depends(get_current_user)):
    if not payload.app.strip() or not payload.url.strip():
        raise HTTPException(status_code=400, detail="app and url are required.")
    label = payload.label.strip() or payload.app.strip()
    enc = encrypt_secret(payload.auth_token) if payload.auth_token else None
    # one row per (company, app) — upsert so re-connecting just updates the token/url
    c = db.query(MCPConnection).filter(
        MCPConnection.company_id == current_user.company_id,
        MCPConnection.app == payload.app.strip(),
    ).first()
    if c:
        c.label = label
        c.url = payload.url.strip()
        if enc is not None:
            c.auth_token_enc = enc
        c.enabled = True
    else:
        c = MCPConnection(
            company_id=current_user.company_id,
            app=payload.app.strip(), label=label,
            url=payload.url.strip(), auth_token_enc=enc, enabled=True,
        )
        db.add(c)
    db.commit(); db.refresh(c)
    return {"connection": _public(c), "message": f"{label} connected."}


@router.patch("/{conn_id}/toggle")
def toggle(conn_id: int, db: Session = Depends(get_db),
           current_user: Employee = Depends(get_current_user)):
    c = db.query(MCPConnection).filter(
        MCPConnection.id == conn_id, MCPConnection.company_id == current_user.company_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Connection not found.")
    c.enabled = not c.enabled
    db.commit()
    return {"connection": _public(c)}


@router.delete("/{conn_id}")
def disconnect(conn_id: int, db: Session = Depends(get_db),
               current_user: Employee = Depends(get_current_user)):
    c = db.query(MCPConnection).filter(
        MCPConnection.id == conn_id, MCPConnection.company_id == current_user.company_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Connection not found.")
    db.delete(c); db.commit()
    return {"message": "Disconnected."}
