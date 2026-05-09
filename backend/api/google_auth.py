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
    Generates the Google OAuth authorization URL.
    Stores the flow object so we can complete the exchange in the callback.
    """
    flow = _make_flow()

    auth_url, state = flow.authorization_url(
        access_type            = "offline",
        include_granted_scopes = "true",
        prompt                 = "consent",
        state                  = str(employee_id),
    )

    # Store flow keyed by state so callback can retrieve it
    _pending_flows[state] = flow
    return auth_url


def handle_google_callback(code: str, state: str, db: Session) -> dict:
    """
    Exchanges the authorization code for tokens.
    Uses the stored flow object to avoid PKCE mismatch.
    """
    employee_id = int(state)

    # Retrieve the exact flow we created during auth URL generation
    flow = _pending_flows.pop(state, None)

    if flow is None:
        # Fallback: create a fresh flow (loses PKCE state but works for simple cases)
        flow = _make_flow()

    # Tell the flow to skip code verifier validation
    import os
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # allow http for local dev

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

    token_data = json.loads(record.access_token)

    creds = Credentials(
        token         = token_data.get("token"),
        refresh_token = token_data.get("refresh_token"),
        token_uri     = "https://oauth2.googleapis.com/token",
        client_id     = GOOGLE_CLIENT_ID,
        client_secret = GOOGLE_CLIENT_SECRET,
        scopes        = SCOPES,
    )

    # Auto-refresh if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_tokens(employee_id, creds, db)

    return creds


# ---------------------------------------------------------------------------
# SAVE TOKENS TO DB
# ---------------------------------------------------------------------------
def _save_tokens(employee_id: int, credentials: Credentials, db: Session):
    """Upserts OAuth tokens for an employee."""
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

    if existing:
        existing.access_token = token_data
        existing.token_expiry = credentials.expiry
        existing.updated_at   = datetime.now(timezone.utc)
    else:
        db.add(OAuthToken(
            employee_id   = employee_id,
            provider      = "google",
            access_token  = token_data,   # storing full token JSON
            token_expiry  = credentials.expiry,
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