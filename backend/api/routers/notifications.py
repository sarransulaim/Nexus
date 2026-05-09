"""
notifications.py — Notifications Router
=========================================
GET  /notifications/         → get all notifications for current user
POST /notifications/read     → mark one as read
POST /notifications/read-all → mark all as read
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database.core import get_db
from database.models import Notification, Employee

router = APIRouter()


class MarkReadPayload(BaseModel):
    notification_id: int


@router.get("/{employee_id}")
def get_notifications(employee_id: int, db: Session = Depends(get_db)):
    """
    Get the 20 most recent notifications for an employee.
    Unread ones come first.
    """
    notifications = (
        db.query(Notification)
        .filter(Notification.recipient_id == employee_id)
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .limit(20)
        .all()
    )

    return {
        "notifications": [
            {
                "id":          n.id,
                "type":        n.type,
                "title":       n.title,
                "message":     n.message,
                "is_read":     n.is_read,
                "entity_type": n.entity_type,
                "entity_id":   n.entity_id,
                "created_at":  str(n.created_at),
            }
            for n in notifications
        ],
        "unread_count": sum(1 for n in notifications if not n.is_read)
    }


@router.post("/read/{notification_id}")
def mark_read(notification_id: int, db: Session = Depends(get_db)):
    """Mark a single notification as read."""
    notif = db.query(Notification).filter(Notification.id == notification_id).first()
    if notif:
        notif.is_read = True
        db.commit()
    return {"status": "ok"}


@router.post("/read-all/{employee_id}")
def mark_all_read(employee_id: int, db: Session = Depends(get_db)):
    """Mark all notifications for an employee as read."""
    db.query(Notification).filter(
        Notification.recipient_id == employee_id,
        Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return {"status": "ok"}