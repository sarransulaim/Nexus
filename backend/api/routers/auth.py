"""
auth.py — Authentication Router
=================================
Handles:
  - POST /auth/login       → returns access + refresh tokens
  - POST /auth/refresh     → swap refresh token for a new access token
  - POST /auth/logout      → invalidate the refresh token
  - POST /auth/setup       → first-time: create manager account
  - GET  /auth/me          → returns current logged-in user info
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from database.core import get_db
from database.models import Employee
from api.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    decode_token, get_current_user
)

router = APIRouter()


# ---------------------------------------------------------------------------
# SCHEMAS — what the frontend sends and receives
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    name: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class SetupRequest(BaseModel):
    name: str
    password: str
    secret_key: str   # a one-time secret so random people can't create managers


# ---------------------------------------------------------------------------
# POST /auth/setup
# First-time manager account creation.
# ---------------------------------------------------------------------------
@router.post("/setup")
def setup_manager(payload: SetupRequest, db: Session = Depends(get_db)):
    """
    Creates the first manager account.
    Only works if no manager exists yet OR if the secret key matches.

    HOW TO USE:
    Send a POST request with:
    { "name": "Director", "password": "yourpassword", "secret_key": "NEXUS_SETUP_2026" }
    """
    import os
    setup_secret = os.getenv("SETUP_SECRET", "NEXUS_SETUP_2026")

    if payload.secret_key != setup_secret:
        raise HTTPException(status_code=403, detail="Invalid setup secret key.")

    existing = db.query(Employee).filter(Employee.system_role == "manager").first()
    if existing:
        raise HTTPException(status_code=400, detail="Manager account already exists.")

    manager = Employee(
        name=payload.name,
        role="Chief of Staff",
        system_role="manager",
        password_hash=hash_password(payload.password),
        is_active=True,
        team="Management"
    )
    db.add(manager)
    db.commit()
    db.refresh(manager)

    return {"message": f"Manager account '{manager.name}' created successfully."}


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------
@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Login with name + password.
    Returns an access token (8hrs) and a refresh token (30 days).

    WHAT THE FRONTEND DOES WITH THIS:
    - Stores the access token in memory
    - Stores the refresh token in sessionStorage
    - Sends the access token in every future API request header
    """
    # 1. Find the employee by name (case-insensitive)
    employee = db.query(Employee).filter(
        Employee.name.ilike(payload.name.strip()),
        Employee.is_active == True
    ).first()

    # 2. Check if they exist and password is correct
    if not employee or not employee.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid name or password."
        )

    if not verify_password(payload.password, employee.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid name or password."
        )

    # 3. Update last login timestamp
    employee.last_login = datetime.now(timezone.utc)

    # 4. Create tokens
    access_token  = create_access_token(employee.id, employee.system_role, employee.name)
    refresh_token = create_refresh_token(employee.id)

    # 5. Store refresh token hash in DB (so we can invalidate it on logout)
    from api.security import hash_password as hash_token
    employee.refresh_token = hash_token(refresh_token)
    db.commit()

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":   employee.id,
            "name": employee.name,
            "role": employee.system_role,
            "team": employee.team
        }
    }


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------
@router.post("/refresh")
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    """
    When the access token expires after 8 hours, the frontend
    sends the refresh token here to get a new access token.
    User never has to log in again for 30 days.
    """
    token_data = decode_token(payload.refresh_token)

    if token_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    employee = db.query(Employee).filter(
        Employee.id == int(token_data["sub"]),
        Employee.is_active == True
    ).first()

    if not employee:
        raise HTTPException(status_code=401, detail="User not found.")

    # Issue a fresh access token
    new_access_token = create_access_token(
        employee.id, employee.system_role, employee.name
    )

    return {
        "access_token": new_access_token,
        "token_type": "bearer"
    }


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------
@router.post("/logout")
def logout(
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Clears the refresh token from the database.
    Even if someone has the old tokens, they can't refresh anymore.
    """
    current_user.refresh_token = None
    db.commit()
    return {"message": "Logged out successfully."}


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------
@router.get("/me")
def get_me(current_user: Employee = Depends(get_current_user)):
    """
    Returns the currently logged-in user's info.
    The frontend calls this on app load to restore the session.
    """
    return {
        "id":          current_user.id,
        "name":        current_user.name,
        "role":        current_user.system_role,
        "team":        current_user.team,
        "last_login":  current_user.last_login,
    }


# ---------------------------------------------------------------------------
# POST /auth/employees/create
# Manager creates employee accounts (with temporary passwords)
# ---------------------------------------------------------------------------
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
    db: Session = Depends(get_db)
):
    """
    Manager creates a new employee account.
    Employee gets a temporary password they can change later.
    """
    existing = db.query(Employee).filter(
        Employee.name.ilike(payload.name.strip())
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"An employee named '{payload.name}' already exists."
        )

    employee = Employee(
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
    db.add(employee)
    db.commit()
    db.refresh(employee)

    return {
        "message": f"Employee '{employee.name}' created successfully.",
        "employee_id": employee.id
    }


# ---------------------------------------------------------------------------
# POST /auth/set-password
# Manager sets or resets password for any employee
# ---------------------------------------------------------------------------
class SetPasswordRequest(BaseModel):
    employee_id: int
    new_password: str

@router.post("/set-password")
def set_employee_password(
    payload: SetPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Sets or resets a password for an existing employee.
    Used when:
    - Manager onboards an employee added via Claude tool
    - Manager resets a forgotten password
    """
    employee = db.query(Employee).filter(Employee.id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    employee.password_hash = hash_password(payload.new_password)
    db.commit()

    return {
        "message": f"Password set for '{employee.name}'. They can now log in.",
        "employee_id": employee.id,
        "name": employee.name
    }


# ---------------------------------------------------------------------------
# POST /auth/change-password
# Employee changes their own password after first login
# ---------------------------------------------------------------------------
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Employee changes their own password."""
    if not verify_password(payload.current_password, current_user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password changed successfully."}