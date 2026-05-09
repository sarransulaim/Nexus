"""
google_router.py — Google OAuth Endpoints
==========================================
GET  /google/connect/{employee_id}  → redirect to Google auth
GET  /google/callback               → handle OAuth callback
GET  /google/status/{employee_id}   → check if connected
POST /google/disconnect/{employee_id} → revoke access
GET  /google/emails/{employee_id}   → fetch recent emails
GET  /google/calendar/{employee_id} → fetch upcoming events
GET  /google/availability/{employee_id} → check free slots
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from database.core import get_db
from api.google_auth import (
    get_google_auth_url,
    handle_google_callback,
    is_google_connected,
    disconnect_google,
)
from api.google_services import (
    read_recent_emails,
    get_upcoming_events,
    check_availability,
    get_focus_time_suggestions,
    draft_email_reply,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Step 1 — Employee clicks "Connect Google"
# We redirect them to Google's consent screen
# ---------------------------------------------------------------------------
@router.get("/connect/{employee_id}")
def connect_google(employee_id: int, db: Session = Depends(get_db)):
    """
    Generates a Google OAuth URL and redirects the employee to it.
    After they approve, Google sends them back to /callback.
    """
    auth_url = get_google_auth_url(employee_id)
    return RedirectResponse(url=auth_url)


# ---------------------------------------------------------------------------
# Step 2 — Google redirects back here after user approves
# ---------------------------------------------------------------------------
@router.get("/callback")
def google_callback(
    code:  str = Query(...),
    state: str = Query(...),
    db:    Session = Depends(get_db)
):
    """
    Handles the OAuth callback from Google.
    Exchanges the code for tokens and stores them.
    """
    try:
        result      = handle_google_callback(code=code, state=state, db=db)
        employee_id = result["employee_id"]

        # Return a clean success page the user can see
        return JSONResponse(content={
            "status":      "success",
            "message":     f"✅ Google Workspace connected for Employee ID {employee_id}!",
            "next_step":   "Go back to Nexus and tell your AI agent: 'check my emails' or 'check my calendar'",
            "employee_id": employee_id,
        })
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(e)}
        )


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------
@router.get("/status/{employee_id}")
def google_status(employee_id: int, db: Session = Depends(get_db)):
    """Returns whether an employee has connected their Google account."""
    connected = is_google_connected(employee_id, db)
    return {
        "employee_id": employee_id,
        "connected":   connected,
        "message":     "Google Workspace connected" if connected else "Google account not connected"
    }


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------
@router.post("/disconnect/{employee_id}")
def google_disconnect(employee_id: int, db: Session = Depends(get_db)):
    """Revokes Google access for an employee."""
    disconnect_google(employee_id, db)
    return {"message": f"Google Workspace disconnected for employee {employee_id}"}


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------
@router.get("/emails/{employee_id}")
def get_emails(
    employee_id:  int,
    max_results:  int = Query(default=10, le=50),
    db:           Session = Depends(get_db)
):
    """Fetches and summarizes recent emails for an employee."""
    if not is_google_connected(employee_id, db):
        raise HTTPException(status_code=400, detail="Google account not connected.")
    result = read_recent_emails(employee_id, max_results, db)
    return {"summary": result}


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
@router.get("/calendar/{employee_id}")
def get_calendar(
    employee_id: int,
    days:        int = Query(default=7, le=30),
    db:          Session = Depends(get_db)
):
    """Fetches upcoming calendar events for an employee."""
    if not is_google_connected(employee_id, db):
        raise HTTPException(status_code=400, detail="Google account not connected.")
    result = get_upcoming_events(employee_id, days, db)
    return {"schedule": result}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
@router.get("/availability/{employee_id}")
def get_availability(
    employee_id:      int,
    date:             str = Query(..., description="Date in YYYY-MM-DD format"),
    duration_minutes: int = Query(default=60),
    db:               Session = Depends(get_db)
):
    """Checks free slots for an employee on a given date."""
    result = check_availability(employee_id, date, duration_minutes, db)
    return {"availability": result}


# ---------------------------------------------------------------------------
# Focus time analysis
# ---------------------------------------------------------------------------
@router.get("/focus-time/{employee_id}")
def get_focus_time(employee_id: int, db: Session = Depends(get_db)):
    """AI analysis of calendar patterns to suggest focus time blocks."""
    if not is_google_connected(employee_id, db):
        raise HTTPException(status_code=400, detail="Google account not connected.")
    result = get_focus_time_suggestions(employee_id, db)
    return {"analysis": result}