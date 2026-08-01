"""
auth.py — Authentication Router (Hardened)
==========================================
Changes from v2:
  - Rate limiting on /login (5 per 15min per IP via slowapi)
  - Refresh token verified against bcrypt hash (logout actually works)
  - company_id returned in all user objects
  - Employee creation requires company_id
  - Audit log on login / password change
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from database.core import get_db
from database.models import Employee, Company, AuditLog
from api.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    decode_token, get_current_user,
)
from api.rate_limit import limiter

router = APIRouter()

DEFAULT_COMPANY_ID = 1  # Set on bootstrap — changes when multi-tenant UI is added


# ── Schemas ───────────────────────────────────────────────────

class LoginRequest(BaseModel):
    name: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class SetupRequest(BaseModel):
    name: str
    password: str
    secret_key: str


# ── POST /auth/setup ─────────────────────────────────────────

@router.post("/setup")
@limiter.limit("5/hour")
def setup_manager(request: Request, payload: SetupRequest, db: Session = Depends(get_db)):
    import os, hmac
    expected_secret = os.getenv("SETUP_SECRET", "")
    if not expected_secret or expected_secret == "NEXUS_SETUP_2026" or len(expected_secret) < 16:
        raise HTTPException(
            status_code=503,
            detail="Setup is not configured. Set a strong SETUP_SECRET in the environment before bootstrapping.",
        )
    if not hmac.compare_digest(payload.secret_key, expected_secret):
        raise HTTPException(status_code=403, detail="Invalid setup secret key.")

    existing = db.query(Employee).filter(Employee.system_role == "manager").first()
    if existing:
        raise HTTPException(status_code=400, detail="Manager account already exists.")

    # Ensure company exists
    company = db.query(Company).filter(Company.id == DEFAULT_COMPANY_ID).first()
    if not company:
        raise HTTPException(status_code=500, detail="Company not bootstrapped. Restart the server.")

    manager = Employee(
        company_id=DEFAULT_COMPANY_ID,
        name=payload.name,
        role="Chief of Staff",
        system_role="manager",
        password_hash=hash_password(payload.password),
        is_active=True,
        team="Management",
    )
    db.add(manager)
    db.commit()
    db.refresh(manager)
    return {"message": f"Manager '{manager.name}' created.", "employee_id": manager.id}


# ── GET /auth/status ─────────────────────────────────────────
@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    """Public: whether the instance is bootstrapped (a manager exists). Lets the
    frontend decide between the first-run Setup screen and the Login screen.
    Returns no user data."""
    has_manager = db.query(Employee).filter(Employee.system_role == "manager").first() is not None
    return {"initialized": has_manager}


# ── POST /auth/login ─────────────────────────────────────────

@router.post("/login")
@limiter.limit("10/15minutes")   # 10 attempts per 15 minutes per IP
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Rate limited to prevent brute force.
    Returns access token (8hrs) + refresh token (30 days).
    """
    employee = db.query(Employee).filter(
        Employee.name.ilike(payload.name.strip()),
        Employee.is_active == True,
    ).first()

    if not employee or not employee.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid name or password.",
        )

    if not verify_password(payload.password, employee.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid name or password.",
        )

    employee.last_login = datetime.now(timezone.utc)

    access_token  = create_access_token(employee.id, employee.system_role, employee.name)
    refresh_token = create_refresh_token(employee.id)

    # Store bcrypt hash — so logout invalidates the token
    employee.refresh_token = hash_password(refresh_token)

    # Audit log
    db.add(AuditLog(
        company_id=employee.company_id,
        actor_id=employee.id,
        action="login",
        entity_type="employee",
        entity_id=employee.id,
    ))
    db.commit()

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":         employee.id,
            "name":       employee.name,
            "role":       employee.system_role,
            "team":       employee.team,
            "company_id": employee.company_id,
        },
    }


# ── POST /auth/refresh ───────────────────────────────────────

@router.post("/refresh")
@limiter.limit("30/minute")
def refresh_token_endpoint(request: Request, payload: RefreshRequest, db: Session = Depends(get_db)):
    """
    Verifies refresh token against stored bcrypt hash.
    Logout clears the hash → subsequent refresh attempts fail.
    """
    token_data = decode_token(payload.refresh_token)

    if token_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    employee = db.query(Employee).filter(
        Employee.id == int(token_data["sub"]),
        Employee.is_active == True,
    ).first()

    if not employee:
        raise HTTPException(status_code=401, detail="User not found.")

    # KEY FIX: verify against stored hash
    if not employee.refresh_token:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    if not verify_password(payload.refresh_token, employee.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh token revoked.")

    new_access_token = create_access_token(employee.id, employee.system_role, employee.name)
    return {"access_token": new_access_token, "token_type": "bearer"}


# ── POST /auth/logout ────────────────────────────────────────

@router.post("/logout")
def logout(current_user: Employee = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.refresh_token = None
    db.add(AuditLog(
        company_id=current_user.company_id,
        actor_id=current_user.id,
        action="logout",
        entity_type="employee",
        entity_id=current_user.id,
    ))
    db.commit()
    return {"message": "Logged out."}


# ── GET /auth/me ─────────────────────────────────────────────

@router.get("/me")
def get_me(current_user: Employee = Depends(get_current_user)):
    return {
        "id":         current_user.id,
        "name":       current_user.name,
        "email":      current_user.email,
        "role":       current_user.system_role,
        "team":       current_user.team,
        "company_id": current_user.company_id,
        "last_login": current_user.last_login,
    }


# ── POST /auth/employees/create ──────────────────────────────

class CreateEmployeeRequest(BaseModel):
    name: str
    role: str
    team: str = "Unassigned"
    password: str
    age: int = 25
    experience: int = 0
    skills: str = ""
    gender: str = "Unspecified"

@router.post("/employees/create")
def create_employee(
    payload: CreateEmployeeRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new employee account.

    FIX (Bug 4): Manager-only. Previously any authenticated user could
    create employee accounts within their company.
    """
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")

    existing = db.query(Employee).filter(
        Employee.name.ilike(payload.name.strip()),
        Employee.company_id == current_user.company_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"'{payload.name}' already exists.")

    emp = Employee(
        company_id=current_user.company_id,
        name=payload.name,
        role=payload.role,
        team=payload.team,
        system_role="employee",
        password_hash=hash_password(payload.password),
        age=payload.age,
        experience=payload.experience,
        skills=payload.skills,
        gender=payload.gender,
        is_active=True,
    )
    db.add(emp)
    db.add(AuditLog(
        company_id=current_user.company_id,
        actor_id=current_user.id,
        action="create_employee",
        entity_type="employee",
        new_value={"name": payload.name, "role": payload.role},
    ))
    db.commit()
    db.refresh(emp)
    return {"message": f"Employee '{emp.name}' created.", "employee_id": emp.id}


# ── POST /auth/set-password ──────────────────────────────────

class SetPasswordRequest(BaseModel):
    employee_id: int
    new_password: str

@router.post("/set-password")
def set_employee_password(
    payload: SetPasswordRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Reset another employee's password.

    FIX (Bug 4): Manager-only. Previously any authenticated user could
    reset any other user's password within their company.
    """
    if current_user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")

    emp = db.query(Employee).filter(
        Employee.id == payload.employee_id,
        Employee.company_id == current_user.company_id,
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found.")

    emp.password_hash = hash_password(payload.new_password)
    # Resetting a password must END that person's existing sessions — otherwise
    # a reset prompted by a suspected compromise leaves the attacker's stored
    # refresh token working for its full 30 days.
    emp.refresh_token = None
    db.add(AuditLog(
        company_id=current_user.company_id,
        actor_id=current_user.id,
        action="set_password",
        entity_type="employee",
        entity_id=emp.id,
    ))
    db.commit()
    return {"message": f"Password set for '{emp.name}'.", "employee_id": emp.id}


# ── POST /auth/change-password ───────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    current_user.password_hash = hash_password(payload.new_password)
    # Changing your password revokes every other session (the classic reason
    # people change it is that they think someone else has access).
    current_user.refresh_token = None
    db.add(AuditLog(
        company_id=current_user.company_id,
        actor_id=current_user.id,
        action="change_password",
        entity_type="employee",
        entity_id=current_user.id,
    ))
    db.commit()
    return {"message": "Password changed."}