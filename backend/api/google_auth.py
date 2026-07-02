"""
google_auth.py — Google OAuth2 Flow
=====================================
Handles the full OAuth dance:
  1. Generate authorization URL → redirect user to Google
  2. Google redirects back with a code
  3. Exchange code for access + refresh tokens
  4. Store encrypted tokens in oauth_tokens table
  5. Personal agent now has Gmail + Calendar access

WHY OAUTH AND NOT AN API KEY:
OAuth is user-delegated auth — the employee explicitly grants
Nexus permission to access THEIR Gmail/Calendar.
This is the privacy-first approach: we never see their password,
Google handles auth, and the user can revoke access at any time.
"""

import os
import json
from datetime import datetime, timezone
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from database.models import OAuthToken, Employee
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/google/callback")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

CLIENT_CONFIG = {
    "web": {
        "client_id":                   GOOGLE_CLIENT_ID,
        "client_secret":               GOOGLE_CLIENT_SECRET,
        "redirect_uris":               [GOOGLE_REDIRECT_URI],
        "auth_uri":                    "https://accounts.google.com/o/oauth2/auth",
        "token_uri":                   "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    }
}

# In-memory store for OAuth state → flow mapping
# This keeps the flow object alive between the auth URL generation
# and the callback so we can complete the token exchange
_pending_flows: dict = {}


# ── Token encryption at rest ──────────────────────────────────
# OAuth tokens (incl. the refresh token + client_secret) are encrypted before
# they touch the DB, so a database dump alone no longer yields working Google
# access. The key is derived from an env secret (NEXUS_TOKEN_KEY, falling back
# to JWT_SECRET) and lives only in the environment — never in the DB.
import base64
import hashlib
from cryptography.fernet import Fernet


# Token encryption is centralized in token_crypto (multi-key: ENCRYPT with the
# stable NEXUS_TOKEN_KEY, DECRYPT with any known key), so rotating JWT_SECRET
# never bricks stored Google tokens and legacy rows keep decrypting.
from api.token_crypto import encrypt_secret as _encrypt, decrypt_secret as _decrypt


def _load_token_json(stored: str) -> dict:
    """Decrypt + parse stored token JSON. Falls back to legacy plaintext (which
    is then re-encrypted on the next save), so pre-existing rows keep working."""
    try:
        return json.loads(_decrypt(stored))
    except Exception:
        try:
            return json.loads(stored)   # legacy plaintext row
        except Exception:
            return {}


def _make_flow() -> Flow:
    """Creates a Flow with PKCE disabled — required for server-side web apps."""
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    # Disable PKCE — server-side apps use client_secret instead
    flow.code_challenge_method = None
    return flow


def get_google_auth_url(employee_id: int) -> str:
    """
    Generates the Google OAuth authorization URL with a RANDOM, single-use state
    (B4). The state is an opaque server-issued token bound to employee_id in
    _pending_flows — never the employee_id itself — so the callback can't be
    tricked into binding tokens to an attacker-chosen id.
    """
    import secrets, time
    flow = _make_flow()
    state = secrets.token_urlsafe(32)

    auth_url, _ = flow.authorization_url(
        access_type            = "offline",
        include_granted_scopes = "true",
        prompt                 = "consent",
        state                  = state,
    )

    # Evict expired pending flows first — abandoned OAuth starts (user gets the
    # URL but never returns) would otherwise leak Flow objects unbounded across
    # the long-lived worker's lifetime. Then bind state → flow + employee_id (TTL).
    now = time.time()
    for _s in [k for k, v in _pending_flows.items() if v.get("exp", 0) < now]:
        _pending_flows.pop(_s, None)
    _pending_flows[state] = {"flow": flow, "employee_id": employee_id, "exp": now + 600}
    return auth_url


def handle_google_callback(code: str, state: str, db: Session) -> dict:
    """
    Exchanges the authorization code for tokens.
    Uses the stored flow object to avoid PKCE mismatch.
    """
    import time

    # Pull the SERVER-SIDE entry bound to this random state. employee_id comes from
    # here, never from the state string (B4); the state is single-use + expiring,
    # which closes the OAuth CSRF / account-linking hole.
    entry = _pending_flows.pop(state, None)
    if not entry or entry.get("exp", 0) < time.time():
        raise ValueError("Invalid or expired OAuth state. Please start the connection again.")

    employee_id = entry["employee_id"]
    flow = entry["flow"]

    # Allow http only for local/dev redirect URIs; production (https) stays strict.
    if GOOGLE_REDIRECT_URI.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    flow.fetch_token(code=code)
    credentials = flow.credentials

    _save_tokens(employee_id, credentials, db)

    return {
        "employee_id": employee_id,
        "message":     "Google Workspace connected successfully.",
    }


# ---------------------------------------------------------------------------
# GET VALID CREDENTIALS
# Call this before every Google API call.
# Automatically refreshes expired tokens.
# ---------------------------------------------------------------------------
def get_credentials(employee_id: int, db: Session) -> Credentials | None:
    """
    Loads stored tokens for an employee and returns a valid
    Credentials object. Auto-refreshes if expired.
    Returns None if the employee hasn't connected Google yet.
    """
    record = db.query(OAuthToken).filter(
        OAuthToken.employee_id == employee_id,
        OAuthToken.provider    == "google",
    ).first()

    if not record:
        return None

    token_data = _load_token_json(record.access_token)

    # google-auth expects expiry as a NAIVE UTC datetime; our column is tz-aware.
    # Passing it matters: without expiry, creds.expired is always False, so the
    # proactive refresh below never runs and an expired token instead fails
    # lazily INSIDE the first API call (bypassing the self-heal below).
    # A NAIVE stored value (older rows / non-tz drivers) is treated as UTC —
    # google-auth's own expiry is naive UTC, so that's the correct reading.
    expiry = None
    if record.token_expiry is not None:
        try:
            raw = record.token_expiry
            if raw.tzinfo is None:
                raw = raw.replace(tzinfo=timezone.utc)
            expiry = raw.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            expiry = None

    creds = Credentials(
        token         = token_data.get("token"),
        refresh_token = token_data.get("refresh_token"),
        token_uri     = "https://oauth2.googleapis.com/token",
        client_id     = GOOGLE_CLIENT_ID,
        client_secret = GOOGLE_CLIENT_SECRET,
        scopes        = SCOPES,
        expiry        = expiry,
    )

    # Auto-refresh if expired. Two rules learned the hard way:
    #  1. NEVER commit/rollback the CALLER's session here — callers (e.g. the
    #     orchestrator's reschedule handler) may have their own uncommitted work
    #     in flight; token maintenance runs on a PRIVATE short-lived session.
    #  2. Only self-heal (delete the stored row → UI shows "reconnect") on
    #     PERMANENT refresh failures (invalid_grant = revoked/expired refresh
    #     token). Transient errors (network blip, Google 5xx) keep the token
    #     and just report "not connected" for this one call.
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            from google.auth.exceptions import RefreshError
            from database.core import SessionLocal
            _msg = str(e).lower()
            _permanent = isinstance(e, RefreshError) and any(
                k in _msg for k in ("invalid_grant", "invalid_client",
                                    "unauthorized_client", "deleted_client")
            )
            # ASCII-only messages: an emoji here crashes with UnicodeEncodeError
            # on cp1252 stdout (Windows), escaping this except mid-cleanup.
            if _permanent:
                print(f"[google_auth] token refresh failed permanently for employee "
                      f"{employee_id}: {e} - clearing stored token.")
                _tdb = SessionLocal()
                try:
                    _tdb.query(OAuthToken).filter(
                        OAuthToken.employee_id == employee_id,
                        OAuthToken.provider == "google",
                    ).delete()
                    _tdb.commit()
                except Exception:
                    _tdb.rollback()
                finally:
                    _tdb.close()
            else:
                print(f"[google_auth] transient token refresh error for employee "
                      f"{employee_id}: {e} - keeping stored token.")
            return None

        # Refresh succeeded — persist the rotated token on a PRIVATE session so
        # the caller's transaction is never committed as a side effect.
        from database.core import SessionLocal
        _tdb = SessionLocal()
        try:
            _save_tokens(employee_id, creds, _tdb)
        finally:
            _tdb.close()

    return creds


# ---------------------------------------------------------------------------
# SAVE TOKENS TO DB
# ---------------------------------------------------------------------------
def _save_tokens(employee_id: int, credentials: Credentials, db: Session):
    """Upserts OAuth tokens for an employee. NOTE: commits `db` — call it with a
    dedicated session unless committing the caller's transaction is intended."""
    # google-auth returns expiry as NAIVE UTC; our column is timestamptz. Storing
    # it naive would make Postgres interpret it in the SERVER's timezone, shifting
    # the instant by the UTC offset and silently defeating the proactive refresh.
    expiry = credentials.expiry
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    token_data = json.dumps({
        "token":         credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri":     credentials.token_uri,
        "client_id":     credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes":        credentials.scopes,
    })

    existing = db.query(OAuthToken).filter(
        OAuthToken.employee_id == employee_id,
        OAuthToken.provider    == "google",
    ).first()

    encrypted = _encrypt(token_data)
    if existing:
        existing.access_token = encrypted
        existing.token_expiry = expiry
        existing.updated_at   = datetime.now(timezone.utc)
    else:
        db.add(OAuthToken(
            employee_id   = employee_id,
            provider      = "google",
            access_token  = encrypted,    # encrypted token JSON (Fernet, key in env)
            token_expiry  = expiry,
            scope         = " ".join(SCOPES),
        ))
    db.commit()


# ---------------------------------------------------------------------------
# CHECK CONNECTION STATUS
# ---------------------------------------------------------------------------
def is_google_connected(employee_id: int, db: Session) -> bool:
    """Returns True if this employee has connected their Google account."""
    record = db.query(OAuthToken).filter(
        OAuthToken.employee_id == employee_id,
        OAuthToken.provider    == "google",
    ).first()
    return record is not None


# ---------------------------------------------------------------------------
# REVOKE ACCESS
# ---------------------------------------------------------------------------
def disconnect_google(employee_id: int, db: Session):
    """Removes stored tokens — employee will need to re-auth to reconnect."""
    db.query(OAuthToken).filter(
        OAuthToken.employee_id == employee_id,
        OAuthToken.provider    == "google",
    ).delete()
    db.commit()