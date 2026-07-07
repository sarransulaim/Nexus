"""
files.py — File Intelligence Router
=====================================
Endpoints:
  POST   /files/upload              → upload, analyze, return preview
  POST   /files/{id}/execute        → user confirmed, create entities
  GET    /files/recent              → list past uploads
  GET    /files/{id}                → get one file's details
  DELETE /files/{id}                → delete file + extraction

Auth: All endpoints require manager (employees can't upload files yet).
Files stored at: ./uploads/{company_id}/{uuid}.{ext}
"""

import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime, date, timezone
from typing import Optional, List

import aiofiles
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.core import get_db
from database.models import (
    UploadedFile, FileExtraction, Project, Task, Employee, Notification,
    Meeting, AuditLog,
)
from api.security import get_current_user
from api.ws_manager import notifier
from api.file_intelligence import analyze_file, MAX_FILE_SIZE

router = APIRouter()

# Where files live on disk
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads")).resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _require_manager(user: Employee):
    if user.system_role != "manager":
        raise HTTPException(status_code=403, detail="Manager access required.")


def _build_employee_context(db: Session, company_id: int) -> list:
    """Gather all employees for the AI to consider when assigning tasks."""
    emps = db.query(Employee).filter(
        Employee.company_id == company_id,
        Employee.is_active  == True,
    ).all()
    return [
        {"id": e.id, "name": e.name, "role": e.role, "team": e.team, "skills": e.skills}
        for e in emps
    ]


async def broadcast_db_update():
    await notifier.broadcast("SYNC_REQUIRED")


def _parse_date(value):
    """Try multiple formats. Returns datetime.date or None."""
    if not value or value in ("null", "None", ""):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    return None


def _find_employee_by_name(db: Session, company_id: int, name_hint: str) -> Optional[Employee]:
    """Best-effort fuzzy match — used when AI provides attendee names."""
    if not name_hint:
        return None
    n = name_hint.strip().lower()
    emps = db.query(Employee).filter(
        Employee.company_id == company_id,
        Employee.is_active  == True,
    ).all()
    # Exact match first
    for e in emps:
        if e.name.lower() == n:
            return e
    # Substring match
    for e in emps:
        if n in e.name.lower() or e.name.lower() in n:
            return e
    return None


def _find_employee_by_skill(db: Session, company_id: int, skill_hint: str) -> Optional[Employee]:
    """Find employee whose skills match a hint. Returns least-loaded match."""
    if not skill_hint:
        return None
    hints = [h.strip().lower() for h in skill_hint.replace(",", " ").split() if h.strip()]
    emps = db.query(Employee).filter(
        Employee.company_id == company_id,
        Employee.is_active  == True,
        Employee.system_role != "manager",
    ).all()

    # Score by skill overlap and active workload
    best = None
    best_score = -1
    for e in emps:
        skills = (e.skills or "").lower()
        score = sum(1 for h in hints if h in skills)
        if score > best_score:
            best = e
            best_score = score
    return best if best_score > 0 else None


# ═══════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════

class ExecuteRequest(BaseModel):
    # Optional override — frontend can edit the AI's proposal before executing
    edited_actions: Optional[list] = None


# ═══════════════════════════════════════════════════════════════
# POST /files/upload
# ═══════════════════════════════════════════════════════════════

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    1. Save file to disk under uploads/{company_id}/
    2. Run AI analysis to extract structured intent
    3. Save UploadedFile + FileExtraction records
    4. Return preview to frontend for confirmation
    """
    _require_manager(current_user)

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    # Build storage path
    company_dir = UPLOAD_DIR / str(current_user.company_id)
    company_dir.mkdir(parents=True, exist_ok=True)

    ext             = Path(file.filename).suffix
    stored_filename = f"{uuid.uuid4().hex}{ext}"
    file_path       = company_dir / stored_filename

    # Stream to disk with size check
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as out:
            while chunk := await file.read(1 << 20):    # 1MB chunks
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File exceeds 50MB limit.")
                await out.write(chunk)
    except HTTPException:
        if file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    # Create DB record for the file
    uploaded = UploadedFile(
        company_id        = current_user.company_id,
        uploader_id       = current_user.id,
        original_filename = file.filename,
        stored_filename   = stored_filename,
        file_path         = str(file_path),
        file_size         = written,
        source            = "dashboard",
    )
    db.add(uploaded)
    db.commit()
    db.refresh(uploaded)

    # Run AI analysis (synchronous — manager waits ~5-15s for response)
    employee_context = _build_employee_context(db, current_user.company_id)
    try:
        analysis = analyze_file(str(file_path), file.filename, employee_context)
    except Exception as e:
        analysis = {
            "type":       "general",
            "title":      file.filename,
            "summary":    f"Analysis failed: {e}",
            "confidence": 0,
            "needs_review":     True,
            "proposed_actions": [],
            "raw_extract":      "",
        }

    # Save extraction record
    uploaded.ai_analyzed    = True
    uploaded.extracted_text = (analysis.get("raw_extract") or "")[:50_000]

    extraction = FileExtraction(
        file_id         = uploaded.id,
        extraction_type = analysis.get("type", "general"),
        result_json     = analysis,
    )
    db.add(extraction)

    db.add(AuditLog(
        company_id  = current_user.company_id,
        actor_id    = current_user.id,
        action      = "file_uploaded",
        entity_type = "uploaded_file",
        entity_id   = uploaded.id,
        new_value   = {"filename": file.filename, "size": written},
    ))
    db.commit()
    db.refresh(extraction)

    # Auto-ingest into the knowledge base (non-blocking) so the AI can recall
    # this document's contents later via search_knowledge.
    try:
        import rag
        if uploaded.extracted_text and uploaded.extracted_text.strip():
            rag.index_async(
                current_user.company_id, "uploaded_file", uploaded.id,
                uploaded.extracted_text, meta={"filename": uploaded.original_filename},
            )
    except Exception:
        pass

    return {
        "file_id":       uploaded.id,
        "extraction_id": extraction.id,
        "filename":      uploaded.original_filename,
        "size":          uploaded.file_size,
        "analysis":      analysis,
    }


# ═══════════════════════════════════════════════════════════════
# POST /files/{file_id}/execute
# ═══════════════════════════════════════════════════════════════

@router.post("/{file_id}/execute")
async def execute_extraction(
    file_id: int,
    payload: ExecuteRequest,
    background_tasks: BackgroundTasks,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    User confirmed the AI's proposal (possibly with edits).
    Create the actual entities in the DB.
    """
    _require_manager(current_user)

    uploaded = db.query(UploadedFile).filter(
        UploadedFile.id         == file_id,
        UploadedFile.company_id == current_user.company_id,
    ).first()
    if not uploaded:
        raise HTTPException(status_code=404, detail="File not found.")

    extraction = db.query(FileExtraction).filter(
        FileExtraction.file_id == file_id,
    ).order_by(FileExtraction.id.desc()).first()
    if not extraction:
        raise HTTPException(status_code=404, detail="No extraction found for this file.")

    if extraction.executed:
        raise HTTPException(status_code=400, detail="This extraction has already been executed.")

    # Use edited actions if user changed something, otherwise AI's proposal
    actions = payload.edited_actions if payload.edited_actions is not None \
        else (extraction.result_json or {}).get("proposed_actions", [])

    created = {
        "projects":  [],
        "tasks":     [],
        "meetings":  [],
        "employees": [],
        "skipped":   [],
    }

    for action_obj in actions:
        action_type = action_obj.get("action")
        details     = action_obj.get("details", {}) or {}

        try:
            # ── create_project + nested tasks ────────────────
            if action_type == "create_project":
                proj = Project(
                    company_id     = current_user.company_id,
                    name           = details.get("name", "Untitled Project")[:300],
                    description    = details.get("description", "")[:5000],
                    priority       = details.get("priority", "Medium"),
                    due_date       = _parse_date(details.get("due_date")),
                    created_by     = current_user.id,
                    source_file_id = uploaded.id,
                )
                db.add(proj)
                db.flush()

                # Owner assignment: ONLY use owner_id from edited_actions.
                # Manager explicitly picks each owner in the UI — no auto-matching.
                for task_def in details.get("tasks", []):
                    owner_id = task_def.get("owner_id")   # set by manager in UI
                    # Validate the owner_id belongs to this company if provided
                    valid_owner_id = None
                    if owner_id:
                        owner_check = db.query(Employee).filter(
                            Employee.id         == owner_id,
                            Employee.company_id == current_user.company_id,
                        ).first()
                        if owner_check:
                            valid_owner_id = owner_check.id

                    task = Task(
                        company_id  = current_user.company_id,
                        project_id  = proj.id,
                        title       = task_def.get("title", "Untitled Task")[:500],
                        description = task_def.get("description", "")[:5000],
                        owner_id    = valid_owner_id,
                        priority    = task_def.get("priority", "Medium"),
                        due_date    = _parse_date(task_def.get("due_date")),
                    )
                    db.add(task)
                    if valid_owner_id:
                        db.add(Notification(
                            company_id   = current_user.company_id,
                            recipient_id = valid_owner_id,
                            type         = "task_assigned",
                            title        = "New Task from Uploaded File",
                            message      = f"You've been assigned: {task.title}",
                        ))
                    created["tasks"].append(task.title)

                created["projects"].append(proj.name)

            # ── create_task (standalone) ─────────────────────
            elif action_type == "create_task":
                owner_id = details.get("owner_id")
                valid_owner_id = None
                if owner_id:
                    owner_check = db.query(Employee).filter(
                        Employee.id         == owner_id,
                        Employee.company_id == current_user.company_id,
                    ).first()
                    if owner_check:
                        valid_owner_id = owner_check.id

                task = Task(
                    company_id  = current_user.company_id,
                    title       = details.get("title", "Untitled Task")[:500],
                    description = details.get("description", "")[:5000],
                    owner_id    = valid_owner_id,
                    priority    = details.get("priority", "Medium"),
                    due_date    = _parse_date(details.get("due_date")),
                )
                db.add(task)
                if valid_owner_id:
                    db.add(Notification(
                        company_id   = current_user.company_id,
                        recipient_id = valid_owner_id,
                        type         = "task_assigned",
                        title        = "New Task from Uploaded File",
                        message      = f"You've been assigned: {task.title}",
                    ))
                created["tasks"].append(task.title)

            # ── schedule_meeting ─────────────────────────────
            elif action_type == "schedule_meeting":
                meeting = Meeting(
                    company_id       = current_user.company_id,
                    topic            = details.get("topic", "Untitled Meeting")[:500],
                    scheduled_time   = details.get("scheduled_time", ""),
                    duration_minutes = details.get("duration_minutes"),
                    location         = details.get("location", ""),
                    created_by       = current_user.id,
                )
                db.add(meeting)
                db.flush()
                # Link attendees by name
                for name in details.get("attendee_names", []):
                    emp = _find_employee_by_name(db, current_user.company_id, name)
                    if emp:
                        meeting.attendees.append(emp)
                created["meetings"].append(meeting.topic)

            # ── add_employee ────────────────────────────────
            elif action_type == "add_employee":
                # Parse experience safely — AI may send "8", 8, "8 years", or null
                raw_exp = details.get("experience")
                experience = 0
                if raw_exp not in (None, "", "null"):
                    try:
                        # Extract digits in case it's "8 years"
                        digits = "".join(c for c in str(raw_exp) if c.isdigit())
                        experience = int(digits) if digits else 0
                    except (ValueError, TypeError):
                        experience = 0

                # Email — only keep if it looks like an email
                raw_email = details.get("email")
                email = None
                if raw_email and raw_email not in ("null", "") and "@" in str(raw_email):
                    email = str(raw_email)[:254]

                emp = Employee(
                    company_id  = current_user.company_id,
                    name        = details.get("name", "Unnamed")[:200],
                    role        = details.get("role", "Employee"),
                    team        = details.get("team", "Unassigned"),
                    email       = email,
                    experience  = experience,
                    system_role = "employee",
                    skills      = details.get("skills", ""),
                    is_active   = True,
                )
                db.add(emp)
                created["employees"].append(emp.name)

            else:
                created["skipped"].append(action_type or "unknown")

        except Exception as e:
            created["skipped"].append(f"{action_type} ({e})")

    # Mark extraction as executed
    extraction.executed    = True
    extraction.executed_at = datetime.now(timezone.utc)
    extraction.confirmed_by = current_user.id
    extraction.confirmed_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        company_id  = current_user.company_id,
        actor_id    = current_user.id,
        action      = "file_executed",
        entity_type = "file_extraction",
        entity_id   = extraction.id,
        new_value   = created,
    ))
    db.commit()

    background_tasks.add_task(broadcast_db_update)

    return {
        "status":  "success",
        "created": created,
    }


# ═══════════════════════════════════════════════════════════════
# GET /files/recent
# ═══════════════════════════════════════════════════════════════

@router.get("/recent")
def list_recent_files(
    limit: int = 20,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List recent uploads for the current company."""
    files = db.query(UploadedFile).filter(
        UploadedFile.company_id == current_user.company_id,
    ).order_by(UploadedFile.created_at.desc()).limit(limit).all()

    result = []
    for f in files:
        extraction = db.query(FileExtraction).filter(
            FileExtraction.file_id == f.id,
        ).order_by(FileExtraction.id.desc()).first()
        result.append({
            "id":             f.id,
            "filename":       f.original_filename,
            "size":           f.file_size,
            "uploader_id":    f.uploader_id,
            "created_at":     f.created_at,
            "ai_analyzed":    f.ai_analyzed,
            "extraction_type": extraction.extraction_type if extraction else None,
            "executed":       extraction.executed if extraction else False,
        })
    return result


# ═══════════════════════════════════════════════════════════════
# GET /files/{file_id}
# ═══════════════════════════════════════════════════════════════

@router.get("/{file_id}")
def get_file_detail(
    file_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full detail for one file including its extraction."""
    f = db.query(UploadedFile).filter(
        UploadedFile.id         == file_id,
        UploadedFile.company_id == current_user.company_id,
    ).first()
    if not f:
        raise HTTPException(status_code=404, detail="File not found.")

    extraction = db.query(FileExtraction).filter(
        FileExtraction.file_id == file_id,
    ).order_by(FileExtraction.id.desc()).first()

    return {
        "id":          f.id,
        "filename":    f.original_filename,
        "size":        f.file_size,
        "created_at":  f.created_at,
        "ai_analyzed": f.ai_analyzed,
        "extraction":  {
            "id":         extraction.id,
            "type":       extraction.extraction_type,
            "result":     extraction.result_json,
            "executed":   extraction.executed,
            "executed_at": extraction.executed_at,
        } if extraction else None,
    }


# ═══════════════════════════════════════════════════════════════
# DELETE /files/{file_id}
# ═══════════════════════════════════════════════════════════════

@router.delete("/{file_id}")
def delete_file(
    file_id: int,
    current_user: Employee = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove file from disk and DB. Extractions cascade."""
    _require_manager(current_user)

    f = db.query(UploadedFile).filter(
        UploadedFile.id         == file_id,
        UploadedFile.company_id == current_user.company_id,
    ).first()
    if not f:
        raise HTTPException(status_code=404, detail="File not found.")

    # Remove from disk
    try:
        if f.file_path and os.path.exists(f.file_path):
            os.unlink(f.file_path)
    except Exception:
        pass

    db.add(AuditLog(
        company_id  = current_user.company_id,
        actor_id    = current_user.id,
        action      = "file_deleted",
        entity_type = "uploaded_file",
        entity_id   = f.id,
        old_value   = {"filename": f.original_filename},
    ))
    db.delete(f)
    db.commit()

    return {"status": "deleted"}