from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.core import get_db
from database.models import Employee, PeerRequest
from api.security import get_current_user

router = APIRouter()

@router.get("/")
def get_all_employees(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    employees = db.query(Employee).filter(Employee.company_id == current_user.company_id).all()
    
    emp_list = []
    for e in employees:
        assisting_requests = db.query(PeerRequest).filter(
            PeerRequest.recipient_id == e.id, 
            PeerRequest.status == "Accepted"
        ).all()
        
        # --- THE FIX: Look up the sender's name instead of just printing their ID! ---
        formatted_assisting_list = []
        for r in assisting_requests:
            # Query the database to find the person who asked for help
            sender = db.query(Employee).filter(Employee.id == r.sender_id).first()
            
            # If the database finds them, use their name. Otherwise, fallback to ID.
            sender_name = sender.name if sender else str(r.sender_id)
            formatted_assisting_list.append(f"Emp {sender_name}: {r.topic}")
            
        emp_dict = {
            "id": e.id,
            "name": e.name,
            "role": e.role,
            "experience": e.experience,
            "skills": e.skills,
            "gender": e.gender,
            "age": e.age,
            "team": e.team,
            # Computed dynamically — removed stale DB columns
            "task_count": len(e.tasks),
            "completed_count": sum(1 for t in e.tasks if t.is_completed),
            "tasks": [
                {"id": t.id, "title": t.title, "is_completed": t.is_completed, "priority": t.priority} 
                for t in e.tasks
            ],
            "assisting": formatted_assisting_list
        }
        emp_list.append(emp_dict)
        
    return {"employees": emp_list}