from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from database.core import get_db
from database.models import Meeting, Employee
from api.security import get_current_user, require_manager

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class MeetingCreate(BaseModel):
    topic: str
    scheduled_time: str
    attendee_ids: List[int]  # list of employee IDs

class MeetingUpdate(BaseModel):
    topic: Optional[str] = None
    scheduled_time: Optional[str] = None
    attendee_ids: Optional[List[int]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_meeting(meeting: Meeting) -> dict:
    return {
        "id": meeting.id,
        "topic": meeting.topic,
        "scheduled_time": meeting.scheduled_time,
        # Return both the proper list AND a legacy comma string so the
        # existing frontend code doesn't break during transition
        "attendee_ids": ",".join(str(a.id) for a in meeting.attendees),
        "attendees": [
            {"id": a.id, "name": a.name, "role": a.role}
            for a in meeting.attendees
        ],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/")
def get_all_meetings(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    meetings = db.query(Meeting).filter(Meeting.company_id == current_user.company_id).all()
    return {"meetings": [format_meeting(m) for m in meetings]}


@router.post("/")
def create_meeting(
    payload: MeetingCreate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(require_manager),
):
    meeting = Meeting(
        company_id=current_user.company_id,
        topic=payload.topic,
        scheduled_time=payload.scheduled_time,
    )
    # Attach employees via the junction table (scoped to the manager's company)
    employees = db.query(Employee).filter(
        Employee.id.in_(payload.attendee_ids),
        Employee.company_id == current_user.company_id,
    ).all()
    if not employees:
        raise HTTPException(status_code=404, detail="No valid employees found for provided IDs")
    meeting.attendees = employees
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return {"message": "Meeting created", "meeting": format_meeting(meeting)}


@router.patch("/{meeting_id}")
def update_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(require_manager),
):
    meeting = db.query(Meeting).filter(
        Meeting.id == meeting_id,
        Meeting.company_id == current_user.company_id,
    ).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if payload.topic is not None:
        meeting.topic = payload.topic
    if payload.scheduled_time is not None:
        meeting.scheduled_time = payload.scheduled_time
    if payload.attendee_ids is not None:
        employees = db.query(Employee).filter(
            Employee.id.in_(payload.attendee_ids),
            Employee.company_id == current_user.company_id,
        ).all()
        meeting.attendees = employees

    db.commit()
    db.refresh(meeting)
    return {"message": "Meeting updated", "meeting": format_meeting(meeting)}


@router.delete("/{meeting_id}")
def delete_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(require_manager),
):
    meeting = db.query(Meeting).filter(
        Meeting.id == meeting_id,
        Meeting.company_id == current_user.company_id,
    ).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    db.delete(meeting)
    db.commit()
    return {"message": f"Meeting {meeting_id} deleted"}