"""
seed_demo.py — Nexus Command Demo Seeder
==========================================
Wipes company_id=1 and loads a realistic, stress-testable organization:
  - 3 teams (Engineering, Design, Marketing)
  - 1 manager + 8 employees with full details (skills, experience, age, role)
  - 3 projects with deadlines + roadmaps
  - ~22 tasks distributed DELIBERATELY UNEVENLY so every system fires:
      • someone OVERLOADED  → proactive overload trigger + negotiation/rebalance
      • some OVERDUE tasks   → proactive overdue trigger + briefings
      • some APPROACHING     → proactive deadline trigger
      • task DEPENDENCIES    → X must finish before Y (the DAG feature)
      • subtasks, comments, meetings, goals
  - Everyone's password is "demo123" so you can log in as anyone to test.

USAGE:
    python seed_demo.py

SAFETY: Only touches company_id=1. Prints exactly what it deletes before doing it.
Run from the backend/ folder (same place as create_tables.py).
"""

import sys
from datetime import date, timedelta, datetime, timezone

from database.core import SessionLocal, engine
from database.models import (
    Company, Team, Employee, Project, Task, Subtask, TaskComment,
    TaskDependency, Meeting, Goal, GoalTask, Notification,
    project_members, meeting_attendees,
)
from api.security import hash_password

COMPANY_ID = 1
TODAY = date.today()
DEMO_PASSWORD = "demo123"


def _d(days_from_today: int) -> date:
    return TODAY + timedelta(days=days_from_today)


# ════════════════════════════════════════════════════════════════
# WIPE — only company 1, and show what's being removed
# ════════════════════════════════════════════════════════════════

def wipe_company(db):
    company = db.query(Company).filter(Company.id == COMPANY_ID).first()
    if not company:
        print(f"No company with id={COMPANY_ID} — will create fresh.")
        return

    emp_count  = db.query(Employee).filter(Employee.company_id == COMPANY_ID).count()
    task_count = db.query(Task).filter(Task.company_id == COMPANY_ID).count()
    proj_count = db.query(Project).filter(Project.company_id == COMPANY_ID).count()
    print(f"⚠️  About to wipe company {COMPANY_ID} ('{company.name}'): "
          f"{emp_count} employees, {task_count} tasks, {proj_count} projects.")

    # Deleting the company cascades to employees, teams, projects, tasks, etc.
    # (cascade="all, delete-orphan" on the relationships handles the children).
    db.delete(company)
    db.commit()
    print("   ✅ Old company data removed.")


# ════════════════════════════════════════════════════════════════
# SEED
# ════════════════════════════════════════════════════════════════

def seed(db):
    # ── Company ──────────────────────────────────────────────────
    company = Company(id=COMPANY_ID, name="Nexus Command", slug="nexus-command", plan="enterprise")
    db.add(company)
    db.flush()

    # ── Teams ────────────────────────────────────────────────────
    eng = Team(company_id=COMPANY_ID, name="Engineering", description="Builds the product")
    design = Team(company_id=COMPANY_ID, name="Design", description="Product & brand design")
    mktg = Team(company_id=COMPANY_ID, name="Marketing", description="Growth & go-to-market")
    db.add_all([eng, design, mktg])
    db.flush()

    # ── Manager ──────────────────────────────────────────────────
    manager = Employee(
        company_id=COMPANY_ID, name="Mr Kurbi", email="kurbi@nexus.dev",
        role="Founder / CEO", system_role="manager",
        age=34, experience=12, gender="Male",
        skills="leadership, strategy, product vision, fundraising",
        password_hash=hash_password(DEMO_PASSWORD),
    )
    db.add(manager)
    db.flush()

    # ── Employees (full details, varied) ─────────────────────────
    # (name, email, role, team_obj, team_name, age, exp, gender, skills)
    people = [
        ("Sulaim Sarran",   "sulaim@nexus.dev",  "Senior Full-Stack Engineer", eng,    "Engineering", 29, 7, "Male",   "python, react, postgres, fastapi, system design"),
        ("Aisha Khan",      "aisha@nexus.dev",   "Backend Engineer",           eng,    "Engineering", 26, 4, "Female", "python, django, databases, api design"),
        ("Diego Morales",   "diego@nexus.dev",   "Frontend Engineer",          eng,    "Engineering", 31, 8, "Male",   "react, typescript, css, animation, accessibility"),
        ("Priya Nair",      "priya@nexus.dev",   "DevOps Engineer",            eng,    "Engineering", 33, 9, "Female", "aws, docker, kubernetes, ci/cd, monitoring"),
        ("Liam O'Brien",    "liam@nexus.dev",    "Lead Product Designer",      design, "Design",      30, 7, "Male",   "figma, ux research, prototyping, design systems"),
        ("Mei Tanaka",      "mei@nexus.dev",     "UI Designer",                design, "Design",      27, 5, "Female", "ui design, figma, branding, illustration"),
        ("Sofia Rossi",     "sofia@nexus.dev",   "Marketing Lead",             mktg,   "Marketing",   35, 11,"Female", "growth, content strategy, seo, analytics"),
        ("Noah Bennett",    "noah@nexus.dev",    "Content & Social",           mktg,   "Marketing",   24, 2, "Male",   "copywriting, social media, video, community"),
    ]
    emps = {}
    for name, email, role, team_obj, team_name, age, exp, gender, skills in people:
        e = Employee(
            company_id=COMPANY_ID, name=name, email=email, role=role,
            system_role="employee", team_id=team_obj.id, team=team_name,
            age=age, experience=exp, gender=gender, skills=skills,
            password_hash=hash_password(DEMO_PASSWORD),
        )
        db.add(e)
        emps[name] = e
    db.flush()

    # Team leads
    eng.lead_id = emps["Sulaim Sarran"].id
    design.lead_id = emps["Liam O'Brien"].id
    mktg.lead_id = emps["Sofia Rossi"].id

    # ── Projects (with deadlines / roadmap) ──────────────────────
    p_mobile = Project(
        company_id=COMPANY_ID, name="Mobile App v1.0",
        description="Native mobile app for retail clients. Roadmap: API → UI → integrate → launch.",
        status="active", priority="High", due_date=_d(21), created_by=manager.id,
    )
    p_redesign = Project(
        company_id=COMPANY_ID, name="Website Redesign",
        description="Full marketing-site redesign with new brand system.",
        status="active", priority="Medium", due_date=_d(35), created_by=manager.id,
    )
    p_launch = Project(
        company_id=COMPANY_ID, name="Q3 Product Launch",
        description="Go-to-market campaign for the v1.0 launch.",
        status="active", priority="Critical", due_date=_d(14), created_by=manager.id,
    )
    db.add_all([p_mobile, p_redesign, p_launch])
    db.flush()

    # Project members
    db.execute(project_members.insert(), [
        {"project_id": p_mobile.id,   "employee_id": emps["Sulaim Sarran"].id},
        {"project_id": p_mobile.id,   "employee_id": emps["Aisha Khan"].id},
        {"project_id": p_mobile.id,   "employee_id": emps["Diego Morales"].id},
        {"project_id": p_mobile.id,   "employee_id": emps["Priya Nair"].id},
        {"project_id": p_redesign.id, "employee_id": emps["Liam O'Brien"].id},
        {"project_id": p_redesign.id, "employee_id": emps["Mei Tanaka"].id},
        {"project_id": p_redesign.id, "employee_id": emps["Diego Morales"].id},
        {"project_id": p_launch.id,   "employee_id": emps["Sofia Rossi"].id},
        {"project_id": p_launch.id,   "employee_id": emps["Noah Bennett"].id},
        {"project_id": p_launch.id,   "employee_id": emps["Mei Tanaka"].id},
    ])

    # ── Tasks ────────────────────────────────────────────────────
    # helper
    def task(title, owner, project, priority, due_days, done=False, desc="", est=None):
        t = Task(
            company_id=COMPANY_ID, title=title, description=desc,
            owner_id=emps[owner].id if owner else None,
            project_id=project.id if project else None,
            priority=priority, is_completed=done,
            due_date=_d(due_days) if due_days is not None else None,
            estimated_hours=est,
            completed_at=datetime.now(timezone.utc) if done else None,
        )
        db.add(t); db.flush()
        return t

    # MOBILE APP — has a dependency chain (the DAG: API → UI → integrate)
    t_api    = task("Build mobile API endpoints", "Aisha Khan", p_mobile, "High", -2, desc="REST API for the app", est=20)   # OVERDUE
    t_ui     = task("Build app UI screens", "Diego Morales", p_mobile, "High", 5, desc="All screens in React Native", est=24)
    t_integ  = task("Integrate API with UI", "Sulaim Sarran", p_mobile, "High", 10, desc="Wire frontend to backend", est=16)
    t_qa     = task("QA + bug bash", "Priya Nair", p_mobile, "Medium", 18, desc="Full regression", est=12)

    # Dependencies: UI depends on API; Integrate depends on both; QA depends on integrate
    db.add_all([
        TaskDependency(task_id=t_ui.id,    depends_on_id=t_api.id),
        TaskDependency(task_id=t_integ.id, depends_on_id=t_api.id),
        TaskDependency(task_id=t_integ.id, depends_on_id=t_ui.id),
        TaskDependency(task_id=t_qa.id,    depends_on_id=t_integ.id),
    ])

    # OVERLOAD Sulaim deliberately (senior, ends up with many) → triggers overload + rebalance
    task("Refactor auth service", "Sulaim Sarran", p_mobile, "Medium", 7, est=8)
    task("Set up payment gateway", "Sulaim Sarran", p_mobile, "High", 3, est=10)        # APPROACHING
    task("Write API documentation", "Sulaim Sarran", p_mobile, "Low", 12, est=4)
    task("Code review backlog", "Sulaim Sarran", None, "Medium", 1, est=6)              # APPROACHING
    task("Database migration plan", "Sulaim Sarran", p_mobile, "High", -1, est=5)       # OVERDUE

    # WEBSITE REDESIGN
    t_wires  = task("Design wireframes", "Liam O'Brien", p_redesign, "High", -3, done=True, desc="Low-fi wireframes", est=10)  # DONE
    t_visual = task("Visual design mockups", "Mei Tanaka", p_redesign, "High", 6, est=16)
    t_build  = task("Build redesigned pages", "Diego Morales", p_redesign, "Medium", 20, est=20)
    db.add(TaskDependency(task_id=t_visual.id, depends_on_id=t_wires.id))
    db.add(TaskDependency(task_id=t_build.id,  depends_on_id=t_visual.id))

    # Q3 LAUNCH (critical, tight deadline)
    task("Write launch announcement", "Noah Bennett", p_launch, "High", 4, est=6)       # APPROACHING
    task("Plan social campaign", "Sofia Rossi", p_launch, "Critical", 2, est=8)         # APPROACHING (critical)
    task("Design launch graphics", "Mei Tanaka", p_launch, "High", 5, est=10)
    task("Set up analytics tracking", "Sofia Rossi", p_launch, "Medium", -2, est=4)     # OVERDUE
    task("Press outreach list", "Noah Bennett", p_launch, "Low", 9, est=3)

    # A couple unassigned / loose tasks
    task("Investigate crash reports", None, p_mobile, "High", 2, desc="Unassigned — needs an owner")
    task("Update brand guidelines", "Liam O'Brien", p_redesign, "Low", 15, est=4)

    # Aisha gets a second (light) so not everyone is equal
    task("Optimize DB queries", "Aisha Khan", p_mobile, "Medium", 11, est=6)

    db.flush()

    # ── Subtasks on a few tasks ──────────────────────────────────
    for st in ["Auth endpoints", "Product endpoints", "Order endpoints", "Webhook handlers"]:
        db.add(Subtask(task_id=t_api.id, title=st, is_completed=(st == "Auth endpoints")))
    for st in ["Home screen", "Product list", "Cart", "Checkout", "Profile"]:
        db.add(Subtask(task_id=t_ui.id, title=st, is_completed=False))

    # ── A comment or two ─────────────────────────────────────────
    db.add(TaskComment(task_id=t_api.id, author_id=manager.id,
                       content="This is blocking the whole mobile project — let's prioritize.",
                       is_ai_generated=False))

    # ── Meetings ─────────────────────────────────────────────────
    m1 = Meeting(company_id=COMPANY_ID, topic="Mobile App standup",
                 scheduled_time="09:30", scheduled_date=_d(1), duration_minutes=30,
                 location="Zoom", created_by=manager.id)
    m2 = Meeting(company_id=COMPANY_ID, topic="Launch planning",
                 scheduled_time="14:00", scheduled_date=_d(2), duration_minutes=60,
                 location="Conf Room A", created_by=manager.id)
    db.add_all([m1, m2]); db.flush()
    db.execute(meeting_attendees.insert(), [
        {"meeting_id": m1.id, "employee_id": emps["Sulaim Sarran"].id},
        {"meeting_id": m1.id, "employee_id": emps["Aisha Khan"].id},
        {"meeting_id": m1.id, "employee_id": emps["Diego Morales"].id},
        {"meeting_id": m2.id, "employee_id": emps["Sofia Rossi"].id},
        {"meeting_id": m2.id, "employee_id": emps["Noah Bennett"].id},
    ])

    # ── Goals (OKR-style) ────────────────────────────────────────
    g = Goal(company_id=COMPANY_ID, employee_id=manager.id,
             title="Ship Mobile App v1.0", description="Launch to first 100 retail clients",
             target_date=_d(21)) if _goal_has_fields() else None
    if g:
        db.add(g)

    db.commit()

    # ── Summary ──────────────────────────────────────────────────
    total_tasks = db.query(Task).filter(Task.company_id == COMPANY_ID).count()
    overdue = db.query(Task).filter(Task.company_id == COMPANY_ID,
                                    Task.is_completed == False,
                                    Task.due_date < TODAY).count()
    sulaim_load = db.query(Task).filter(Task.owner_id == emps["Sulaim Sarran"].id,
                                        Task.is_completed == False).count()
    print("\n✅ SEED COMPLETE")
    print(f"   Teams: 3 | Employees: {len(people)} + 1 manager")
    print(f"   Projects: 3 | Tasks: {total_tasks}")
    print(f"   Overdue tasks: {overdue}  (proactive + briefings will catch these)")
    print(f"   Sulaim's open tasks: {sulaim_load}  (overload → triggers rebalance/negotiation)")
    print(f"   Task dependencies wired: Mobile (API→UI→Integrate→QA), Redesign (Wires→Visual→Build)")
    print(f"\n   Login as anyone with password: {DEMO_PASSWORD}")
    print(f"   Manager: 'Mr Kurbi'  |  e.g. employee: 'Sulaim Sarran'")


def _goal_has_fields():
    """Goal model fields vary; guard so seeding never crashes on Goal."""
    try:
        from database.models import Goal as _G
        cols = {c.name for c in _G.__table__.columns}
        return {"employee_id", "title", "target_date"}.issubset(cols)
    except Exception:
        return False


if __name__ == "__main__":
    db = SessionLocal()
    try:
        print("=" * 55)
        print("NEXUS DEMO SEEDER — wipe + reseed company 1")
        print("=" * 55)
        wipe_company(db)
        seed(db)
    except Exception as e:
        db.rollback()
        print(f"\n❌ Seed failed: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()
        