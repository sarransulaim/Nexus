from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.core import get_db
from database.models import Task

router = APIRouter()

@router.get("/")
def get_all_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).all()
    
    task_list = []
    for t in tasks:
        task_dict = {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "owner_id": t.owner_id,
            "is_completed": t.is_completed,
            "priority": t.priority,
            "due_date": t.due_date,
            "subtasks": [
                {"id": st.id, "title": st.title, "is_completed": st.is_completed} 
                for st in t.subtasks
            ],
            # --- THE MISSING PLUMBING ---
            "peer_requests": [
                {
                    "id": pr.id,
                    "sender_id": pr.sender_id,
                    "recipient_id": pr.recipient_id,
                    "topic": pr.topic,
                    "status": pr.status
                }
                for pr in t.peer_requests
            ]
        }
        task_list.append(task_dict)
            
    return {"tasks": task_list}