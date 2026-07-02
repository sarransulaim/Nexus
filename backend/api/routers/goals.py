"""
goals.py — Goals / OKRs REST API
================================
Backs the Goals page. Company-scoped (uses the authenticated user's company),
so it's safe under the single-tenant pilot and forward-compatible with tenancy.
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import Goal, GoalTask, Employee
from api.security import get_current_user

router = APIRouter()


class GoalCreate(BaseModel):
    title: str
    description: str = ""
    target_date: date | None = None


class ProgressUpdate(BaseModel):
    progress_pct: float


@router.get("/")
def list_goals(db: Session = Depends(get_db), current_user: Employee = Depends(get_current_user)):
    """Goals with owner name + linked-task count. Managers see the whole company;
    employees see only their own (the page is labelled 'My Goals' for them)."""
    q = db.query(Goal).filter(Goal.company_id == current_user.company_id)
    if current_user.system_role != "manager":
        q = q.filter(Goal.employee_id == current_user.id)
    goals = q.order_by(Goal.created_at.desc()).all()
    emp_map = {e.id: e.name for e in db.query(Employee)
               .filter(Employee.company_id == current_user.company_id).all()}
    out = []
    for g in goals:
        out.append({
            "id": g.id,
            "title": g.title,
            "description": g.description or "",
            "progress_pct": round(g.progress_pct or 0, 1),
            "status": g.status,
            "target_date": str(g.target_date) if g.target_date else None,
            "owner": emp_map.get(g.employee_id, "—"),
            "owner_id": g.employee_id,
            "linked_tasks": db.query(GoalTask).filter(GoalTask.goal_id == g.id).count(),
        })
    return {"goals": out}


@router.post("/")
def create_goal(payload: GoalCreate, db: Session = Depends(get_db),
                current_user: Employee = Depends(get_current_user)):
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Goal title is required.")
    g = Goal(
        company_id=current_user.company_id,
        employee_id=current_user.id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        target_date=payload.target_date,
    )
    db.add(g); db.commit(); db.refresh(g)
    return {"id": g.id, "message": "Goal created."}


@router.patch("/{goal_id}/progress")
def update_progress(goal_id: int, payload: ProgressUpdate, db: Session = Depends(get_db),
                    current_user: Employee = Depends(get_current_user)):
    q = db.query(Goal).filter(Goal.id == goal_id,
                              Goal.company_id == current_user.company_id)
    # Employees may only update their OWN goal; managers may update any. This
    # mirrors list_goals' role-scoping — without it any employee could overwrite
    # a peer's or the manager's goal progress.
    if current_user.system_role != "manager":
        q = q.filter(Goal.employee_id == current_user.id)
    g = q.first()
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found.")
    g.progress_pct = max(0.0, min(100.0, payload.progress_pct))
    g.status = "completed" if g.progress_pct >= 100 else "active"
    db.commit()
    return {"id": g.id, "progress_pct": g.progress_pct, "status": g.status}
