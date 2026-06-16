"""
analytics.py — Enterprise Analytics Router
===========================================
GET /api/v1/analytics/summary?period=week|month|quarter
GET /api/v1/analytics/team/{team_name}?period=week|month|quarter
GET /api/v1/analytics/employee/{employee_id}

All data from existing tables — zero schema changes.
Single-pass DB fetch — no N+1 query loops.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone, date as date_type
from collections import defaultdict
from typing import Optional

from database.core import get_db
from database.models import (
    Task, Employee, PeerRequest, Escalation,
    AgentMemory, Meeting, Goal, TimeEntry,
)
from api.security import get_current_user

router = APIRouter()


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_period_bounds(period: str):
    now = datetime.now(timezone.utc)
    if period == "week":
        return now - timedelta(days=7),  now - timedelta(days=14),  "Last 7 Days",  7
    elif period == "quarter":
        return now - timedelta(days=90), now - timedelta(days=180), "Last 90 Days", 90
    else:
        return now - timedelta(days=30), now - timedelta(days=60),  "Last 30 Days", 30


def _parse_date(raw) -> date_type:
    try:
        return datetime.strptime(str(raw).split("T")[0], "%Y-%m-%d").date()
    except Exception:
        return date_type(9999, 12, 31)


def _safe_dt(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if hasattr(dt, "tzinfo") and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _rate(done: int, total: int) -> float:
    return round((done / total * 100), 1) if total > 0 else 0.0


def _build_trend(all_tasks, period_days: int):
    today = datetime.now(timezone.utc).date()
    points = []
    if period_days <= 30:
        for i in range(period_days - 1, -1, -1):
            day = today - timedelta(days=i)
            created   = sum(1 for t in all_tasks if _safe_dt(t.created_at) and _safe_dt(t.created_at).date() == day)
            completed = sum(1 for t in all_tasks if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.completed_at).date() == day)
            points.append({"date": day.strftime("%b %d"), "created": created, "completed": completed})
    else:
        weeks = period_days // 7
        for i in range(weeks - 1, -1, -1):
            week_end   = today - timedelta(weeks=i)
            week_start = week_end - timedelta(days=6)
            created   = sum(1 for t in all_tasks if _safe_dt(t.created_at) and week_start <= _safe_dt(t.created_at).date() <= week_end)
            completed = sum(1 for t in all_tasks if t.is_completed and _safe_dt(t.completed_at) and week_start <= _safe_dt(t.completed_at).date() <= week_end)
            points.append({"date": week_start.strftime("%b %d"), "created": created, "completed": completed})
    return points


def _is_this_week(scheduled_time, week_start, week_end) -> bool:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(scheduled_time)[:19], fmt)
            return week_start <= dt.date() < week_end
        except ValueError:
            continue
    return False


# ─────────────────────────────────────────────────────────────
# GET /summary
# ─────────────────────────────────────────────────────────────

@router.get("/summary")
def get_analytics_summary(
    period: str = Query(default="month"),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    period_start, prev_start, period_label, period_days = _get_period_bounds(period)
    now   = datetime.now(timezone.utc)
    today = now.date()

    # Single-pass fetch
    all_tasks     = db.query(Task).all()
    all_employees = db.query(Employee).filter(Employee.system_role == "employee").all()
    all_requests  = db.query(PeerRequest).all()
    all_escs      = db.query(Escalation).all()
    all_memories  = db.query(AgentMemory).filter(AgentMemory.agent_id.like("Employee_%")).all()
    all_meetings  = db.query(Meeting).all()
    all_goals     = db.query(Goal).all()

    # AI usage map: employee_id → message_count
    ai_usage = {}
    for mem in all_memories:
        try:
            ai_usage[int(mem.agent_id.split("_")[1])] = mem.message_count or 0
        except Exception:
            pass

    # Task aggregates
    total_tasks  = len(all_tasks)
    done_tasks   = sum(1 for t in all_tasks if t.is_completed)
    active_tasks = total_tasks - done_tasks
    overdue_list = [t for t in all_tasks if not t.is_completed and t.due_date and _parse_date(t.due_date) < today]
    overdue_count = len(overdue_list)
    critical_overdue = [t for t in overdue_list if (today - _parse_date(t.due_date)).days > 7]
    completion_rate = _rate(done_tasks, total_tasks)
    overdue_rate    = _rate(overdue_count, active_tasks)

    # Period velocity
    this_period = sum(1 for t in all_tasks if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.completed_at) >= period_start)
    prev_period = sum(1 for t in all_tasks if t.is_completed and _safe_dt(t.completed_at) and prev_start <= _safe_dt(t.completed_at) < period_start)
    velocity_pct = 0
    if prev_period > 0:
        velocity_pct = round(((this_period - prev_period) / prev_period) * 100)
    elif this_period > 0:
        velocity_pct = 100

    # Avg completion time
    times = []
    for t in all_tasks:
        if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.created_at):
            h = (_safe_dt(t.completed_at) - _safe_dt(t.created_at)).total_seconds() / 3600
            if 0 < h < 8760:
                times.append(h)
    avg_completion_hours = round(sum(times) / len(times), 1) if times else 0

    # AI adoption
    threshold = 3 if period == "week" else 5
    active_ai  = sum(1 for v in ai_usage.values() if v >= threshold)
    ai_adoption = round((active_ai / len(all_employees) * 100)) if all_employees else 0
    no_ai_employees = [e for e in all_employees if ai_usage.get(e.id, 0) < threshold]

    # Org health score (completion 35% + overdue health 35% + AI adoption 30%)
    overdue_health = max(0, 100 - overdue_rate)
    health_score   = round(completion_rate * 0.35 + overdue_health * 0.35 + ai_adoption * 0.30)
    health_label   = "Excellent" if health_score >= 85 else "Good" if health_score >= 70 else "Fair" if health_score >= 55 else "At Risk"
    health_color   = "#10b981"   if health_score >= 85 else "#6366f1" if health_score >= 70 else "#f59e0b" if health_score >= 55 else "#ef4444"

    # Risk alerts
    emp_name_map = {e.id: e.name for e in all_employees}
    risk_alerts = []
    if critical_overdue:
        names = list({emp_name_map.get(t.owner_id, "?") for t in critical_overdue[:3]})
        risk_alerts.append({"type": "critical_overdue", "severity": "high", "count": len(critical_overdue),
                             "message": f"{len(critical_overdue)} task{'s' if len(critical_overdue) > 1 else ''} overdue >7 days — {', '.join(names)}"})
    pending_escs = [e for e in all_escs if e.status == "pending"]
    if pending_escs:
        risk_alerts.append({"type": "pending_escalations", "severity": "high", "count": len(pending_escs),
                             "message": f"{len(pending_escs)} escalation{'s' if len(pending_escs) > 1 else ''} awaiting manager action"})
    if no_ai_employees:
        names = [e.name.split()[0] for e in no_ai_employees[:3]]
        risk_alerts.append({"type": "low_ai_adoption", "severity": "medium", "count": len(no_ai_employees),
                             "message": f"{len(no_ai_employees)} employee{'s' if len(no_ai_employees) > 1 else ''} not using AI: {', '.join(names)}"})
    no_tasks_emps = [e for e in all_employees if sum(1 for t in all_tasks if t.owner_id == e.id and not t.is_completed) == 0]
    if no_tasks_emps:
        names = [e.name.split()[0] for e in no_tasks_emps[:3]]
        risk_alerts.append({"type": "underutilized", "severity": "low", "count": len(no_tasks_emps),
                             "message": f"{len(no_tasks_emps)} employee{'s' if len(no_tasks_emps) > 1 else ''} with no active tasks: {', '.join(names)}"})

    # Employee scatter (workload vs performance)
    employee_scatter = []
    for e in all_employees:
        emp_tasks  = [t for t in all_tasks if t.owner_id == e.id]
        emp_active = sum(1 for t in emp_tasks if not t.is_completed)
        emp_done   = sum(1 for t in emp_tasks if t.is_completed)
        emp_over   = sum(1 for t in emp_tasks if not t.is_completed and t.due_date and _parse_date(t.due_date) < today)
        employee_scatter.append({
            "id": e.id, "name": e.name.split()[0], "full_name": e.name,
            "role": e.role, "team": e.team or "Unassigned",
            "active": emp_active, "completed": emp_done, "overdue": emp_over,
            "total": len(emp_tasks),
            "completion_rate": _rate(emp_done, len(emp_tasks)),
            "ai_messages": ai_usage.get(e.id, 0),
        })

    # Team breakdown
    teams_data = defaultdict(lambda: {"members": [], "tasks": []})
    for e in all_employees:
        teams_data[e.team or "Unassigned"]["members"].append(e)
    for t in all_tasks:
        owner = next((e for e in all_employees if e.id == t.owner_id), None)
        if owner:
            teams_data[owner.team or "Unassigned"]["tasks"].append(t)

    team_breakdown = []
    for tname, td in teams_data.items():
        tt = td["tasks"]
        tdone  = sum(1 for t in tt if t.is_completed)
        tact   = sum(1 for t in tt if not t.is_completed)
        tover  = sum(1 for t in tt if not t.is_completed and t.due_date and _parse_date(t.due_date) < today)
        tperiod = sum(1 for t in tt if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.completed_at) >= period_start)
        team_breakdown.append({
            "name": tname, "member_count": len(td["members"]),
            "total_tasks": len(tt), "completed": tdone, "active": tact, "overdue": tover,
            "completion_rate": _rate(tdone, len(tt)),
            "avg_tasks_per_person": round(tact / len(td["members"]), 1) if td["members"] else 0,
            "period_completions": tperiod,
        })
    team_breakdown.sort(key=lambda x: x["total_tasks"], reverse=True)

    # Priority breakdown (active only)
    pmap = defaultdict(int)
    for t in all_tasks:
        if not t.is_completed:
            pmap[t.priority or "Medium"] += 1
    priority_breakdown = [{"name": k, "value": pmap[k]} for k in ["Critical", "High", "Medium", "Low"] if pmap[k] > 0]

    # Peer requests
    pr_t = len(all_requests)
    pr_a = sum(1 for r in all_requests if r.status == "Accepted")
    pr_d = sum(1 for r in all_requests if r.status == "Declined")
    pr_p = sum(1 for r in all_requests if r.status == "Pending")
    pr_c = sum(1 for r in all_requests if r.status == "Completed")

    # Escalations
    esc_t = len(all_escs)
    esc_p = sum(1 for e in all_escs if e.status == "pending")
    esc_r = sum(1 for e in all_escs if e.status == "resolved")

    # Agent activity
    agent_activity = sorted(
        [{"name": e.name.split()[0], "full_name": e.name, "messages": ai_usage.get(e.id, 0)}
         for e in all_employees if ai_usage.get(e.id, 0) > 0],
        key=lambda x: x["messages"], reverse=True,
    )

    # Meetings this week
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=7)
    meetings_week = sum(1 for m in all_meetings if m.scheduled_time and _is_this_week(m.scheduled_time, week_start, week_end))

    # Goals
    goals_active   = sum(1 for g in all_goals if g.status == "active")
    goals_achieved = sum(1 for g in all_goals if g.status == "achieved")
    avg_goal_pct   = round(sum(g.progress_pct or 0 for g in all_goals) / len(all_goals), 1) if all_goals else 0

    return {
        "period": period, "period_label": period_label,
        "org_health": {"score": health_score, "label": health_label, "color": health_color,
                       "completion_rate": completion_rate, "overdue_rate": overdue_rate, "ai_adoption": ai_adoption},
        "stats": {
            "total_tasks": total_tasks, "completed_tasks": done_tasks, "active_tasks": active_tasks,
            "overdue_count": overdue_count, "completion_rate": completion_rate, "overdue_rate": overdue_rate,
            "this_period_completed": this_period, "last_period_completed": prev_period, "velocity_pct": velocity_pct,
            "avg_completion_hours": avg_completion_hours, "total_employees": len(all_employees),
            "meetings_this_week": meetings_week, "goals_active": goals_active, "goals_achieved": goals_achieved,
            "avg_goal_progress": avg_goal_pct, "ai_adoption_rate": ai_adoption,
        },
        "risk_alerts":       risk_alerts,
        "team_breakdown":    team_breakdown,
        "employee_scatter":  employee_scatter,
        "completion_trend":  _build_trend(all_tasks, period_days),
        "priority_breakdown": priority_breakdown,
        "peer_requests": {"total": pr_t, "accepted": pr_a, "declined": pr_d, "pending": pr_p,
                          "completed": pr_c, "acceptance_rate": _rate(pr_a, pr_t)},
        "escalations":   {"total": esc_t, "pending": esc_p, "resolved": esc_r, "resolution_rate": _rate(esc_r, esc_t)},
        "agent_activity": agent_activity,
    }


# ─────────────────────────────────────────────────────────────
# GET /team/{team_name}  — drill-down
# ─────────────────────────────────────────────────────────────

@router.get("/team/{team_name}")
def get_team_analytics(
    team_name: str,
    period: str = Query(default="month"),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    period_start, _, period_label, period_days = _get_period_bounds(period)
    today = datetime.now(timezone.utc).date()

    members = db.query(Employee).filter(Employee.team == team_name, Employee.system_role == "employee").all()
    if not members:
        return {"error": f"No employees found in team '{team_name}'"}

    member_ids = [e.id for e in members]
    tasks = db.query(Task).filter(Task.owner_id.in_(member_ids)).all()
    emp_name_map = {e.id: e.name for e in members}

    member_stats = []
    for e in members:
        et = [t for t in tasks if t.owner_id == e.id]
        ea = sum(1 for t in et if not t.is_completed)
        ed = sum(1 for t in et if t.is_completed)
        eo = sum(1 for t in et if not t.is_completed and t.due_date and _parse_date(t.due_date) < today)
        ep = sum(1 for t in et if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.completed_at) >= period_start)
        member_stats.append({
            "id": e.id, "name": e.name, "role": e.role,
            "active": ea, "completed": ed, "overdue": eo, "period_done": ep,
            "total": len(et), "completion_rate": _rate(ed, len(et)),
        })

    total = len(tasks)
    done  = sum(1 for t in tasks if t.is_completed)
    active = total - done
    overdue = sum(1 for t in tasks if not t.is_completed and t.due_date and _parse_date(t.due_date) < today)
    period_done = sum(1 for t in tasks if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.completed_at) >= period_start)

    task_list = []
    for t in sorted(tasks, key=lambda x: (x.is_completed, str(x.due_date or "9999"))):
        is_over = not t.is_completed and t.due_date and _parse_date(t.due_date) < today
        subs = t.subtasks or []
        task_list.append({
            "id": t.id, "title": t.title,
            "owner_name": emp_name_map.get(t.owner_id, "Unknown"),
            "priority": t.priority or "Medium",
            "is_completed": t.is_completed,
            "due_date": str(t.due_date) if t.due_date else None,
            "overdue": bool(is_over),
            "days_overdue": (today - _parse_date(t.due_date)).days if is_over else 0,
            "subtasks_done": sum(1 for s in subs if s.is_completed),
            "subtasks_total": len(subs),
        })

    # Daily trend
    trend = []
    days = min(period_days, 30)
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        c = sum(1 for t in tasks if t.is_completed and _safe_dt(t.completed_at) and _safe_dt(t.completed_at).date() == day)
        trend.append({"date": day.strftime("%b %d"), "completed": c})

    return {
        "team": team_name, "period": period, "period_label": period_label,
        "stats": {"member_count": len(members), "total_tasks": total, "completed": done,
                  "active": active, "overdue": overdue, "period_done": period_done,
                  "completion_rate": _rate(done, total),
                  "avg_per_person": round(active / len(members), 1) if members else 0},
        "members": member_stats, "tasks": task_list, "trend": trend,
    }


# ─────────────────────────────────────────────────────────────
# GET /employee/{employee_id}  — drill-down
# ─────────────────────────────────────────────────────────────

@router.get("/employee/{employee_id}")
def get_employee_analytics(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    today = datetime.now(timezone.utc).date()

    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        return {"error": "Employee not found"}

    tasks        = db.query(Task).filter(Task.owner_id == employee_id).all()
    goals        = db.query(Goal).filter(Goal.employee_id == employee_id).all()
    peer_sent    = db.query(PeerRequest).filter(PeerRequest.sender_id    == employee_id).all()
    peer_recv    = db.query(PeerRequest).filter(PeerRequest.recipient_id == employee_id).all()
    memory       = db.query(AgentMemory).filter(AgentMemory.agent_id == f"Employee_{employee_id}").first()
    time_entries = db.query(TimeEntry).filter(TimeEntry.employee_id == employee_id).all()

    active  = sum(1 for t in tasks if not t.is_completed)
    done    = sum(1 for t in tasks if t.is_completed)
    overdue = sum(1 for t in tasks if not t.is_completed and t.due_date and _parse_date(t.due_date) < today)
    hours   = round(sum((e.duration_minutes or 0) for e in time_entries) / 60, 1)

    task_list = []
    for t in sorted(tasks, key=lambda x: (x.is_completed, str(x.due_date or "9999"))):
        is_over = not t.is_completed and t.due_date and _parse_date(t.due_date) < today
        subs = t.subtasks or []
        task_list.append({
            "id": t.id, "title": t.title, "priority": t.priority or "Medium",
            "is_completed": t.is_completed, "due_date": str(t.due_date) if t.due_date else None,
            "overdue": bool(is_over), "days_overdue": (today - _parse_date(t.due_date)).days if is_over else 0,
            "subtasks_total": len(subs), "subtasks_done": sum(1 for s in subs if s.is_completed),
        })

    return {
        "employee": {"id": emp.id, "name": emp.name, "role": emp.role,
                     "team": emp.team or "Unassigned", "experience": emp.experience or 0,
                     "skills": emp.skills or "", "age": emp.age},
        "stats": {"total_tasks": len(tasks), "active": active, "completed": done, "overdue": overdue,
                  "completion_rate": _rate(done, len(tasks)), "ai_messages": memory.message_count if memory else 0,
                  "peer_sent": len(peer_sent), "peer_received": len(peer_recv),
                  "peer_accepted": sum(1 for r in peer_recv if r.status == "Accepted"),
                  "hours_logged": hours, "goals_active": sum(1 for g in goals if g.status == "active"),
                  "goals_achieved": sum(1 for g in goals if g.status == "achieved")},
        "tasks": task_list,
        "goals": [{"id": g.id, "title": g.title, "progress_pct": g.progress_pct or 0,
                   "status": g.status, "target_date": str(g.target_date) if g.target_date else None}
                  for g in goals],
        "peer_history": sorted(
            [{"id": r.id, "topic": r.topic, "status": r.status, "direction": "sent"} for r in peer_sent] +
            [{"id": r.id, "topic": r.topic, "status": r.status, "direction": "received"} for r in peer_recv],
            key=lambda x: x["id"], reverse=True
        )[:20],
    }
