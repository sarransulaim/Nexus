"""
negotiation_engine.py — Multi-Agent Negotiation System
========================================================
Agents proactively detect problems and negotiate solutions
with each other — without the manager having to ask.

Three negotiation types:
1. Workload Rebalancing  — overloaded agent finds help
2. Deadline Conflict     — agent detects and resolves conflicts  
3. Skill-based Routing   — new tasks auto-routed to best agent

Architecture:
  ProblemDetector   → scans for issues every 30 mins
  AgentNegotiator   → agents send proposals to each other
  ConsensusBuilder  → reaches agreement between agents
  ManagerReporter   → keeps manager informed of resolutions
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from database.core import SessionLocal
from database.models import (
    Employee, Task, Notification, AgentMemory,
    Escalation, Subtask
)
from api.ws_manager import notifier

log = logging.getLogger("nexus.negotiation")

# ── Thresholds ─────────────────────────────────────────────────
OVERLOAD_THRESHOLD   = 5   # tasks before agent is considered overloaded
DEADLINE_WARNING_HRS = 24  # hours before deadline to trigger negotiation
CHECK_INTERVAL_SECS  = 1800  # 30 minutes between scans


# ═══════════════════════════════════════════════════════════════
# PROBLEM DETECTOR
# Scans the org for issues that need agent negotiation
# ═══════════════════════════════════════════════════════════════

class ProblemDetector:

    def detect_overloaded_agents(self, db) -> list[dict]:
        """
        Finds employees with too many active tasks.
        Returns list of {employee, tasks, overload_count}
        """
        overloaded = []
        employees = db.query(Employee).filter(
            Employee.system_role == "employee"
        ).all()

        for emp in employees:
            active_tasks = db.query(Task).filter(
                Task.owner_id == emp.id,
                Task.is_completed == False
            ).all()

            if len(active_tasks) >= OVERLOAD_THRESHOLD:
                overloaded.append({
                    "employee":     emp,
                    "tasks":        active_tasks,
                    "task_count":   len(active_tasks),
                    "agent_id":     f"Employee_{emp.id}",
                })
                log.info(f"⚠️  Overload detected: {emp.name} has {len(active_tasks)} tasks")

        return overloaded

    def detect_deadline_conflicts(self, db) -> list[dict]:
        """
        Finds employees with multiple tasks due within 24 hours.
        """
        conflicts = []
        now = datetime.now(timezone.utc)
        cutoff = (now + timedelta(hours=DEADLINE_WARNING_HRS)).date()

        employees = db.query(Employee).filter(
            Employee.system_role == "employee"
        ).all()

        for emp in employees:
            urgent_tasks = db.query(Task).filter(
                Task.owner_id == emp.id,
                Task.is_completed == False,
                Task.due_date <= str(cutoff),
                Task.due_date != None,
            ).all()

            if len(urgent_tasks) > 1:
                conflicts.append({
                    "employee":     emp,
                    "urgent_tasks": urgent_tasks,
                    "agent_id":     f"Employee_{emp.id}",
                })
                log.info(f"⏰ Deadline conflict: {emp.name} has {len(urgent_tasks)} urgent tasks")

        return conflicts

    def find_available_agents(self, db, exclude_id: int, skill_hint: str = None) -> list[dict]:
        """
        Finds employees with capacity to take on more work.
        Optionally filters by skill keyword.
        """
        available = []
        employees = db.query(Employee).filter(
            Employee.system_role == "employee",
            Employee.id != exclude_id,
        ).all()

        for emp in employees:
            active_count = db.query(Task).filter(
                Task.owner_id == emp.id,
                Task.is_completed == False
            ).count()

            # Consider available if below threshold
            if active_count < OVERLOAD_THRESHOLD - 1:
                score = OVERLOAD_THRESHOLD - active_count  # higher = more available

                # Boost score if skill matches
                if skill_hint and emp.skills:
                    if skill_hint.lower() in emp.skills.lower():
                        score += 3

                available.append({
                    "employee":     emp,
                    "active_count": active_count,
                    "agent_id":     f"Employee_{emp.id}",
                    "score":        score,
                })

        # Sort by availability score (most available first)
        available.sort(key=lambda x: x["score"], reverse=True)
        return available


# ═══════════════════════════════════════════════════════════════
# AGENT NEGOTIATOR
# Agents send proposals to each other and reach agreements
# ═══════════════════════════════════════════════════════════════

class AgentNegotiator:

    def propose_task_transfer(
        self,
        task: Task,
        from_employee: Employee,
        to_employee: Employee,
        db,
    ) -> dict:
        """
        Agent A proposes transferring a task to Agent B.
        Uses Claude to reason about the proposal.
        """
        try:
            from api.claude_orchestrator import run_orchestrator, _negotiation_local

            # Ask the receiving agent if they can take the task
            proposal_prompt = (
                f"Your colleague {from_employee.name} is overloaded and needs help. "
                f"They want to transfer this task to you: '{task.title}' "
                f"(Priority: {task.priority}, Due: {task.due_date}). "
                f"Check your current workload using get_my_tasks with employee_id={to_employee.id}. "
                f"Then answer in plain language: agree to take it if you clearly have capacity; "
                f"if you could take it only under a condition (after a date, after finishing a "
                f"specific task), say exactly what condition; if you genuinely can't, say no with "
                f"the concrete reason."
            )

            # Flag this thread as mid-negotiation so the candidate's orchestrator
            # can't launch its OWN nested negotiation (negotiate_peer_help), which
            # would re-enter the negotiation machinery and fan out unbounded
            # AI-to-AI calls. The tool path sets this same guard — the background
            # engine bypassed it, so nesting was possible from here. (#15)
            _prev_neg = getattr(_negotiation_local, "active", False)
            _negotiation_local.active = True
            try:
                response = run_orchestrator(
                    agent_id=f"Employee_{to_employee.id}",
                    command=proposal_prompt,
                )
            finally:
                _negotiation_local.active = _prev_neg

            # STRUCTURED VERDICT — the old `"ACCEPT" in response.upper()` check
            # treated "I can't ACCEPT more work" as a yes and transferred the task.
            # Only a clear unconditional accept executes an autonomous transfer;
            # a conditional yes (counter) is surfaced to the manager, not executed.
            from api.claude_orchestrator import extract_negotiation_decision
            verdict  = extract_negotiation_decision(proposal_prompt, response)
            accepted = verdict["decision"] == "accept"

            return {
                "accepted":    accepted,
                "decision":    verdict["decision"],
                "reason":      verdict.get("reason", ""),
                "counter_proposal": verdict.get("counter_proposal", ""),
                "response":    response,
                "from":        from_employee.name,
                "to":          to_employee.name,
                "task":        task.title,
                "task_id":     task.id,
            }

        except Exception as e:
            log.error(f"Negotiation error: {e}")
            return {"accepted": False, "response": str(e)}

    def execute_task_transfer(
        self,
        task_id: int,
        new_owner_id: int,
        old_owner: Employee,
        new_owner: Employee,
        db,
    ):
        """
        Executes the agreed task transfer in the database.
        Notifies both employees and the manager.
        """
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return

        old_owner_id = task.owner_id
        task.owner_id = new_owner_id
        db.commit()

        # Notify the new owner
        db.add(Notification(
            company_id=task.company_id,
            recipient_id=new_owner_id,
            type="task_assigned",
            title="Task Transferred to You",
            message=f"Agent negotiation: '{task.title}' transferred from {old_owner.name}. Auto-balanced by Nexus.",
        ))

        # Notify the old owner
        db.add(Notification(
            company_id=task.company_id,
            recipient_id=old_owner_id,
            type="task_assigned",
            title="Task Transferred Away",
            message=f"'{task.title}' was transferred to {new_owner.name} to balance your workload.",
        ))

        # Notify manager via escalation log
        db.add(Escalation(
            company_id=task.company_id,
            from_agent_id=f"Employee_{old_owner_id}",
            to_agent_id=f"Employee_{new_owner_id}",
            reason=f"Auto-rebalance: '{task.title}' transferred from {old_owner.name} → {new_owner.name}. Multi-agent negotiation completed.",
            status="resolved",
        ))

        db.commit()
        log.info(f"✅ Transfer complete: '{task.title}' → {new_owner.name}")


# ═══════════════════════════════════════════════════════════════
# MANAGER REPORTER
# Keeps the manager informed of all agent negotiations
# ═══════════════════════════════════════════════════════════════

class ManagerReporter:

    async def broadcast_negotiation_result(self, result: dict):
        """Broadcasts negotiation outcome to the Glass Brain."""
        decision = result.get("decision") or ("accept" if result.get("accepted") else "decline")
        if result.get("accepted"):
            outcome = "✅ Transferred"
        elif decision == "counter":
            cond = (result.get("counter_proposal") or "").strip()
            outcome = f"🤝 Conditional offer{': ' + cond[:100] if cond else ''} (not executed — your call)"
        else:
            why = (result.get("reason") or "").strip()
            outcome = f"❌ Declined{' — ' + why[:100] if why else ''}"
        msg = (
            f"Manager_1|[NEXUS NEGOTIATION] "
            f"Agent {result.get('from')} ↔ Agent {result.get('to')}: "
            f"'{result.get('task')}' — {outcome}"
        )
        await notifier.broadcast_to_managers(f"THOUGHT:{msg}")
        await notifier.broadcast("SYNC_REQUIRED")

    async def report_overload_resolution(self, employee_name: str, task_title: str, new_owner: str):
        """Reports a successful workload rebalancing to the manager."""
        msg = (
            f"Manager_1|[AUTO-REBALANCE] Workload optimized: "
            f"'{task_title}' moved from {employee_name} → {new_owner}. "
            f"Team balance restored."
        )
        await notifier.broadcast_to_managers(f"THOUGHT:{msg}")

    async def report_deadline_alert(self, employee_name: str, task_count: int):
        """Alerts manager to unresolvable deadline conflicts."""
        msg = (
            f"Manager_1|[DEADLINE ALERT] {employee_name} has {task_count} tasks "
            f"due within 24 hours. Manual intervention may be needed."
        )
        await notifier.broadcast_to_managers(f"THOUGHT:{msg}")


# ═══════════════════════════════════════════════════════════════
# NEGOTIATION ORCHESTRATOR
# Main coordinator — runs the full negotiation loop
# ═══════════════════════════════════════════════════════════════

class NegotiationOrchestrator:

    def __init__(self):
        self.detector   = ProblemDetector()
        self.negotiator = AgentNegotiator()
        self.reporter   = ManagerReporter()

    async def run_negotiation_cycle(self):
        """
        Full negotiation cycle:
        1. Detect problems
        2. Find solutions via agent negotiation
        3. Execute agreements
        4. Report to manager
        """
        db = SessionLocal()
        try:
            log.info("🔄 Starting negotiation cycle...")

            # PHASE 3: emit negotiation start event
            try:
                from event_bus import emit_negotiation_start
                emit_negotiation_start()
            except Exception:
                pass

            resolved = 0

            # ── Workload Rebalancing ──────────────────────────
            overloaded = self.detector.detect_overloaded_agents(db)

            for case in overloaded:
                emp      = case["employee"]
                tasks    = case["tasks"]

                # Find the least critical task to transfer
                # (lowest priority, furthest due date)
                from datetime import date as _date
                _FAR_FUTURE = _date(9999, 12, 31)
                transferable = sorted(
                    tasks,
                    key=lambda t: (
                        {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}.get(t.priority, 1),
                        t.due_date if t.due_date else _FAR_FUTURE,
                    )
                )

                if not transferable:
                    continue

                task_to_transfer = transferable[0]

                # Find available agents
                available = self.detector.find_available_agents(
                    db,
                    exclude_id=emp.id,
                    skill_hint=emp.skills,
                )

                if not available:
                    await self.reporter.report_deadline_alert(emp.name, case["task_count"])
                    continue

                # Try negotiating with the most available agent
                for candidate in available[:2]:  # Try top 2 candidates
                    to_emp = candidate["employee"]

                    result = self.negotiator.propose_task_transfer(
                        task=task_to_transfer,
                        from_employee=emp,
                        to_employee=to_emp,
                        db=db,
                    )

                    # PHASE 3: emit per-step event for the circuit board
                    try:
                        from event_bus import emit_negotiation_step
                        emit_negotiation_step(
                            from_agent=f"Employee_{emp.id}",
                            to_agent=f"Employee_{to_emp.id}",
                            accepted=result.get("accepted"),
                        )
                    except Exception:
                        pass

                    await self.reporter.broadcast_negotiation_result(result)

                    if result["accepted"]:
                        self.negotiator.execute_task_transfer(
                            task_id=task_to_transfer.id,
                            new_owner_id=to_emp.id,
                            old_owner=emp,
                            new_owner=to_emp,
                            db=db,
                        )
                        await self.reporter.report_overload_resolution(
                            emp.name, task_to_transfer.title, to_emp.name
                        )
                        resolved += 1
                        break  # Move to next overloaded agent

            # ── Deadline Conflict Detection ───────────────────
            conflicts = self.detector.detect_deadline_conflicts(db)

            for case in conflicts:
                emp = case["employee"]
                await self.reporter.report_deadline_alert(
                    emp.name,
                    len(case["urgent_tasks"])
                )

            log.info(f"✅ Negotiation cycle complete. Resolved: {resolved}, Conflicts flagged: {len(conflicts)}")

            # PHASE 3: emit cycle complete event
            try:
                from event_bus import emit_negotiation_done
                emit_negotiation_done(resolved=resolved)
            except Exception:
                pass

        except Exception as e:
            log.error(f"Negotiation cycle error: {e}")
            try:
                from event_bus import emit_error
                emit_error(location="negotiation_cycle", message=str(e), actor="negotiation_engine")
            except Exception:
                pass
        finally:
            db.close()

    async def run_forever(self):
        """Background loop — runs negotiation every 30 minutes."""
        log.info("🤝 Multi-agent negotiation engine started")
        await asyncio.sleep(60)  # Wait 1 min after startup before first run

        while True:
            try:
                await self.run_negotiation_cycle()
            except Exception as e:
                log.error(f"Negotiation loop error: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SECS)


# ═══════════════════════════════════════════════════════════════
# MANUAL TRIGGER
# Allows manager to trigger negotiation on demand via AI command
# ═══════════════════════════════════════════════════════════════

async def trigger_negotiation_now() -> str:
    """
    Called when manager says 'rebalance team' or 'optimize workload'.
    Runs an immediate negotiation cycle and returns a summary.
    """
    orchestrator = NegotiationOrchestrator()
    await orchestrator.run_negotiation_cycle()
    return "Negotiation cycle complete. Check notifications and Glass Brain for results."


# Singleton instance used by main.py
negotiation_engine = NegotiationOrchestrator()