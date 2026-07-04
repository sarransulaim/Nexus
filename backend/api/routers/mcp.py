"""
mcp.py — manage connected enterprise apps / data sources (MCP servers).
Company-scoped; ANY authenticated user can connect/manage (the "anyone can
connect" design). Auth tokens are Fernet-encrypted at rest and are
never returned to clients. These connections are later fed into the orchestrator's
Claude calls (Anthropic's remote MCP connector) so the AI can read real artifacts.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
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


class MCPOAuthStart(BaseModel):
    app: str
    label: str = ""
    url: str


def _public(c: MCPConnection, viewer: Employee = None) -> dict:
    """Connection without the secret — never expose the token."""
    return {
        "id":        c.id,
        "app":       c.app,
        "label":     c.label,
        "url":       c.url,
        "enabled":   c.enabled,
        "has_token": bool(c.auth_token_enc),
        "auth_type": c.auth_type or "token",
        "shared":    c.owner_id is None,
        "mine":      (viewer is not None and c.owner_id == viewer.id),
    }


@router.get("/")
def list_connections(db: Session = Depends(get_db), current_user: Employee = Depends(get_current_user)):
    # You see company-shared connections plus YOUR OWN per-user (OAuth) ones —
    # never someone else's personal connection.
    rows = (db.query(MCPConnection)
              .filter(MCPConnection.company_id == current_user.company_id,
                      (MCPConnection.owner_id.is_(None)) | (MCPConnection.owner_id == current_user.id))
              .order_by(MCPConnection.created_at.desc())
              .all())
    return {"connections": [_public(c, current_user) for c in rows]}


# ── One-click OAuth (the Claude-connectors experience) ─────────────────────

@router.post("/oauth/start")
def oauth_start(payload: MCPOAuthStart, current_user: Employee = Depends(get_current_user)):
    """Discovery + dynamic client registration → returns the provider's consent
    URL for the frontend to open in a popup. The state binds this attempt to
    the CURRENT user server-side (per-user consent, per-user connection)."""
    if not payload.app.strip() or not payload.url.strip():
        raise HTTPException(status_code=400, detail="app and url are required.")
    from api import mcp_oauth
    try:
        authorize_url = mcp_oauth.start_flow(
            server_url=payload.url.strip(),
            app=payload.app.strip(),
            label=(payload.label.strip() or payload.app.strip()),
            employee_id=current_user.id,
            company_id=current_user.company_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach the MCP server's OAuth endpoints.")
    return {"authorize_url": authorize_url}


@router.get("/oauth/callback")
def oauth_callback(state: str = "", code: str = "", error: str = "",
                   db: Session = Depends(get_db)):
    """Public browser redirect target. The single-use expiring `state` is the
    credential (same pattern as the Google OAuth callback). Stores the tokens
    encrypted on a PER-USER connection and closes the popup."""
    def _page(title: str, body: str, ok: bool) -> HTMLResponse:
        return HTMLResponse(f"""<!doctype html><html><body style="font-family:sans-serif;
            background:#0b0b12;color:#e8e8f0;display:flex;align-items:center;justify-content:center;height:96vh">
            <div style="text-align:center"><h2>{'✅' if ok else '⚠️'} {title}</h2><p>{body}</p></div>
            <script>try{{window.opener&&window.opener.postMessage({{type:'nexus-mcp-oauth',ok:{str(ok).lower()}}},'*')}}catch(e){{}}
            setTimeout(()=>window.close(), {2500 if ok else 6000});</script></body></html>""")

    if error:
        return _page("Connection cancelled", f"The provider reported: {error}", False)
    if not state or not code:
        return _page("Connection failed", "Missing state or code.", False)

    from api import mcp_oauth
    try:
        result = mcp_oauth.finish_flow(state, code)
    except ValueError as e:
        return _page("Connection failed", str(e), False)

    ctx, tok = result["ctx"], result["tokens"]
    c = db.query(MCPConnection).filter(
        MCPConnection.company_id == ctx["company_id"],
        MCPConnection.app == ctx["app"],
        MCPConnection.owner_id == ctx["employee_id"],
    ).first()
    if not c:
        c = MCPConnection(company_id=ctx["company_id"], app=ctx["app"],
                          owner_id=ctx["employee_id"])
        db.add(c)
    c.label = ctx["label"]
    c.url = ctx["server_url"]
    c.enabled = True
    c.auth_type = "oauth"
    c.auth_token_enc = encrypt_secret(tok["access_token"])
    c.refresh_token_enc = encrypt_secret(tok["refresh_token"]) if tok.get("refresh_token") else None
    c.oauth_client_id = ctx["client_id"]
    c.oauth_client_secret_enc = encrypt_secret(ctx["client_secret"]) if ctx.get("client_secret") else None
    c.oauth_token_endpoint = ctx["token_endpoint"]
    c.token_expires_at = mcp_oauth.expiry_from(tok)
    db.commit()
    return _page("Connected", f"{ctx['label']} is now available to your AI. You can close this window.", True)


@router.post("/")
def connect(payload: MCPConnect, db: Session = Depends(get_db),
            current_user: Employee = Depends(get_current_user)):
    if not payload.app.strip() or not payload.url.strip():
        raise HTTPException(status_code=400, detail="app and url are required.")
    label = payload.label.strip() or payload.app.strip()
    enc = encrypt_secret(payload.auth_token) if payload.auth_token else None
    # one SHARED row per (company, app) — upsert so re-connecting updates the
    # token/url. Pasted-token connections stay company-shared (owner NULL);
    # per-user rows are created only by the OAuth flow.
    c = db.query(MCPConnection).filter(
        MCPConnection.company_id == current_user.company_id,
        MCPConnection.app == payload.app.strip(),
        MCPConnection.owner_id.is_(None),
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
        MCPConnection.id == conn_id, MCPConnection.company_id == current_user.company_id,
        (MCPConnection.owner_id.is_(None)) | (MCPConnection.owner_id == current_user.id)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Connection not found.")
    c.enabled = not c.enabled
    db.commit()
    return {"connection": _public(c, current_user)}


@router.delete("/{conn_id}")
def disconnect(conn_id: int, db: Session = Depends(get_db),
               current_user: Employee = Depends(get_current_user)):
    c = db.query(MCPConnection).filter(
        MCPConnection.id == conn_id, MCPConnection.company_id == current_user.company_id,
        (MCPConnection.owner_id.is_(None)) | (MCPConnection.owner_id == current_user.id)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Connection not found.")
    db.delete(c); db.commit()
    return {"message": "Disconnected."}
