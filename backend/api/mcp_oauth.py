"""
mcp_oauth.py — one-click OAuth for remote MCP servers (the Claude-connectors UX)
================================================================================
Implements the client side of the MCP authorization spec so connecting Linear/
Notion/Atlassian/GitHub-style servers is: click → provider's consent page →
connected. No pasted tokens.

Flow (per the MCP auth spec, tolerant of older servers):
  1. DISCOVER   RFC 9728 protected-resource metadata on the MCP server
                (path-aware then root), falling back to treating the MCP origin
                as its own authorization server; then RFC 8414 authorization-
                server metadata (+ OIDC fallback).
  2. REGISTER   RFC 7591 Dynamic Client Registration (public client, PKCE).
  3. AUTHORIZE  Authorization-code + PKCE (S256) + RFC 8707 resource indicator,
                with a random single-use expiring state bound server-side.
  4. EXCHANGE   code → access/refresh tokens (stored Fernet-encrypted).
  5. REFRESH    near-expiry refresh with rotation, on a private DB session.

Security notes: the state is the only credential on the public callback (same
pattern as google_auth); tokens never reach the browser; per-user connections
carry the CONSENTING user's identity (owner_id) — they are not company-shared.
"""
import os
import time
import base64
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlencode

import requests

log = logging.getLogger("nexus.mcp_oauth")

HTTP_TIMEOUT = 10
BACKEND_BASE = os.getenv("BACKEND_URL", "http://localhost:8000")
REDIRECT_URI = f"{BACKEND_BASE.rstrip('/')}/api/v1/mcp/oauth/callback"

# state → pending flow context (single-use, expiring). In-process, like
# google_auth._pending_flows — fine under the documented one-worker rule.
_pending: dict = {}
_PENDING_TTL = 600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _get_json(url: str):
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"Accept": "application/json"})
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def discover(server_url: str) -> dict:
    """Resolve the MCP server's OAuth endpoints.

    Returns {"authorization_endpoint", "token_endpoint", "registration_endpoint"
    (may be None), "resource"} or raises ValueError with a user-readable reason.
    """
    # Everything below fetches this URL from the backend, so validate before
    # the first request rather than after: an https URL pointed at
    # 169.254.169.254 would otherwise have us read cloud instance metadata and
    # hand the result back through the OAuth flow.
    from api.url_guard import validate_outbound_url, UnsafeURL
    try:
        server_url = validate_outbound_url(server_url)
    except UnsafeURL as e:
        raise ValueError(str(e))

    p = urlparse(server_url)
    origin = f"{p.scheme}://{p.netloc}"
    path = (p.path or "").rstrip("/")

    # 1) protected-resource metadata → who is the authorization server?
    auth_server = None
    for u in (f"{origin}/.well-known/oauth-protected-resource{path}",
              f"{origin}/.well-known/oauth-protected-resource"):
        meta = _get_json(u)
        if meta and meta.get("authorization_servers"):
            auth_server = meta["authorization_servers"][0]
            break
    if not auth_server:
        auth_server = origin   # older servers: MCP origin doubles as the AS

    # 2) authorization-server metadata
    ap = urlparse(auth_server)
    as_origin = f"{ap.scheme}://{ap.netloc}"
    as_path = (ap.path or "").rstrip("/")
    candidates = [f"{as_origin}/.well-known/oauth-authorization-server{as_path}",
                  f"{as_origin}/.well-known/oauth-authorization-server",
                  f"{as_origin}/.well-known/openid-configuration"]
    as_meta = None
    for u in candidates:
        as_meta = _get_json(u)
        if as_meta and as_meta.get("authorization_endpoint") and as_meta.get("token_endpoint"):
            break
        as_meta = None
    if not as_meta:
        raise ValueError(
            "This MCP server doesn't advertise OAuth (no authorization metadata found). "
            "It may need an API token instead — use the Advanced option."
        )

    return {
        "authorization_endpoint": as_meta["authorization_endpoint"],
        "token_endpoint":         as_meta["token_endpoint"],
        "registration_endpoint":  as_meta.get("registration_endpoint"),
        "resource":               server_url,
    }


def register_client(registration_endpoint: str) -> dict:
    """RFC 7591 dynamic registration as a PUBLIC client (PKCE, no secret).
    Returns {"client_id", "client_secret" (usually empty)}."""
    r = requests.post(registration_endpoint, json={
        "client_name": "Nexus Command",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, timeout=HTTP_TIMEOUT, headers={"Accept": "application/json"})
    if r.status_code not in (200, 201):
        raise ValueError(f"Client registration was rejected (HTTP {r.status_code}).")
    data = r.json()
    if not data.get("client_id"):
        raise ValueError("Client registration returned no client_id.")
    return {"client_id": data["client_id"], "client_secret": data.get("client_secret", "")}


# Sensible default scopes for providers that need an explicit scope parameter
# (override per deployment with MCP_SCOPE_<APP>).
PROVIDER_DEFAULT_SCOPES = {
    "github": "repo read:org read:user",
    # Slack's MCP server authorizes as the USER (oauth/v2_user/authorize)
    "slack":  "channels:read channels:history groups:history im:history search:read users:read",
}

# Providers whose OAuth client can be reused from credentials we already hold,
# so the operator doesn't have to register a second app. Slack in particular:
# the workspace app powering the bot is the same app the MCP server expects.
CLIENT_ID_FALLBACK_ENV = {
    "slack": ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"),
}


def _env_key(app: str) -> str:
    import re as _re
    return _re.sub(r"[^A-Z0-9]", "_", app.upper())


def start_flow(server_url: str, app: str, label: str,
               employee_id: int, company_id: int) -> str:
    """Discovery + registration + authorize-URL. Returns the URL to open."""
    meta = discover(server_url)
    k = _env_key(app)
    scope = os.getenv(f"MCP_SCOPE_{k}", PROVIDER_DEFAULT_SCOPES.get(app.lower(), ""))

    client = None
    if meta.get("registration_endpoint"):
        client = register_client(meta["registration_endpoint"])
    else:
        # Providers WITHOUT Dynamic Client Registration (e.g. GitHub) need a
        # one-time pre-registered OAuth app; after that, one-click works for
        # everyone. Callback URL for the provider's app config: REDIRECT_URI.
        cid = os.getenv(f"MCP_CLIENT_ID_{k}", "").strip()
        csec = os.getenv(f"MCP_CLIENT_SECRET_{k}", "").strip()
        if not cid:
            # Reuse credentials we already hold for this provider (e.g. the
            # Slack app that runs the bot IS the app Slack's MCP expects).
            fb = CLIENT_ID_FALLBACK_ENV.get(app.lower())
            if fb:
                cid = os.getenv(fb[0], "").strip()
                csec = os.getenv(fb[1], "").strip()
        if cid:
            client = {"client_id": cid, "client_secret": csec}
    if client is None:
        raise ValueError(
            f"{label} doesn't support automatic app registration. Use the Advanced option "
            f"with an API token — or (one-time admin setup) register an OAuth app at the "
            f"provider with callback URL {REDIRECT_URI} and set MCP_CLIENT_ID_{k} + "
            f"MCP_CLIENT_SECRET_{k} on the server, then one-click works for everyone."
        )

    state    = secrets.token_urlsafe(32)
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())

    # evict expired pendings, then bind this one
    now = time.time()
    for k in [k for k, v in _pending.items() if v.get("exp", 0) < now]:
        _pending.pop(k, None)
    _pending[state] = {
        "exp": now + _PENDING_TTL,
        "verifier": verifier,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "token_endpoint": meta["token_endpoint"],
        "resource": meta["resource"],
        "server_url": server_url,
        "app": app, "label": label,
        "employee_id": employee_id, "company_id": company_id,
    }

    q = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": meta["resource"],
    }
    if scope:
        q["scope"] = scope
    return f"{meta['authorization_endpoint']}{'&' if '?' in meta['authorization_endpoint'] else '?'}{urlencode(q)}"


def finish_flow(state: str, code: str) -> dict:
    """Exchange the code. Returns the pending ctx + token payload.
    Raises ValueError on unknown/expired state or a failed exchange."""
    ctx = _pending.pop(state, None)
    if not ctx or ctx.get("exp", 0) < time.time():
        raise ValueError("This connection attempt expired — please start again.")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": ctx["client_id"],
        "code_verifier": ctx["verifier"],
        "resource": ctx["resource"],
    }
    if ctx.get("client_secret"):
        data["client_secret"] = ctx["client_secret"]
    r = requests.post(ctx["token_endpoint"], data=data, timeout=HTTP_TIMEOUT,
                      headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise ValueError(f"Token exchange failed (HTTP {r.status_code}).")
    tok = r.json()
    if not tok.get("access_token"):
        raise ValueError("Token exchange returned no access token.")
    return {"ctx": ctx, "tokens": tok}


def expiry_from(tokens: dict):
    try:
        ttl = int(tokens.get("expires_in") or 0)
        return datetime.now(timezone.utc) + timedelta(seconds=ttl) if ttl > 0 else None
    except (TypeError, ValueError):
        return None


def refresh(conn, decrypt, encrypt) -> bool:
    """Refresh an oauth connection's access token IN PLACE on the given ORM
    object (caller commits). Returns True on success, False on permanent
    failure (caller should skip the connection). Never raises."""
    try:
        if not (conn.refresh_token_enc and conn.oauth_token_endpoint and conn.oauth_client_id):
            return False
        data = {
            "grant_type": "refresh_token",
            "refresh_token": decrypt(conn.refresh_token_enc),
            "client_id": conn.oauth_client_id,
            "resource": conn.url,
        }
        if conn.oauth_client_secret_enc:
            data["client_secret"] = decrypt(conn.oauth_client_secret_enc)
        r = requests.post(conn.oauth_token_endpoint, data=data, timeout=HTTP_TIMEOUT,
                          headers={"Accept": "application/json"})
        if r.status_code != 200:
            log.warning(f"MCP token refresh failed for {conn.app} (HTTP {r.status_code})")
            return False
        tok = r.json()
        if not tok.get("access_token"):
            return False
        conn.auth_token_enc = encrypt(tok["access_token"])
        if tok.get("refresh_token"):   # rotation
            conn.refresh_token_enc = encrypt(tok["refresh_token"])
        conn.token_expires_at = expiry_from(tok)
        return True
    except Exception as e:
        log.warning(f"MCP token refresh error for {getattr(conn, 'app', '?')}: {e}")
        return False
