"""
security.py — JWT + Password Hashing Utilities
================================================
This file handles two things:
  1. Password hashing — never store plain passwords in the DB
  2. JWT tokens — create and verify the ID badges we give to logged-in users
"""

import os
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database.core import get_db
from database.models import Employee
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
JWT_SECRET      = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM   = "HS256"

# Fail closed: a weak or default signing key lets anyone forge a manager token.
# Refuse to start unless JWT_SECRET is explicitly set to a strong, non-default value.
_KNOWN_WEAK_SECRETS = {
    "", "nexus_change_this_in_production", "nexus_super_secret_change_this_now",
    "changeme", "secret", "your-secret-key",
}
if JWT_SECRET in _KNOWN_WEAK_SECRETS or len(JWT_SECRET) < 32:
    raise RuntimeError(
        "JWT_SECRET is unset, a known default, or shorter than 32 characters. "
        "Set a strong random JWT_SECRET in the environment before starting Nexus.\n"
        "Generate one with:  python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
ACCESS_TOKEN_EXPIRE_HOURS  = 8    # token lasts 8 hours
REFRESH_TOKEN_EXPIRE_DAYS  = 30   # refresh token lasts 30 days

# ---------------------------------------------------------------------------
# PASSWORD HASHING
# ---------------------------------------------------------------------------
# bcrypt is the industry standard for hashing passwords.
# It's a one-way function — you can never reverse it back to the original.
# We hash on signup, then compare hashes on login. Never store plain text.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain_password: str) -> str:
    """Turn 'mypassword123' into '$2b$12$...' (unreadable hash)"""
    return pwd_context.hash(plain_password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if the typed password matches the stored hash."""
    return pwd_context.verify(plain_password, hashed_password)

# ---------------------------------------------------------------------------
# JWT TOKEN CREATION
# ---------------------------------------------------------------------------
def create_access_token(employee_id: int, role: str, name: str) -> str:
    """
    Create a short-lived access token (8 hours).
    This is the badge the frontend sends with every request.
    """
    payload = {
        "sub": str(employee_id),   # 'sub' = subject (who is this token for)
        "role": role,              # "manager" or "employee"
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token(employee_id: int) -> str:
    """
    Create a long-lived refresh token (30 days).
    Used to get a new access token when the old one expires.
    Without this, users would have to log in every 8 hours.
    """
    payload = {
        "sub": str(employee_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
        # A JWT is a pure function of its claims, so two tokens minted for the
        # same user in the same second used to come out byte-identical —
        # "rotating" one returned the caller's existing token unchanged. A
        # random jti makes every issued token distinct.
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# REFRESH-TOKEN STORAGE
# ---------------------------------------------------------------------------
# Refresh tokens were stored as bcrypt hashes, reusing the password helpers.
# bcrypt silently TRUNCATES its input at 72 bytes, and the first 72 bytes of a
# refresh JWT are the header plus the opening of the payload — identical for
# every token belonging to the same user. The stored hash therefore proved
# only "some refresh token for this user", never WHICH one, so a superseded
# token kept verifying against its replacement's hash and revocation-by-
# rotation could not work at all.
#
# A refresh token is 143 bytes of high-entropy, server-generated material, not
# a human-chosen password: it needs no salt and no key stretching, so a plain
# SHA-256 over the WHOLE token is both correct and free of the length limit.
def hash_refresh_token(token: str) -> str:
    import hashlib
    return "sha256$" + hashlib.sha256(token.encode()).hexdigest()


def verify_refresh_token(token: str, stored: str | None) -> bool:
    """Constant-time check against a stored refresh-token hash.

    Falls back to bcrypt for rows written before the switch, so existing
    sessions keep working and simply upgrade on their next rotation.
    """
    if not stored:
        return False
    if stored.startswith("sha256$"):
        import hmac as _hmac
        return _hmac.compare_digest(stored, hash_refresh_token(token))
    try:
        return pwd_context.verify(token, stored)   # legacy bcrypt row
    except Exception:
        return False

# ---------------------------------------------------------------------------
# JWT TOKEN VERIFICATION
# ---------------------------------------------------------------------------
def decode_token(token: str) -> dict:
    """
    Decode and verify a token.
    Raises an error if the token is fake, expired, or tampered with.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token. Access denied.",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ---------------------------------------------------------------------------
# FASTAPI DEPENDENCY — get current logged-in user
# ---------------------------------------------------------------------------
# This is what we'll add to any route we want to protect.
# Just add: current_user: Employee = Depends(get_current_user)
# FastAPI will automatically check the token before running the route.

bearer_scheme = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> Employee:
    """
    Extracts the token from the request header,
    decodes it, and returns the Employee object from the database.
    If anything is wrong, the request is blocked with a 401 error.
    """
    token = credentials.credentials
    payload = decode_token(token)

    # Only ACCESS tokens authenticate an API call. Refresh tokens live 30 days
    # and are the thing /auth/logout and a password reset revoke — accepting
    # one here meant a stolen refresh token kept working as a bearer token
    # forever, so logging out never actually ended a hijacked session.
    # (/auth/refresh reads its token from the body, not this dependency.)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — use an access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    employee_id = int(payload.get("sub"))
    employee = db.query(Employee).filter(
        Employee.id == employee_id,
        Employee.is_active == True
    ).first()

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account deactivated."
        )
    return employee

# The subprotocol name the client offers alongside its token. `accept()` must
# echo exactly one of the offered protocols back, so the token rides as a
# second protocol value and this is what we select.
WS_AUTH_SUBPROTOCOL = "nexus-auth"


# The query-string fallback is deprecated but still accepted, because turning
# it off is a decision about live users rather than about code: a browser tab
# opened before the subprotocol shipped is still running the old bundle, and
# production logs showed most WebSocket handshakes still arriving that way.
# Set NEXUS_ALLOW_WS_QUERY_TOKEN=0 to switch it off once the deprecation
# warnings stop appearing — a config flip, not a deploy.
ALLOW_WS_QUERY_TOKEN = os.getenv("NEXUS_ALLOW_WS_QUERY_TOKEN", "1") != "0"


def ws_token_from(websocket) -> tuple[str | None, bool]:
    """Pull a WebSocket's bearer token, preferring the Sec-WebSocket-Protocol
    header over the query string.

    A token in the URL ends up in reverse-proxy access logs, browser history,
    and any Referer sent by a page on the same origin — none of which are
    places an 8-hour credential should live. Browsers won't let you set headers
    on a WebSocket handshake, but they DO let you name subprotocols, which are
    sent as a header and never logged as part of the URL.

    Returns (token, used_query_string) so the caller can warn about the
    deprecated path.
    """
    offered = websocket.headers.get("sec-websocket-protocol", "")
    for part in (p.strip() for p in offered.split(",")):
        if part and part != WS_AUTH_SUBPROTOCOL:
            return part, False
    if not ALLOW_WS_QUERY_TOKEN:
        return None, True      # refuse rather than read it from the URL
    return websocket.query_params.get("token"), True


def internal_token() -> str:
    """Shared secret for in-process callers (the Slack bot -> /internal/sync).
    Derived from JWT_SECRET so there is no extra env var to distribute or
    rotate; it never authenticates a user, only a loopback caller."""
    import hashlib
    return hashlib.sha256(('internal-sync:' + JWT_SECRET).encode()).hexdigest()


def require_manager(current_user: Employee = Depends(get_current_user)) -> Employee:
    """
    Extra layer — only managers can access routes using this dependency.
    Usage: current_user: Employee = Depends(require_manager)
    """
    if current_user.system_role != "manager":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Manager role required."
        )
    return current_user