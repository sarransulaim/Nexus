"""
cleanup_stress.py — remove everything seed_stress.py created, and nothing else.
==============================================================================
Identifies stress data by the EXACT names seed_stress used (single source of
truth — imported from that module), never by id ranges, so real users and their
work can't be caught in the blast radius.

Deletion is explicit rather than FK-cascade-only, because several FKs are
ondelete=SET NULL (Task.owner_id, Meeting.created_by, Message.sender_id):
a plain employee delete would leave orphaned ownerless tasks behind.

    python cleanup_stress.py --dry-run     # show what would go
    python cleanup_stress.py               # do it (single transaction)
    DATABASE_URL=... python cleanup_stress.py    # target the cloud
"""
import sys

from sqlalchemy import text

from database.core import SessionLocal
from seed_stress import NAMES, PROJECTS, TEAMS

DRY = "--dry-run" in sys.argv


def main():
    db = SessionLocal()
    try:
        stress_names = list(NAMES)
        proj_names = [p[0] for p in PROJECTS]
        team_names = list(TEAMS.keys())

        # ── identify (by name, never by id range) ─────────────────
        emp_ids = [r[0] for r in db.execute(
            text("SELECT id FROM employees WHERE name = ANY(:n)"), {"n": stress_names})]
        proj_ids = [r[0] for r in db.execute(
            text("SELECT id FROM projects WHERE name = ANY(:n)"), {"n": proj_names})]
        # tasks owned by stress people OR belonging to stress projects
        task_ids = [r[0] for r in db.execute(
            text("SELECT id FROM tasks WHERE owner_id = ANY(:e) OR project_id = ANY(:p)"),
            {"e": emp_ids or [0], "p": proj_ids or [0]})]
        meeting_ids = [r[0] for r in db.execute(
            text("SELECT id FROM meetings WHERE created_by = ANY(:e)"), {"e": emp_ids or [0]})]
        agent_ids = [f"Employee_{i}" for i in emp_ids]

        print(f"Stress data found: {len(emp_ids)} employees, {len(proj_ids)} projects, "
              f"{len(task_ids)} tasks, {len(meeting_ids)} meetings")
        keep = db.execute(text(
            "SELECT id, name, system_role FROM employees WHERE NOT (name = ANY(:n)) ORDER BY id"),
            {"n": stress_names}).fetchall()
        print("Will KEEP these people:")
        for r in keep:
            print(f"   id={r[0]:<4} {r[1]} ({r[2]})")

        if DRY:
            print("\n(dry run — nothing deleted)")
            return

        # ── delete, dependents first ──────────────────────────────
        # tasks: CASCADE clears subtasks, comments, dependencies, goal_tasks,
        # contracts and peer_requests that hang off them.
        if task_ids:
            db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
        if meeting_ids:
            db.execute(text("DELETE FROM meetings WHERE id = ANY(:m)"), {"m": meeting_ids})
        if agent_ids:
            # from_agent_id is a plain STRING (no FK) — orphans unless removed here
            db.execute(text("DELETE FROM escalations WHERE from_agent_id = ANY(:a)"),
                       {"a": agent_ids})
            db.execute(text("DELETE FROM agent_memory WHERE agent_id = ANY(:a)"), {"a": agent_ids})
            db.execute(text("DELETE FROM audit_logs WHERE actor_agent_id = ANY(:a)"), {"a": agent_ids})
        if proj_ids:
            # channels (and their messages) cascade off the project
            db.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": proj_ids})
        if emp_ids:
            # notifications, preferences, goals, time entries, channel/meeting
            # memberships and peer requests all cascade off the employee
            db.execute(text("DELETE FROM employees WHERE id = ANY(:e)"), {"e": emp_ids})
        # teams the stress run created that now have nobody left in them
        db.execute(text("""
            DELETE FROM teams t
             WHERE t.name = ANY(:tn)
               AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.team_id = t.id)
        """), {"tn": team_names})
        db.commit()

        print("\nRemaining:")
        for tbl in ("employees", "teams", "projects", "tasks", "meetings",
                    "channels", "messages", "goals", "contracts", "escalations"):
            n = db.execute(text(f"SELECT count(*) FROM {tbl}")).scalar()
            print(f"   {tbl:12} {n}")
        print("\n✅ Stress data removed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
