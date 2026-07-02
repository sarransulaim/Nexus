"""
security.py — JWT + Password Hashing Utilities
================================================
This file handles two things:
  1. Password hashing — never store plain passwords in the DB
  2. JWT tokens — create and verify the ID badges we give to logged-in users
"""

import os
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
        "type": "refresh"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

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