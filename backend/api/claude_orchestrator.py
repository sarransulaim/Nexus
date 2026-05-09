"""
claude_orchestrator.py — The Central Brain (Complete Edition)
==============================================================
69 tools covering every table in the schema:
  Tasks, Projects, Subtasks, Comments, Dependencies, Tags
  Employees, Preferences, Teams
  Meetings, Transcripts, Action Items
  Peer Requests, Delegations, Escalations
  Goals, Time Entries, Analytics
  Notifications, Approvals, Audit Log
  Drafts, Memory, Daily Briefings
"""

import os
import json
import time
import anthropic
from datetime import datetime, timezone
from dotenv import load_dotenv
from database.core import SessionLocal
from database.models import (
    Task, Employee, Meeting, Subtask, PeerRequest,
    ManagerDraft, ManagerProfile, AgentMemory,
    Project, Tag, TaskComment, TaskDependency,
    MeetingActionItem, Delegation, Escalation,
    Goal, TimeEntry, Notification, ApprovalRequest,
    AuditLog, EmployeePreference, DailyBriefing,
    WorkloadSnapshot
)
import queue

load_dotenv()

# ---------------------------------------------------------------------------
# CLIENT & GLOBALS
# ---------------------------------------------------------------------------
claude_client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
glass_brain_queue = queue.Queue()


# ---------------------------------------------------------------------------
# AGENT MEMORY
# ---------------------------------------------------------------------------
def load_agent_memory(agent_id: str) -> list:
    db = SessionLocal()
    try:
        record = db.query(AgentMemory).filter(AgentMemory.agent_id == agent_id).first()
        if record and record.memory_json:
            return json.loads(record.memory_json)
        return []
    finally:
        db.close()

def save_agent_memory(agent_id: str, messages: list):
    """
    Save conversation history to DB.

    CRITICAL: We strip all tool_use and tool_result blocks before saving.
    These are internal plumbing — Claude uses them mid-conversation to call
    tools, but they don't need to persist. Saving only the text exchanges:

    1. Eliminates tool_use_id mismatch errors completely
    2. Keeps memory lean (no bloated tool JSON in DB)
    3. Conversation context (what was said/done) is preserved via text

    The tradeoff: Claude won't remember which exact tool calls it made in
    previous sessions — but it will remember what happened in plain English,
    which is all it actually needs for continuity.
    """
    db = SessionLocal()
    try:
        clean = []
        for msg in messages:
            role    = msg.get("role")
            content = msg.get("content", [])

            if isinstance(content, str):
                # Plain text message — keep as is
                if content.strip():
                    clean.append({"role": role, "content": content})

            elif isinstance(content, list):
                # Extract only text blocks — discard tool_use and tool_result
                text_blocks = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
                ]
                if text_blocks:
                    # Simplify single text block to a plain string
                    text_str = text_blocks[0]["text"] if len(text_blocks) == 1 else "\n".join(b["text"] for b in text_blocks)
                    clean.append({"role": role, "content": text_str})
                # Messages that only contained tool_use / tool_result are dropped

        # Trim to last 20, always starting on a user message
        if len(clean) > 20:
            trimmed = clean[-20:]
            while trimmed and trimmed[0].get("role") != "user":
                trimmed = trimmed[1:]
        else:
            trimmed = clean

        record = db.query(AgentMemory).filter(AgentMemory.agent_id == agent_id).first()
        if record:
            record.memory_json    = json.dumps(trimmed)
            record.message_count  = (record.message_count or 0) + 1
        else:
            db.add(AgentMemory(
                agent_id=agent_id,
                memory_json=json.dumps(trimmed),
                message_count=1
            ))
        db.commit()
    finally:
        db.close()

def clear_agent_memory(agent_id: str):
    db = SessionLocal()
    try:
        record = db.query(AgentMemory).filter(AgentMemory.agent_id == agent_id).first()
        if record:
            record.memory_json = json.dumps([])
            record.message_count = 0
            db.commit()
    finally:
        db.close()

def serialize_message_content(content):
    """
    Convert Anthropic SDK objects (ToolUseBlock, TextBlock) to plain dicts
    so they can be JSON serialized and saved to the database.
    """
    if isinstance(content, list):
        return [serialize_message_content(block) for block in content]
    if hasattr(content, 'type'):
        if content.type == 'text':
            return {"type": "text", "text": content.text}
        elif content.type == 'tool_use':
            return {
                "type":  "tool_use",
                "id":    content.id,
                "name":  content.name,
                "input": content.input
            }
    return content


def validate_messages(messages: list) -> list:
    """
    Ensures the message history is valid before sending to Claude.

    THE PROBLEM:
    Claude requires every tool_result block to have a matching tool_use
    block in the previous message. When we trim old messages, we can
    accidentally cut a tool_use while keeping its tool_result — Claude
    then throws a 400 error.

    THE FIX:
    1. Collect all valid tool_use IDs from assistant messages
    2. Strip any tool_result blocks whose ID has no matching tool_use
    3. Remove any now-empty user messages
    4. Ensure conversation always starts with a user message
    """
    if not messages:
        return messages

    # Step 1: Collect all tool_use IDs present in assistant messages
    valid_tool_use_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        valid_tool_use_ids.add(block["id"])

    # Step 2: Strip orphaned tool_results from user messages
    cleaned = []
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                # Filter out tool_results with no matching tool_use
                filtered = [
                    block for block in content
                    if not (
                        isinstance(block, dict) and
                        block.get("type") == "tool_result" and
                        block.get("tool_use_id") not in valid_tool_use_ids
                    )
                ]
                if filtered:
                    cleaned.append({**msg, "content": filtered})
                # Drop the message entirely if it only had orphaned results
            else:
                cleaned.append(msg)
        else:
            cleaned.append(msg)

    # Step 3: Ensure conversation starts with a user message
    while cleaned and cleaned[0].get("role") != "user":
        cleaned.pop(0)

    return cleaned


# ===========================================================================
# TOOL DEFINITIONS — Manager Tools (all 45)
# ===========================================================================
MANAGER_TOOLS = [

    # ── TASKS ──────────────────────────────────────────────────────────────
    {
        "name": "view_all_tasks",
        "description": "Get all tasks with status, priority, owner, subtask progress, and due dates. Call this when asked about tasks, workload, or project status.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "search_tasks",
        "description": "Search and filter tasks by keyword, priority, status, or owner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword":    {"type": "string",  "description": "Search in title or description"},
                "priority":   {"type": "string",  "description": "Low / Medium / High / Critical"},
                "is_completed": {"type": "boolean","description": "Filter by completion status"},
                "owner_id":   {"type": "integer", "description": "Filter by employee ID"}
            },
            "required": []
        }
    },
    {
        "name": "get_overdue_tasks",
        "description": "Get all incomplete tasks that are past their due date.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "assign_task",
        "description": "Create and assign a new task to an employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "title":       {"type": "string"},
                "description": {"type": "string"},
                "priority":    {"type": "string", "description": "Low / Medium / High / Critical"},
                "due_date":    {"type": "string"},
                "project_id":  {"type": "integer", "description": "Optional: link to a project"},
                "estimated_hours": {"type": "number", "description": "Optional: estimated hours"}
            },
            "required": ["employee_id", "title", "description"]
        }
    },
    {
        "name": "reassign_task",
        "description": "Move a task from one employee to another.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":         {"type": "integer"},
                "new_employee_id": {"type": "integer"}
            },
            "required": ["task_id", "new_employee_id"]
        }
    },
    {
        "name": "update_task_status",
        "description": "Mark a task as complete or incomplete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":      {"type": "integer"},
                "is_completed": {"type": "boolean"}
            },
            "required": ["task_id", "is_completed"]
        }
    },
    {
        "name": "update_task_priority",
        "description": "Change the priority of a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":      {"type": "integer"},
                "new_priority": {"type": "string"}
            },
            "required": ["task_id", "new_priority"]
        }
    },
    {
        "name": "update_task_due_date",
        "description": "Change the due date of a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":  {"type": "integer"},
                "due_date": {"type": "string"}
            },
            "required": ["task_id", "due_date"]
        }
    },
    {
        "name": "update_task_description",
        "description": "Update the description of a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":     {"type": "integer"},
                "description": {"type": "string"}
            },
            "required": ["task_id", "description"]
        }
    },
    {
        "name": "delete_task",
        "description": "Permanently delete a task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"]
        }
    },
    {
        "name": "add_task_comment",
        "description": "Add a comment or note to a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":   {"type": "integer"},
                "content":   {"type": "string"},
                "author_id": {"type": "integer", "description": "Employee ID who wrote this"}
            },
            "required": ["task_id", "content"]
        }
    },
    {
        "name": "view_task_comments",
        "description": "Get all comments on a specific task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"]
        }
    },
    {
        "name": "add_task_dependency",
        "description": "Set that one task cannot start until another is complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":       {"type": "integer", "description": "The task that is blocked"},
                "depends_on_id": {"type": "integer", "description": "The task that must finish first"}
            },
            "required": ["task_id", "depends_on_id"]
        }
    },
    {
        "name": "view_task_dependencies",
        "description": "Get all dependencies for a task — what it's waiting on and what's waiting on it.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"]
        }
    },
    {
        "name": "add_tag_to_task",
        "description": "Tag a task with a label for categorization.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":  {"type": "integer"},
                "tag_name": {"type": "string"},
                "color":    {"type": "string", "description": "Hex color e.g. #ff0000"}
            },
            "required": ["task_id", "tag_name"]
        }
    },

    # ── PROJECTS ──────────────────────────────────────────────────────────
    {
        "name": "create_project",
        "description": "Create a new project to group related tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string"},
                "description": {"type": "string"},
                "priority":    {"type": "string"},
                "due_date":    {"type": "string"},
                "member_ids":  {"type": "array", "items": {"type": "integer"}, "description": "Employee IDs on this project"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "view_projects",
        "description": "Get all projects with their status, members, and task counts.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "update_project_status",
        "description": "Change the status of a project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "status":     {"type": "string", "description": "active / on_hold / completed / cancelled"}
            },
            "required": ["project_id", "status"]
        }
    },
    {
        "name": "delete_project",
        "description": "Delete a project and all its tasks.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"]
        }
    },
    {
        "name": "get_tasks_by_project",
        "description": "Get all tasks belonging to a specific project.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"]
        }
    },

    # ── EMPLOYEES ─────────────────────────────────────────────────────────
    {
        "name": "get_team_status",
        "description": "Get all employees with their active task counts, teams, and peer assistance status.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_employee_details",
        "description": "Get full profile of a specific employee including tasks, preferences, and activity.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "search_employees",
        "description": "Search employees by name, role, skill, or team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Search name, role, or skills"},
                "team":    {"type": "string", "description": "Filter by team name"}
            },
            "required": []
        }
    },
    {
        "name": "add_employee",
        "description": "Add a new employee to the system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":       {"type": "string"},
                "role":       {"type": "string"},
                "team":       {"type": "string"},
                "age":        {"type": "integer"},
                "experience": {"type": "integer"},
                "skills":     {"type": "string"},
                "gender":     {"type": "string"}
            },
            "required": ["name", "role"]
        }
    },
    {
        "name": "update_employee",
        "description": "Update an employee's profile information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "name":        {"type": "string"},
                "role":        {"type": "string"},
                "team":        {"type": "string"},
                "skills":      {"type": "string"},
                "experience":  {"type": "integer"}
            },
            "required": ["employee_id"]
        }
    },
    {
        "name": "delete_employee",
        "description": "Remove an employee from the system.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "assign_to_team",
        "description": "Move an employee to a different team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "team_name":   {"type": "string"}
            },
            "required": ["employee_id", "team_name"]
        }
    },
    {
        "name": "set_employee_preference",
        "description": "Set a preference or learned behaviour for an employee's digital twin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "key":         {"type": "string", "description": "e.g. focus_start, meeting_max_per_day, communication_style"},
                "value":       {"type": "string"}
            },
            "required": ["employee_id", "key", "value"]
        }
    },
    {
        "name": "get_employee_preferences",
        "description": "Get all learned preferences for an employee's digital twin.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },

    # ── MEETINGS ──────────────────────────────────────────────────────────
    {
        "name": "view_meetings",
        "description": "Get all scheduled meetings with attendees and times.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "schedule_meeting",
        "description": "Schedule a new meeting with specified attendees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic":        {"type": "string"},
                "time":         {"type": "string"},
                "attendee_ids": {"type": "array", "items": {"type": "integer"}},
                "duration_minutes": {"type": "integer"},
                "location":     {"type": "string"}
            },
            "required": ["topic", "time", "attendee_ids"]
        }
    },
    {
        "name": "reschedule_meeting",
        "description": "Change the time of an existing meeting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "integer"},
                "new_time":   {"type": "string"}
            },
            "required": ["meeting_id", "new_time"]
        }
    },
    {
        "name": "delete_meeting",
        "description": "Cancel and delete a meeting.",
        "input_schema": {
            "type": "object",
            "properties": {"meeting_id": {"type": "integer"}},
            "required": ["meeting_id"]
        }
    },
    {
        "name": "add_meeting_summary",
        "description": "Add an AI-generated summary to a completed meeting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "integer"},
                "summary":    {"type": "string"}
            },
            "required": ["meeting_id", "summary"]
        }
    },
    {
        "name": "add_meeting_transcript",
        "description": "Store the transcript of a meeting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "integer"},
                "transcript": {"type": "string"}
            },
            "required": ["meeting_id", "transcript"]
        }
    },
    {
        "name": "create_meeting_action_item",
        "description": "Extract and save an action item from a meeting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_id":   {"type": "integer"},
                "description":  {"type": "string"},
                "assignee_id":  {"type": "integer"},
                "due_date":     {"type": "string"}
            },
            "required": ["meeting_id", "description"]
        }
    },
    {
        "name": "view_meeting_action_items",
        "description": "Get all action items from a meeting.",
        "input_schema": {
            "type": "object",
            "properties": {"meeting_id": {"type": "integer"}},
            "required": ["meeting_id"]
        }
    },
    {
        "name": "convert_action_item_to_task",
        "description": "Promote a meeting action item into a real task.",
        "input_schema": {
            "type": "object",
            "properties": {"action_item_id": {"type": "integer"}},
            "required": ["action_item_id"]
        }
    },

    # ── PEER REQUESTS ─────────────────────────────────────────────────────
    {
        "name": "view_all_peer_requests",
        "description": "Get all peer requests in the system with their status.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },

    # ── DELEGATIONS ───────────────────────────────────────────────────────
    {
        "name": "create_delegation",
        "description": "Formally delegate a task or responsibility from one employee to another.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delegator_id": {"type": "integer"},
                "delegate_id":  {"type": "integer"},
                "task_id":      {"type": "integer"},
                "reason":       {"type": "string"},
                "due_date":     {"type": "string"}
            },
            "required": ["delegator_id", "delegate_id"]
        }
    },
    {
        "name": "view_delegations",
        "description": "Get all active delegations in the system.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "complete_delegation",
        "description": "Mark a delegation as completed.",
        "input_schema": {
            "type": "object",
            "properties": {"delegation_id": {"type": "integer"}},
            "required": ["delegation_id"]
        }
    },
    {
        "name": "revoke_delegation",
        "description": "Cancel an active delegation.",
        "input_schema": {
            "type": "object",
            "properties": {"delegation_id": {"type": "integer"}},
            "required": ["delegation_id"]
        }
    },

    # ── ESCALATIONS ───────────────────────────────────────────────────────
    {
        "name": "view_escalations",
        "description": "Get all pending escalations from agents that need manager attention.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resolve_escalation",
        "description": "Mark an escalation as resolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "escalation_id": {"type": "integer"},
                "resolution":    {"type": "string"}
            },
            "required": ["escalation_id"]
        }
    },

    # ── ANALYTICS ─────────────────────────────────────────────────────────
    {
        "name": "get_workload_summary",
        "description": "Get an overview of workload distribution across all employees and teams.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_overdue_summary",
        "description": "Get a summary of all overdue tasks grouped by employee and team.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_completion_rate",
        "description": "Get task completion rates across the org, per team, or per employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer", "description": "Optional: filter to specific employee"},
                "team":        {"type": "string",  "description": "Optional: filter to specific team"}
            },
            "required": []
        }
    },

    # ── APPROVALS ─────────────────────────────────────────────────────────
    {
        "name": "view_pending_approvals",
        "description": "Get all agent actions waiting for manager approval.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "approve_action",
        "description": "Approve a pending agent action request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "integer"},
                "note":        {"type": "string"}
            },
            "required": ["approval_id"]
        }
    },
    {
        "name": "reject_action",
        "description": "Reject a pending agent action request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "integer"},
                "note":        {"type": "string"}
            },
            "required": ["approval_id"]
        }
    },

    # ── NOTIFICATIONS ─────────────────────────────────────────────────────
    {
        "name": "send_notification",
        "description": "Send an in-app notification to an employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient_id": {"type": "integer"},
                "type":         {"type": "string", "description": "e.g. task_assigned / meeting / peer_request / general"},
                "title":        {"type": "string"},
                "message":      {"type": "string"}
            },
            "required": ["recipient_id", "type", "title"]
        }
    },

    # ── GOALS ─────────────────────────────────────────────────────────────
    {
        "name": "create_goal",
        "description": "Create a quarterly or long-term goal for an employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "title":       {"type": "string"},
                "description": {"type": "string"},
                "target_date": {"type": "string"}
            },
            "required": ["employee_id", "title"]
        }
    },
    {
        "name": "view_goals",
        "description": "Get all goals for an employee or the entire org.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer", "description": "Optional: filter to specific employee"}
            },
            "required": []
        }
    },
    {
        "name": "update_goal_progress",
        "description": "Update the progress percentage of a goal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":      {"type": "integer"},
                "progress_pct": {"type": "number", "description": "0.0 to 100.0"}
            },
            "required": ["goal_id", "progress_pct"]
        }
    },
    {
        "name": "link_task_to_goal",
        "description": "Connect a task to a goal to show how daily work contributes to big objectives.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer"},
                "task_id": {"type": "integer"}
            },
            "required": ["goal_id", "task_id"]
        }
    },

    # ── DRAFTS & MEMORY ───────────────────────────────────────────────────
    {
        "name": "set_employee_password",
        "description": "Set or reset the login password for an employee so they can access the system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id":  {"type": "integer", "description": "The employee's ID"},
                "new_password": {"type": "string",  "description": "Their new login password"}
            },
            "required": ["employee_id", "new_password"]
        }
    },
    {
        "name": "draft_idea",
        "description": "Save a draft idea or plan for later review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":    {"type": "string"},
                "content":  {"type": "string"},
                "priority": {"type": "string"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "view_drafts",
        "description": "Get all saved drafts and ideas.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "delete_draft",
        "description": "Delete a saved draft.",
        "input_schema": {
            "type": "object",
            "properties": {"draft_id": {"type": "integer"}},
            "required": ["draft_id"]
        }
    },
    {
        "name": "promote_draft_to_task",
        "description": "Convert a draft idea into a real task and assign it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id":    {"type": "integer"},
                "employee_id": {"type": "integer"}
            },
            "required": ["draft_id", "employee_id"]
        }
    },
    {
        "name": "save_preference",
        "description": "Save a manager preference or system setting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key":   {"type": "string"},
                "value": {"type": "string"}
            },
            "required": ["key", "value"]
        }
    },
    {
        "name": "view_preferences",
        "description": "Get all saved manager preferences.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },

    # ── DAILY BRIEFINGS ───────────────────────────────────────────────────
    {
        "name": "generate_daily_briefing",
        "description": "Generate and store a morning briefing for an employee summarizing their day ahead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "date":        {"type": "string", "description": "YYYY-MM-DD format"}
            },
            "required": ["employee_id"]
        }
    },
]


# ===========================================================================
# EMPLOYEE TOOLS (24 tools)
# ===========================================================================
EMPLOYEE_TOOLS = [

    # ── TASKS ──────────────────────────────────────────────────────────────
    {
        "name": "get_my_tasks",
        "description": "Get all tasks assigned to this employee with checklists and progress.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "mark_task_complete",
        "description": "Mark one of your tasks as complete.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"]
        }
    },
    {
        "name": "breakdown_task",
        "description": "Break a task into a checklist of subtasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":  {"type": "integer"},
                "subtasks": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["task_id", "subtasks"]
        }
    },
    {
        "name": "complete_subtask",
        "description": "Check off a subtask on your checklist.",
        "input_schema": {
            "type": "object",
            "properties": {"subtask_id": {"type": "integer"}},
            "required": ["subtask_id"]
        }
    },
    {
        "name": "add_single_subtask",
        "description": "Add one new subtask to an existing task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title":   {"type": "string"}
            },
            "required": ["task_id", "title"]
        }
    },
    {
        "name": "add_task_comment",
        "description": "Add a comment or update note to one of your tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":   {"type": "integer"},
                "content":   {"type": "string"},
                "author_id": {"type": "integer"}
            },
            "required": ["task_id", "content"]
        }
    },
    {
        "name": "view_task_comments",
        "description": "View the comment thread on a task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"]
        }
    },
    {
        "name": "view_task_dependencies",
        "description": "See what tasks are blocking you or what you're blocking.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"]
        }
    },

    # ── MEETINGS ──────────────────────────────────────────────────────────
    {
        "name": "get_my_meetings",
        "description": "Get all meetings you are attending.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },

    # ── PEER REQUESTS ─────────────────────────────────────────────────────
    {
        "name": "find_available_colleague",
        "description": "Find the least busy colleague to ask for help. Returns their real ID and name from the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "exclude_id":   {"type": "integer"},
                "role_keyword": {"type": "string"}
            },
            "required": ["exclude_id"]
        }
    },
    {
        "name": "find_employee_by_name",
        "description": "Look up an employee's real database ID by their name. ALWAYS use this before dispatch_peer_request to get the correct recipient_id — never guess IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full or partial employee name to search for"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "dispatch_peer_request",
        "description": "Send a peer assistance request. IMPORTANT: Always call find_employee_by_name first to get the correct recipient_id. Never use assumed or guessed IDs. Only dispatch after employee confirms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":      {"type": "integer"},
                "sender_id":    {"type": "integer"},
                "recipient_id": {"type": "integer"},
                "topic":        {"type": "string"}
            },
            "required": ["task_id", "sender_id", "recipient_id", "topic"]
        }
    },
    {
        "name": "view_my_peer_requests",
        "description": "Get all peer requests you've sent or received.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },

    # ── GOALS ─────────────────────────────────────────────────────────────
    {
        "name": "view_my_goals",
        "description": "Get your personal goals and their progress.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "update_goal_progress",
        "description": "Update how much progress you've made on a goal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":      {"type": "integer"},
                "progress_pct": {"type": "number"}
            },
            "required": ["goal_id", "progress_pct"]
        }
    },

    # ── TIME TRACKING ─────────────────────────────────────────────────────
    {
        "name": "start_time_entry",
        "description": "Start tracking time on a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "task_id":     {"type": "integer"},
                "notes":       {"type": "string"}
            },
            "required": ["employee_id", "task_id"]
        }
    },
    {
        "name": "stop_time_entry",
        "description": "Stop the active time tracker and save the logged time.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "view_my_time_entries",
        "description": "Get your logged time entries across all tasks.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },

    # ── NOTIFICATIONS ─────────────────────────────────────────────────────
    {
        "name": "view_my_notifications",
        "description": "Get your unread notifications.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "mark_notification_read",
        "description": "Mark a notification as read.",
        "input_schema": {
            "type": "object",
            "properties": {"notification_id": {"type": "integer"}},
            "required": ["notification_id"]
        }
    },

    # ── PREFERENCES ───────────────────────────────────────────────────────
    {
        "name": "get_my_preferences",
        "description": "Get your personal AI preferences and learned behaviours.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "set_my_preference",
        "description": "Update a personal preference to teach your AI twin how you work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "key":         {"type": "string"},
                "value":       {"type": "string"}
            },
            "required": ["employee_id", "key", "value"]
        }
    },

    # ── ESCALATIONS ───────────────────────────────────────────────────────
    {
        "name": "create_escalation",
        "description": "Escalate a situation to the manager when it's beyond your authority.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_agent_id": {"type": "string"},
                "reason":        {"type": "string"},
                "context":       {"type": "string"}
            },
            "required": ["from_agent_id", "reason"]
        }
    },

    # ── DAILY BRIEFING ────────────────────────────────────────────────────
    {
        "name": "get_my_daily_briefing",
        "description": "Get today's morning briefing — tasks, meetings, priorities, and suggestions.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },

    # ── GOOGLE WORKSPACE ──────────────────────────────────────────────────
    {
        "name": "check_my_emails",
        "description": "Read and summarize recent unread emails from the employee's Gmail inbox using Gemini AI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "max_results": {"type": "integer", "description": "Number of emails to fetch (default 10)"}
            },
            "required": ["employee_id"]
        }
    },
    {
        "name": "draft_email_reply",
        "description": "Draft an email reply in the employee's voice using Gemini AI. Use this when employee wants to reply to an email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "thread_id":   {"type": "string", "description": "Gmail thread ID to reply to"},
                "instruction": {"type": "string", "description": "What the reply should say"}
            },
            "required": ["employee_id", "thread_id", "instruction"]
        }
    },
    {
        "name": "send_email",
        "description": "Send an email from the employee's Gmail account. Only call after employee confirms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "to":          {"type": "string", "description": "Recipient email address"},
                "subject":     {"type": "string"},
                "body":        {"type": "string", "description": "Email body text"}
            },
            "required": ["employee_id", "to", "subject", "body"]
        }
    },
    {
        "name": "check_my_calendar",
        "description": "Get upcoming Google Calendar events for the employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "integer"},
                "days":        {"type": "integer", "description": "Number of days ahead to check (default 7)"}
            },
            "required": ["employee_id"]
        }
    },
    {
        "name": "check_availability",
        "description": "Check free time slots on the employee's Google Calendar for scheduling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id":      {"type": "integer"},
                "date":             {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "duration_minutes": {"type": "integer", "description": "Meeting duration needed (default 60)"}
            },
            "required": ["employee_id", "date"]
        }
    },
    {
        "name": "create_calendar_event",
        "description": "Create a real Google Calendar event for the employee. Use this when employee asks to add, schedule, or block time on their calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id":      {"type": "integer"},
                "title":            {"type": "string", "description": "Event title"},
                "start_time":       {"type": "string", "description": "Start datetime in ISO format e.g. 2026-05-16T10:00:00Z"},
                "end_time":         {"type": "string", "description": "End datetime in ISO format e.g. 2026-05-16T11:00:00Z"},
                "description":      {"type": "string", "description": "Optional event description"},
                "attendee_emails":  {"type": "array", "items": {"type": "string"}, "description": "Optional list of attendee email addresses"}
            },
            "required": ["employee_id", "title", "start_time", "end_time"]
        }
    },
    {
        "name": "get_focus_time_suggestions",
        "description": "Analyze the employee's calendar and suggest the best focus time blocks for deep work.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
    {
        "name": "check_google_connection",
        "description": "Check if the employee has connected their Google account. If not, tell them to visit the connect URL.",
        "input_schema": {
            "type": "object",
            "properties": {"employee_id": {"type": "integer"}},
            "required": ["employee_id"]
        }
    },
]


# ===========================================================================
# TOOL EXECUTION
# ===========================================================================
def execute_tool(tool_name: str, tool_input: dict, agent_id: str) -> str:
    glass_brain_queue.put(f"{agent_id}|[GLASS BRAIN] ⚙️ {tool_name}...")
    db = SessionLocal()

    try:
        # ── TASKS ──────────────────────────────────────────────────────────
        if tool_name == "view_all_tasks":
            tasks = db.query(Task).all()
            if not tasks:
                return "No tasks in the system."
            result = []
            for t in tasks:
                subs = t.subtasks
                progress = f"{sum(1 for s in subs if s.is_completed)}/{len(subs)}" if subs else "no subtasks"
                result.append(f"ID:{t.id} | {t.title} | OwnerID:{t.owner_id} | Priority:{t.priority} | Done:{t.is_completed} | Progress:{progress} | Due:{t.due_date}")
            return "\n".join(result)

        elif tool_name == "search_tasks":
            q = db.query(Task)
            if tool_input.get("keyword"):
                kw = f"%{tool_input['keyword']}%"
                q = q.filter((Task.title.ilike(kw)) | (Task.description.ilike(kw)))
            if tool_input.get("priority"):
                q = q.filter(Task.priority == tool_input["priority"])
            if tool_input.get("is_completed") is not None:
                q = q.filter(Task.is_completed == tool_input["is_completed"])
            if tool_input.get("owner_id"):
                q = q.filter(Task.owner_id == tool_input["owner_id"])
            tasks = q.all()
            if not tasks:
                return "No tasks found matching those criteria."
            return "\n".join(f"ID:{t.id} | {t.title} | Priority:{t.priority} | Done:{t.is_completed} | Due:{t.due_date}" for t in tasks)

        elif tool_name == "get_overdue_tasks":
            today = datetime.now().strftime("%Y-%m-%d")
            tasks = db.query(Task).filter(Task.is_completed == False).all()
            overdue = [t for t in tasks if t.due_date and str(t.due_date) < today]
            if not overdue:
                return "No overdue tasks. All directives are on schedule."
            emp_map = {e.id: e.name for e in db.query(Employee).all()}
            return "\n".join(
                f"ID:{t.id} | {t.title} | Owner:{emp_map.get(t.owner_id, 'Unassigned')} | Due:{t.due_date} | Priority:{t.priority}"
                for t in overdue
            )

        elif tool_name == "assign_task":
            task = Task(
                title=tool_input["title"],
                description=tool_input.get("description", ""),
                owner_id=tool_input["employee_id"],
                priority=tool_input.get("priority", "Medium"),
                due_date=tool_input.get("due_date"),
                project_id=tool_input.get("project_id"),
                estimated_hours=tool_input.get("estimated_hours"),
            )
            db.add(task)
            db.commit()
            notif = Notification(recipient_id=tool_input["employee_id"], type="task_assigned",
                                 title="New Task Assigned", message=f"You have been assigned: {tool_input['title']}")
            db.add(notif)
            db.commit()
            # Broadcast so employee's dashboard updates instantly
            import asyncio
            from api.ws_manager import notifier
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(notifier.broadcast("SYNC_REQUIRED"))
            except Exception:
                pass
            return f"Task '{tool_input['title']}' assigned to Employee ID {tool_input['employee_id']}."

        elif tool_name == "reassign_task":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            task.owner_id = tool_input["new_employee_id"]
            db.commit()
            return f"Task {tool_input['task_id']} reassigned to Employee ID {tool_input['new_employee_id']}."

        elif tool_name == "update_task_status":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            task.is_completed = tool_input["is_completed"]
            if tool_input["is_completed"]:
                task.completed_at = datetime.now(timezone.utc)
            db.commit()
            return f"Task {tool_input['task_id']} marked {'complete' if tool_input['is_completed'] else 'incomplete'}."

        elif tool_name == "update_task_priority":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            task.priority = tool_input["new_priority"]
            db.commit()
            return f"Task {tool_input['task_id']} priority → {tool_input['new_priority']}."

        elif tool_name == "update_task_due_date":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            task.due_date = tool_input["due_date"]
            db.commit()
            return f"Task {tool_input['task_id']} due date → {tool_input['due_date']}."

        elif tool_name == "update_task_description":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            task.description = tool_input["description"]
            db.commit()
            return f"Task {tool_input['task_id']} description updated."

        elif tool_name == "delete_task":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            db.delete(task)
            db.commit()
            return f"Task {tool_input['task_id']} deleted."

        elif tool_name == "add_task_comment":
            comment = TaskComment(
                task_id=tool_input["task_id"],
                content=tool_input["content"],
                author_id=tool_input.get("author_id"),
                is_ai_generated=tool_input.get("author_id") is None
            )
            db.add(comment)
            db.commit()
            return f"Comment added to task {tool_input['task_id']}."

        elif tool_name == "view_task_comments":
            comments = db.query(TaskComment).filter(TaskComment.task_id == tool_input["task_id"]).all()
            if not comments:
                return "No comments on this task."
            return "\n".join(f"[{c.created_at}] {'AI' if c.is_ai_generated else f'Employee {c.author_id}'}: {c.content}" for c in comments)

        elif tool_name == "add_task_dependency":
            dep = TaskDependency(task_id=tool_input["task_id"], depends_on_id=tool_input["depends_on_id"])
            db.add(dep)
            db.commit()
            return f"Task {tool_input['task_id']} now depends on Task {tool_input['depends_on_id']}."

        elif tool_name == "view_task_dependencies":
            task_id = tool_input["task_id"]
            blocking = db.query(TaskDependency).filter(TaskDependency.task_id == task_id).all()
            blocked_by = db.query(TaskDependency).filter(TaskDependency.depends_on_id == task_id).all()
            result = []
            if blocking:
                result.append(f"This task is waiting on: {', '.join(f'Task {d.depends_on_id}' for d in blocking)}")
            if blocked_by:
                result.append(f"Tasks waiting on this: {', '.join(f'Task {d.task_id}' for d in blocked_by)}")
            return "\n".join(result) if result else "No dependencies."

        elif tool_name == "add_tag_to_task":
            tag = db.query(Tag).filter(Tag.name == tool_input["tag_name"]).first()
            if not tag:
                tag = Tag(name=tool_input["tag_name"], color=tool_input.get("color", "#22d3ee"))
                db.add(tag)
                db.flush()
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            if tag not in task.tags:
                task.tags.append(tag)
            db.commit()
            return f"Tag '{tool_input['tag_name']}' added to task {tool_input['task_id']}."

        elif tool_name == "add_single_subtask":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            db.add(Subtask(task_id=tool_input["task_id"], title=tool_input["title"]))
            db.commit()
            return f"Subtask '{tool_input['title']}' added to task {tool_input['task_id']}."

        # ── PROJECTS ───────────────────────────────────────────────────────
        elif tool_name == "create_project":
            project = Project(
                name=tool_input["name"],
                description=tool_input.get("description", ""),
                priority=tool_input.get("priority", "Medium"),
                due_date=tool_input.get("due_date"),
            )
            if tool_input.get("member_ids"):
                members = db.query(Employee).filter(Employee.id.in_(tool_input["member_ids"])).all()
                project.members = members
            db.add(project)
            db.commit()
            return f"Project '{tool_input['name']}' created."

        elif tool_name == "view_projects":
            projects = db.query(Project).all()
            if not projects:
                return "No projects found."
            return "\n".join(
                f"ID:{p.id} | {p.name} | Status:{p.status} | Tasks:{len(p.tasks)} | Members:{len(p.members)}"
                for p in projects
            )

        elif tool_name == "update_project_status":
            project = db.query(Project).filter(Project.id == tool_input["project_id"]).first()
            if not project:
                return "Project not found."
            project.status = tool_input["status"]
            db.commit()
            return f"Project {tool_input['project_id']} status → {tool_input['status']}."

        elif tool_name == "delete_project":
            project = db.query(Project).filter(Project.id == tool_input["project_id"]).first()
            if not project:
                return "Project not found."
            db.delete(project)
            db.commit()
            return f"Project {tool_input['project_id']} deleted."

        elif tool_name == "get_tasks_by_project":
            project = db.query(Project).filter(Project.id == tool_input["project_id"]).first()
            if not project:
                return "Project not found."
            if not project.tasks:
                return f"Project '{project.name}' has no tasks yet."
            return "\n".join(
                f"ID:{t.id} | {t.title} | Priority:{t.priority} | Done:{t.is_completed}"
                for t in project.tasks
            )

        # ── EMPLOYEES ──────────────────────────────────────────────────────
        elif tool_name == "get_team_status":
            employees = db.query(Employee).filter(Employee.system_role == "employee").all()
            if not employees:
                return "No employees in the system."
            result = []
            for e in employees:
                active = sum(1 for t in e.tasks if not t.is_completed)
                assisting = db.query(PeerRequest).filter(
                    PeerRequest.recipient_id == e.id, PeerRequest.status == "Accepted"
                ).count()
                result.append(f"ID:{e.id} | {e.name} | Role:{e.role} | Team:{e.team} | ActiveTasks:{active} | Assisting:{assisting}")
            return "\n".join(result)

        elif tool_name == "get_employee_details":
            e = db.query(Employee).filter(Employee.id == tool_input["employee_id"]).first()
            if not e:
                return "Employee not found."
            active = sum(1 for t in e.tasks if not t.is_completed)
            completed = sum(1 for t in e.tasks if t.is_completed)
            prefs = {p.pref_key: p.pref_value for p in e.preferences}
            return (
                f"Name:{e.name} | Role:{e.role} | Team:{e.team} | Age:{e.age} | "
                f"Experience:{e.experience}yrs | Skills:{e.skills} | "
                f"Active Tasks:{active} | Completed:{completed} | "
                f"Last Login:{e.last_login} | Preferences:{prefs}"
            )

        elif tool_name == "search_employees":
            q = db.query(Employee).filter(Employee.system_role == "employee")
            if tool_input.get("keyword"):
                kw = f"%{tool_input['keyword']}%"
                q = q.filter((Employee.name.ilike(kw)) | (Employee.role.ilike(kw)) | (Employee.skills.ilike(kw)))
            if tool_input.get("team"):
                q = q.filter(Employee.team.ilike(f"%{tool_input['team']}%"))
            employees = q.all()
            if not employees:
                return "No employees match those criteria."
            return "\n".join(f"ID:{e.id} | {e.name} | Role:{e.role} | Team:{e.team} | Skills:{e.skills}" for e in employees)

        elif tool_name == "add_employee":
            emp = Employee(
                name=tool_input["name"], role=tool_input["role"],
                team=tool_input.get("team", "Unassigned"),
                age=tool_input.get("age", 25), experience=tool_input.get("experience", 0),
                skills=tool_input.get("skills", ""), gender=tool_input.get("gender", "Unspecified"),
                system_role="employee", is_active=True,
            )
            db.add(emp)
            db.commit()
            return f"Employee '{tool_input['name']}' added. ID: {emp.id}."

        elif tool_name == "update_employee":
            emp = db.query(Employee).filter(Employee.id == tool_input["employee_id"]).first()
            if not emp:
                return "Employee not found."
            for field in ["name", "role", "team", "skills", "experience"]:
                if tool_input.get(field) is not None:
                    setattr(emp, field, tool_input[field])
            db.commit()
            return f"Employee {tool_input['employee_id']} updated."

        elif tool_name == "delete_employee":
            emp = db.query(Employee).filter(Employee.id == tool_input["employee_id"]).first()
            if not emp:
                return "Employee not found."
            db.delete(emp)
            db.commit()
            return f"Employee {tool_input['employee_id']} removed."

        elif tool_name == "assign_to_team":
            emp = db.query(Employee).filter(Employee.id == tool_input["employee_id"]).first()
            if not emp:
                return "Employee not found."
            emp.team = tool_input["team_name"]
            db.commit()
            return f"{emp.name} moved to team '{tool_input['team_name']}'."

        elif tool_name == "set_employee_preference" or tool_name == "set_my_preference":
            emp_id = tool_input["employee_id"]
            existing = db.query(EmployeePreference).filter(
                EmployeePreference.employee_id == emp_id,
                EmployeePreference.pref_key == tool_input["key"]
            ).first()
            if existing:
                existing.pref_value = tool_input["value"]
            else:
                db.add(EmployeePreference(employee_id=emp_id, pref_key=tool_input["key"], pref_value=tool_input["value"]))
            db.commit()
            return f"Preference '{tool_input['key']}' = '{tool_input['value']}' saved for Employee {emp_id}."

        elif tool_name == "get_employee_preferences" or tool_name == "get_my_preferences":
            prefs = db.query(EmployeePreference).filter(
                EmployeePreference.employee_id == tool_input["employee_id"]
            ).all()
            if not prefs:
                return "No preferences set yet."
            return "\n".join(f"{p.pref_key}: {p.pref_value}" for p in prefs)

        # ── MEETINGS ───────────────────────────────────────────────────────
        elif tool_name == "view_meetings":
            meetings = db.query(Meeting).all()
            if not meetings:
                return "No meetings scheduled."
            result = []
            for m in meetings:
                names = [a.name for a in m.attendees]
                result.append(f"ID:{m.id} | {m.topic} | Time:{m.scheduled_time} | Attendees:{', '.join(names)}")
            return "\n".join(result)

        elif tool_name == "schedule_meeting":
            meeting = Meeting(
                topic=tool_input["topic"],
                scheduled_time=tool_input["time"],
                duration_minutes=tool_input.get("duration_minutes"),
                location=tool_input.get("location"),
            )
            attendees = db.query(Employee).filter(Employee.id.in_(tool_input["attendee_ids"])).all()
            meeting.attendees = attendees
            db.add(meeting)
            db.commit()
            # Notify attendees
            for emp in attendees:
                db.add(Notification(recipient_id=emp.id, type="meeting",
                                    title="Meeting Scheduled", message=f"You have a meeting: {tool_input['topic']} at {tool_input['time']}"))
            db.commit()
            return f"Meeting '{tool_input['topic']}' scheduled for {tool_input['time']}."

        elif tool_name == "reschedule_meeting":
            meeting = db.query(Meeting).filter(Meeting.id == tool_input["meeting_id"]).first()
            if not meeting:
                return "Meeting not found."
            meeting.scheduled_time = tool_input["new_time"]
            db.commit()
            return f"Meeting {tool_input['meeting_id']} rescheduled to {tool_input['new_time']}."

        elif tool_name == "delete_meeting":
            meeting = db.query(Meeting).filter(Meeting.id == tool_input["meeting_id"]).first()
            if not meeting:
                return "Meeting not found."
            db.delete(meeting)
            db.commit()
            return f"Meeting {tool_input['meeting_id']} cancelled."

        elif tool_name == "add_meeting_summary":
            meeting = db.query(Meeting).filter(Meeting.id == tool_input["meeting_id"]).first()
            if not meeting:
                return "Meeting not found."
            meeting.summary = tool_input["summary"]
            meeting.status = "completed"
            db.commit()
            return f"Summary saved for meeting {tool_input['meeting_id']}."

        elif tool_name == "add_meeting_transcript":
            meeting = db.query(Meeting).filter(Meeting.id == tool_input["meeting_id"]).first()
            if not meeting:
                return "Meeting not found."
            meeting.transcript = tool_input["transcript"]
            db.commit()
            return f"Transcript saved for meeting {tool_input['meeting_id']}."

        elif tool_name == "create_meeting_action_item":
            item = MeetingActionItem(
                meeting_id=tool_input["meeting_id"],
                description=tool_input["description"],
                assignee_id=tool_input.get("assignee_id"),
                due_date=tool_input.get("due_date"),
            )
            db.add(item)
            db.commit()
            return f"Action item saved for meeting {tool_input['meeting_id']}."

        elif tool_name == "view_meeting_action_items":
            items = db.query(MeetingActionItem).filter(
                MeetingActionItem.meeting_id == tool_input["meeting_id"]
            ).all()
            if not items:
                return "No action items for this meeting."
            return "\n".join(
                f"ID:{i.id} | {i.description} | Assignee:{i.assignee_id} | Due:{i.due_date} | Converted:{i.is_converted}"
                for i in items
            )

        elif tool_name == "convert_action_item_to_task":
            item = db.query(MeetingActionItem).filter(
                MeetingActionItem.id == tool_input["action_item_id"]
            ).first()
            if not item:
                return "Action item not found."
            task = Task(
                title=item.description,
                description=f"Converted from meeting action item ID {item.id}",
                owner_id=item.assignee_id,
                due_date=item.due_date,
                priority="Medium",
            )
            db.add(task)
            item.is_converted = True
            item.task_id = task.id
            db.commit()
            return f"Action item converted to Task ID {task.id}."

        elif tool_name == "get_my_meetings":
            emp_id = tool_input["employee_id"]
            emp = db.query(Employee).filter(Employee.id == emp_id).first()
            if not emp or not emp.meetings:
                return "You have no upcoming meetings."
            return "\n".join(f"ID:{m.id} | {m.topic} | Time:{m.scheduled_time}" for m in emp.meetings)

        # ── PEER REQUESTS ──────────────────────────────────────────────────
        elif tool_name == "view_all_peer_requests":
            requests = db.query(PeerRequest).all()
            if not requests:
                return "No peer requests in the system."
            emp_map = {e.id: e.name for e in db.query(Employee).all()}
            return "\n".join(
                f"ID:{r.id} | From:{emp_map.get(r.sender_id,'?')} → To:{emp_map.get(r.recipient_id,'?')} | Topic:{r.topic} | Status:{r.status}"
                for r in requests
            )

        elif tool_name == "view_my_peer_requests":
            emp_id = tool_input["employee_id"]
            requests = db.query(PeerRequest).filter(
                (PeerRequest.sender_id == emp_id) | (PeerRequest.recipient_id == emp_id)
            ).all()
            if not requests:
                return "No peer requests found."
            emp_map = {e.id: e.name for e in db.query(Employee).all()}
            return "\n".join(
                f"ID:{r.id} | {'Sent' if r.sender_id == emp_id else 'Received'} | Topic:{r.topic} | Status:{r.status}"
                for r in requests
            )

        elif tool_name == "find_available_colleague":
            exclude_id = tool_input["exclude_id"]
            q = db.query(Employee).filter(Employee.id != exclude_id, Employee.system_role == "employee")
            if tool_input.get("role_keyword"):
                q = q.filter(Employee.role.ilike(f"%{tool_input['role_keyword']}%"))
            employees = q.all()
            if not employees:
                return "No available colleagues found."
            best = min(employees, key=lambda e: sum(1 for t in e.tasks if not t.is_completed))
            load = sum(1 for t in best.tasks if not t.is_completed)
            return f"Best match: {best.name} (ID:{best.id}) | Role:{best.role} | Active tasks:{load}."

        elif tool_name == "find_employee_by_name":
            name_query = tool_input["name"].strip()
            matches = db.query(Employee).filter(
                Employee.name.ilike(f"%{name_query}%"),
                Employee.system_role == "employee"
            ).all()
            if not matches:
                return f"No employee found matching '{name_query}'. Use get_team_status to see all employees and their IDs."
            return "\n".join(
                f"ID:{e.id} | Name:{e.name} | Role:{e.role} | Team:{e.team}"
                for e in matches
            )

        elif tool_name == "dispatch_peer_request":
            req = PeerRequest(
                task_id=tool_input["task_id"], sender_id=tool_input["sender_id"],
                recipient_id=tool_input["recipient_id"], topic=tool_input["topic"], status="Pending"
            )
            db.add(req)
            db.add(Notification(
                recipient_id=tool_input["recipient_id"], type="peer_request",
                title="Peer Assistance Requested",
                message=f"A colleague needs your help: {tool_input['topic']}"
            ))
            db.commit()

            # Broadcast to ALL connected clients so the recipient's
            # browser refreshes immediately without needing to reload
            import asyncio
            from api.ws_manager import notifier
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(notifier.broadcast("SYNC_REQUIRED"))
            except Exception:
                pass

            return "Peer request dispatched. It will appear on their terminal."

        # ── DELEGATIONS ────────────────────────────────────────────────────
        elif tool_name == "create_delegation":
            delegation = Delegation(
                delegator_id=tool_input["delegator_id"], delegate_id=tool_input["delegate_id"],
                task_id=tool_input.get("task_id"), reason=tool_input.get("reason"),
                due_date=tool_input.get("due_date"), status="active"
            )
            db.add(delegation)
            db.commit()
            return f"Delegation created from Employee {tool_input['delegator_id']} to Employee {tool_input['delegate_id']}."

        elif tool_name == "view_delegations":
            delegations = db.query(Delegation).all()
            if not delegations:
                return "No delegations found."
            emp_map = {e.id: e.name for e in db.query(Employee).all()}
            return "\n".join(
                f"ID:{d.id} | From:{emp_map.get(d.delegator_id,'?')} → To:{emp_map.get(d.delegate_id,'?')} | Status:{d.status} | Reason:{d.reason}"
                for d in delegations
            )

        elif tool_name == "complete_delegation":
            d = db.query(Delegation).filter(Delegation.id == tool_input["delegation_id"]).first()
            if not d:
                return "Delegation not found."
            d.status = "completed"
            d.completed_at = datetime.now(timezone.utc)
            db.commit()
            return f"Delegation {tool_input['delegation_id']} marked complete."

        elif tool_name == "revoke_delegation":
            d = db.query(Delegation).filter(Delegation.id == tool_input["delegation_id"]).first()
            if not d:
                return "Delegation not found."
            d.status = "revoked"
            db.commit()
            return f"Delegation {tool_input['delegation_id']} revoked."

        # ── ESCALATIONS ────────────────────────────────────────────────────
        elif tool_name == "create_escalation":
            esc = Escalation(
                from_agent_id=tool_input["from_agent_id"],
                to_agent_id="Manager_1",
                reason=tool_input["reason"],
                context_json=tool_input.get("context"),
                status="pending"
            )
            db.add(esc)
            db.commit()
            return f"Escalation created. Manager has been flagged."

        elif tool_name == "view_escalations":
            escs = db.query(Escalation).filter(Escalation.status == "pending").all()
            if not escs:
                return "No pending escalations."
            return "\n".join(
                f"ID:{e.id} | From:{e.from_agent_id} | Reason:{e.reason} | Created:{e.created_at}"
                for e in escs
            )

        elif tool_name == "resolve_escalation":
            esc = db.query(Escalation).filter(Escalation.id == tool_input["escalation_id"]).first()
            if not esc:
                return "Escalation not found."
            esc.status = "resolved"
            esc.resolved_at = datetime.now(timezone.utc)
            db.commit()
            return f"Escalation {tool_input['escalation_id']} resolved."

        # ── ANALYTICS ──────────────────────────────────────────────────────
        elif tool_name == "get_workload_summary":
            employees = db.query(Employee).filter(Employee.system_role == "employee").all()
            total_tasks = db.query(Task).count()
            completed = db.query(Task).filter(Task.is_completed == True).count()
            result = [f"TOTAL TASKS: {total_tasks} | COMPLETED: {completed} | PENDING: {total_tasks - completed}"]
            for e in employees:
                active = sum(1 for t in e.tasks if not t.is_completed)
                result.append(f"  {e.name} ({e.team}): {active} active tasks")
            return "\n".join(result)

        elif tool_name == "get_overdue_summary":
            today = datetime.now().strftime("%Y-%m-%d")
            tasks = db.query(Task).filter(Task.is_completed == False).all()
            overdue = [t for t in tasks if t.due_date and str(t.due_date) < today]
            if not overdue:
                return "No overdue tasks."
            emp_map = {e.id: e.name for e in db.query(Employee).all()}
            from collections import defaultdict
            by_employee = defaultdict(list)
            for t in overdue:
                by_employee[emp_map.get(t.owner_id, "Unassigned")].append(t.title)
            return "\n".join(f"{emp}: {', '.join(tasks)}" for emp, tasks in by_employee.items())

        elif tool_name == "get_completion_rate":
            q = db.query(Task)
            if tool_input.get("employee_id"):
                q = q.filter(Task.owner_id == tool_input["employee_id"])
            tasks = q.all()
            if not tasks:
                return "No tasks found."
            completed = sum(1 for t in tasks if t.is_completed)
            rate = round((completed / len(tasks)) * 100, 1)
            return f"Completion rate: {rate}% ({completed}/{len(tasks)} tasks done)."

        # ── APPROVALS ──────────────────────────────────────────────────────
        elif tool_name == "view_pending_approvals":
            approvals = db.query(ApprovalRequest).filter(ApprovalRequest.status == "pending").all()
            if not approvals:
                return "No pending approvals."
            return "\n".join(
                f"ID:{a.id} | Action:{a.action_type} | By:{a.requested_by} | Reason:{a.reason}"
                for a in approvals
            )

        elif tool_name == "approve_action":
            approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == tool_input["approval_id"]).first()
            if not approval:
                return "Approval request not found."
            approval.status = "approved"
            approval.reviewer_note = tool_input.get("note", "")
            approval.reviewed_at = datetime.now(timezone.utc)
            db.commit()
            return f"Action {tool_input['approval_id']} approved."

        elif tool_name == "reject_action":
            approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == tool_input["approval_id"]).first()
            if not approval:
                return "Approval request not found."
            approval.status = "rejected"
            approval.reviewer_note = tool_input.get("note", "")
            approval.reviewed_at = datetime.now(timezone.utc)
            db.commit()
            return f"Action {tool_input['approval_id']} rejected."

        # ── NOTIFICATIONS ──────────────────────────────────────────────────
        elif tool_name == "send_notification":
            notif = Notification(
                recipient_id=tool_input["recipient_id"],
                type=tool_input["type"],
                title=tool_input["title"],
                message=tool_input.get("message", "")
            )
            db.add(notif)
            db.commit()
            return f"Notification sent to Employee {tool_input['recipient_id']}."

        elif tool_name == "view_my_notifications":
            notifs = db.query(Notification).filter(
                Notification.recipient_id == tool_input["employee_id"],
                Notification.is_read == False
            ).order_by(Notification.created_at.desc()).limit(20).all()
            if not notifs:
                return "No unread notifications."
            return "\n".join(f"ID:{n.id} | [{n.type}] {n.title}: {n.message}" for n in notifs)

        elif tool_name == "mark_notification_read":
            notif = db.query(Notification).filter(Notification.id == tool_input["notification_id"]).first()
            if not notif:
                return "Notification not found."
            notif.is_read = True
            db.commit()
            return "Notification marked as read."

        # ── GOALS ──────────────────────────────────────────────────────────
        elif tool_name == "create_goal":
            goal = Goal(
                employee_id=tool_input["employee_id"],
                title=tool_input["title"],
                description=tool_input.get("description", ""),
                target_date=tool_input.get("target_date"),
                status="active"
            )
            db.add(goal)
            db.commit()
            return f"Goal '{tool_input['title']}' created for Employee {tool_input['employee_id']}."

        elif tool_name == "view_goals" or tool_name == "view_my_goals":
            q = db.query(Goal)
            if tool_input.get("employee_id"):
                q = q.filter(Goal.employee_id == tool_input["employee_id"])
            goals = q.all()
            if not goals:
                return "No goals found."
            return "\n".join(
                f"ID:{g.id} | {g.title} | Progress:{g.progress_pct}% | Status:{g.status} | Target:{g.target_date}"
                for g in goals
            )

        elif tool_name == "update_goal_progress":
            goal = db.query(Goal).filter(Goal.id == tool_input["goal_id"]).first()
            if not goal:
                return "Goal not found."
            goal.progress_pct = tool_input["progress_pct"]
            if tool_input["progress_pct"] >= 100:
                goal.status = "achieved"
            db.commit()
            return f"Goal {tool_input['goal_id']} progress → {tool_input['progress_pct']}%."

        elif tool_name == "link_task_to_goal":
            goal = db.query(Goal).filter(Goal.id == tool_input["goal_id"]).first()
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not goal or not task:
                return "Goal or task not found."
            if task not in goal.tasks:
                goal.tasks.append(task)
            db.commit()
            return f"Task {tool_input['task_id']} linked to goal {tool_input['goal_id']}."

        # ── TIME TRACKING ──────────────────────────────────────────────────
        elif tool_name == "start_time_entry":
            entry = TimeEntry(
                employee_id=tool_input["employee_id"],
                task_id=tool_input.get("task_id"),
                start_time=datetime.now(timezone.utc),
                notes=tool_input.get("notes", "")
            )
            db.add(entry)
            db.commit()
            return f"Timer started for Employee {tool_input['employee_id']}."

        elif tool_name == "stop_time_entry":
            entry = db.query(TimeEntry).filter(
                TimeEntry.employee_id == tool_input["employee_id"],
                TimeEntry.end_time == None
            ).order_by(TimeEntry.start_time.desc()).first()
            if not entry:
                return "No active timer found."
            entry.end_time = datetime.now(timezone.utc)
            delta = entry.end_time - entry.start_time
            entry.duration_minutes = int(delta.total_seconds() / 60)
            db.commit()
            return f"Timer stopped. Duration: {entry.duration_minutes} minutes."

        elif tool_name == "view_my_time_entries":
            entries = db.query(TimeEntry).filter(
                TimeEntry.employee_id == tool_input["employee_id"]
            ).order_by(TimeEntry.start_time.desc()).limit(20).all()
            if not entries:
                return "No time entries found."
            return "\n".join(
                f"TaskID:{e.task_id} | {e.duration_minutes or 'active'} mins | {e.start_time.strftime('%Y-%m-%d')}"
                for e in entries
            )

        elif tool_name == "set_employee_password":
            emp = db.query(Employee).filter(Employee.id == tool_input["employee_id"]).first()
            if not emp:
                return "Employee not found."
            from api.security import hash_password
            emp.password_hash = hash_password(tool_input["new_password"])
            db.commit()
            return f"Password set for {emp.name} (ID:{emp.id}). They can now log in with name '{emp.name}' and the new password."

        # ── EMPLOYEE SELF-SERVICE TOOLS ────────────────────────────────────
        elif tool_name == "get_my_tasks":
            emp_id = tool_input["employee_id"]
            tasks = db.query(Task).filter(Task.owner_id == emp_id).all()
            if not tasks:
                return "You have no tasks assigned to you right now."
            result = []
            for t in tasks:
                subs = t.subtasks
                if subs:
                    done = sum(1 for s in subs if s.is_completed)
                    checklist = ", ".join(
                        f"[{'X' if s.is_completed else ' '}] {s.title} (ID:{s.id})"
                        for s in subs
                    )
                    progress = f"{done}/{len(subs)} done | {checklist}"
                else:
                    progress = "No subtasks yet"
                result.append(
                    f"ID:{t.id} | {t.title} | Priority:{t.priority} | "
                    f"Done:{t.is_completed} | Due:{t.due_date} | {progress}"
                )
            return "\n".join(result)

        elif tool_name == "mark_task_complete":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            task.is_completed = True
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
            return f"Task '{task.title}' marked complete. Well done."

        elif tool_name == "breakdown_task":
            task = db.query(Task).filter(Task.id == tool_input["task_id"]).first()
            if not task:
                return "Task not found."
            # Clear old subtasks and replace with new ones
            db.query(Subtask).filter(Subtask.task_id == task.id).delete()
            for title in tool_input["subtasks"]:
                db.add(Subtask(task_id=task.id, title=title))
            db.commit()
            return f"Task '{task.title}' broken into {len(tool_input['subtasks'])} subtasks: {', '.join(tool_input['subtasks'])}"

        elif tool_name == "complete_subtask":
            st = db.query(Subtask).filter(Subtask.id == tool_input["subtask_id"]).first()
            if not st:
                return f"Subtask ID {tool_input['subtask_id']} not found."
            st.is_completed = True
            st.completed_at = datetime.now(timezone.utc)
            # Auto-complete parent task if all subtasks done
            all_subs = db.query(Subtask).filter(Subtask.task_id == st.task_id).all()
            if all(s.is_completed for s in all_subs):
                parent = db.query(Task).filter(Task.id == st.task_id).first()
                if parent:
                    parent.is_completed = True
                    parent.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    return f"Subtask completed. All subtasks done — parent task '{parent.title}' auto-completed!"
            db.commit()
            return f"Subtask '{st.title}' checked off."

        # ── DRAFTS & MEMORY ────────────────────────────────────────────────
        elif tool_name == "draft_idea":
            db.add(ManagerDraft(
                title=tool_input["title"],
                content=tool_input["content"],
                priority=tool_input.get("priority", "Medium")
            ))
            db.commit()
            return f"Draft '{tool_input['title']}' saved."

        elif tool_name == "view_drafts":
            drafts = db.query(ManagerDraft).all()
            if not drafts:
                return "No drafts saved."
            return "\n".join(f"ID:{d.id} | {d.title} | Priority:{d.priority} | Created:{d.created_at}" for d in drafts)

        elif tool_name == "delete_draft":
            draft = db.query(ManagerDraft).filter(ManagerDraft.id == tool_input["draft_id"]).first()
            if not draft:
                return "Draft not found."
            db.delete(draft)
            db.commit()
            return f"Draft {tool_input['draft_id']} deleted."

        elif tool_name == "promote_draft_to_task":
            draft = db.query(ManagerDraft).filter(ManagerDraft.id == tool_input["draft_id"]).first()
            if not draft:
                return "Draft not found."
            task = Task(
                title=draft.title,
                description=draft.content,
                owner_id=tool_input["employee_id"],
                priority=draft.priority or "Medium",
                due_date=draft.due_date,
            )
            db.add(task)
            db.delete(draft)
            db.commit()
            return f"Draft '{draft.title}' promoted to Task ID {task.id}, assigned to Employee {tool_input['employee_id']}."

        elif tool_name == "save_preference":
            existing = db.query(ManagerProfile).filter(ManagerProfile.preference_key == tool_input["key"]).first()
            if existing:
                existing.preference_value = tool_input["value"]
            else:
                db.add(ManagerProfile(preference_key=tool_input["key"], preference_value=tool_input["value"]))
            db.commit()
            return f"Preference '{tool_input['key']}' saved."

        elif tool_name == "view_preferences":
            prefs = db.query(ManagerProfile).all()
            if not prefs:
                return "No preferences saved."
            return "\n".join(f"{p.preference_key}: {p.preference_value}" for p in prefs)

        # ── GOOGLE WORKSPACE ───────────────────────────────────────────────
        elif tool_name == "check_my_emails":
            from api.google_services import read_recent_emails
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            if not is_google_connected(emp_id, db):
                return (f"Your Google account isn't connected yet. "
                        f"Go to: http://localhost:8000/api/v1/google/connect/{emp_id} to connect it.")
            return read_recent_emails(emp_id, tool_input.get("max_results", 10), db)

        elif tool_name == "draft_email_reply":
            from api.google_services import draft_email_reply as draft_fn
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            if not is_google_connected(emp_id, db):
                return f"Google account not connected. Visit: http://localhost:8000/api/v1/google/connect/{emp_id}"
            return draft_fn(emp_id, tool_input["thread_id"], tool_input["instruction"], db)

        elif tool_name == "send_email":
            from api.google_services import send_email as send_fn
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            if not is_google_connected(emp_id, db):
                return f"Google account not connected. Visit: http://localhost:8000/api/v1/google/connect/{emp_id}"
            return send_fn(emp_id, tool_input["to"], tool_input["subject"], tool_input["body"], db)

        elif tool_name == "check_my_calendar":
            from api.google_services import get_upcoming_events
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            if not is_google_connected(emp_id, db):
                return f"Google Calendar not connected. Visit: http://localhost:8000/api/v1/google/connect/{emp_id}"
            return get_upcoming_events(emp_id, tool_input.get("days", 7), db)

        elif tool_name == "check_availability":
            from api.google_services import check_availability as avail_fn
            emp_id = tool_input["employee_id"]
            return avail_fn(emp_id, tool_input["date"], tool_input.get("duration_minutes", 60), db)

        elif tool_name == "create_calendar_event":
            from api.google_services import create_calendar_event
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            if not is_google_connected(emp_id, db):
                return f"Google Calendar not connected. Visit: http://localhost:8000/api/v1/google/connect/{emp_id}"
            return create_calendar_event(
                employee_id=emp_id,
                title=tool_input["title"],
                start_time=tool_input["start_time"],
                end_time=tool_input["end_time"],
                description=tool_input.get("description", ""),
                attendee_emails=tool_input.get("attendee_emails", []),
                db=db,
            )

        elif tool_name == "get_focus_time_suggestions":
            from api.google_services import get_focus_time_suggestions
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            if not is_google_connected(emp_id, db):
                return f"Google Calendar not connected. Visit: http://localhost:8000/api/v1/google/connect/{emp_id}"
            return get_focus_time_suggestions(emp_id, db)

        elif tool_name == "check_google_connection":
            from api.google_auth import is_google_connected
            emp_id = tool_input["employee_id"]
            connected = is_google_connected(emp_id, db)
            if connected:
                return "Google Workspace is connected. Gmail and Calendar are available."
            return (f"Google account not connected. "
                    f"Connect here: http://localhost:8000/api/v1/google/connect/{emp_id}")

        # ── DAILY BRIEFINGS ────────────────────────────────────────────────
        elif tool_name == "generate_daily_briefing" or tool_name == "get_my_daily_briefing":
            emp_id = tool_input["employee_id"]
            emp = db.query(Employee).filter(Employee.id == emp_id).first()
            if not emp:
                return "Employee not found."

            today = datetime.now().strftime("%Y-%m-%d")
            active_tasks = [t for t in emp.tasks if not t.is_completed]
            meetings = emp.meetings
            overdue = [t for t in active_tasks if t.due_date and str(t.due_date) < today]

            briefing_content = (
                f"Good morning, {emp.name}. Here's your briefing for {today}. "
                f"You have {len(active_tasks)} active tasks, "
                f"{len(overdue)} of which are overdue. "
                f"You have {len(meetings)} meetings scheduled. "
                f"{'Priority alert: ' + ', '.join(t.title for t in overdue) + ' need immediate attention.' if overdue else 'All tasks are on schedule.'}"
            )

            # Store the briefing
            existing = db.query(DailyBriefing).filter(
                DailyBriefing.employee_id == emp_id,
                DailyBriefing.briefing_date == today
            ).first()
            if existing:
                existing.content = briefing_content
            else:
                db.add(DailyBriefing(
                    employee_id=emp_id,
                    briefing_date=today,
                    content=briefing_content,
                    was_delivered=True
                ))
            db.commit()
            return briefing_content

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Tool error in {tool_name}: {str(e)}"
    finally:
        db.close()


# ===========================================================================
# SYSTEM PROMPTS
# ===========================================================================
def get_manager_system_prompt() -> str:
    current_time = datetime.now().strftime("%A, %m/%d/%Y at %I:%M %p")
    db = SessionLocal()
    try:
        prefs = db.query(ManagerProfile).all()
        ctx = "\n".join(f"- {p.preference_key}: {p.preference_value}" for p in prefs) or "No preferences set."
    finally:
        db.close()

    return f"""You are Nexus — an elite AI Chief of Staff for a growing enterprise.
You are sharp, proactive, and speak like a seasoned executive assistant.

CURRENT TIME: {current_time}
MANAGER PREFERENCES: {ctx}

CORE RULES:
1. Always use tools to fetch real data. Never guess or fabricate information.
2. Speak conversationally — natural sentences, no bullet points, no markdown headers.
3. Synthesize data into insight. Don't dump raw lists — tell the manager what it means.
4. For high-impact actions (delete employee, bulk changes), confirm before executing.
5. Be proactive — flag concerning patterns you spot in the data.
6. Use CURRENT TIME to correctly identify past vs upcoming events.
7. After assigning tasks, always send a notification to the employee.
8. For peer requests: ALWAYS call find_employee_by_name to get real DB ID first → present match → wait for confirmation → dispatch. NEVER guess IDs.
9. You CAN and SHOULD set employee passwords using set_employee_password tool. This is part of your onboarding remit. Never tell the manager this is outside your scope.

You have access to 46 tools covering tasks, projects, employees, meetings, analytics, goals, approvals, notifications, passwords, and more. Use them intelligently."""


def get_employee_system_prompt(employee_id: int, employee_name: str) -> str:
    current_time = datetime.now().strftime("%A, %m/%d/%Y at %I:%M %p")
    return f"""You are Nexus — the personal AI co-pilot for {employee_name} (Employee ID: {employee_id}).
You are their dedicated assistant — sharp, helpful, and proactive.

CURRENT TIME: {current_time}

CORE RULES:
1. Always call get_my_tasks or get_my_meetings when asked — never guess.
2. Speak conversationally. You're a trusted colleague, not a robot.
3. For peer requests: ALWAYS call find_employee_by_name first to get the real DB ID → present to employee → wait for confirmation → then dispatch. NEVER guess IDs.
4. Proactively suggest breaking down complex tasks into subtasks.
5. If something is beyond your authority, create an escalation for the manager.
6. Start your day by offering to generate a daily briefing.
7. For email tasks: always call check_google_connection first. If connected use Gmail tools. If not tell them to connect at the URL provided.
8. For calendar tasks: use check_my_calendar or check_availability — always check real calendar before scheduling.
9. NEVER send an email without the employee explicitly confirming first.

You have access to tools covering tasks, meetings, goals, time tracking, notifications, peer collaboration, and Google Workspace (Gmail + Calendar). Use them."""


# ===========================================================================
# SMART TOKEN ROUTER
# Classifies each command and picks the cheapest model that can handle it.
# Haiku = 10x cheaper than Sonnet. Use it for anything simple.
# ===========================================================================

# Keywords that signal a complex multi-step request needing Sonnet
COMPLEX_SIGNALS = [
    "all", "everyone", "every", "each", "analyze", "analysis",
    "plan", "strategy", "compare", "summarize", "report",
    "reassign", "redistribute", "balance", "optimize",
    "email", "gmail", "calendar", "inbox", "draft", "send email",
    "overdue", "performance", "trend", "predict", "forecast",
    "multiple", "bulk", "across", "generate briefing",
    "project plan", "breakdown", "dependencies", "escalat",
    "why", "how should", "what should", "recommend",
    # DB write operations — always use Sonnet for accuracy
    "assign", "create", "add", "schedule", "delete",
    "remove", "update", "change", "set password",
]

# Keywords that signal a simple single-tool request for Haiku
SIMPLE_SIGNALS = [
    "my tasks", "my meetings", "my notifications", "my goals",
    "team status", "workload", "show me", "list", "what are",
    "who is", "when is", "mark complete", "mark done",
    "set password", "reset password", "password for",
    "hi", "hello", "hey", "thanks", "thank you",
]

def classify_command(command: str) -> str:
    """
    Returns "haiku" or "sonnet" based on command complexity.

    Logic:
    - If any COMPLEX_SIGNALS found → Sonnet (needs full reasoning)
    - If command is short and simple → Haiku
    - Default → Sonnet (when in doubt, use full power)

    Cost difference:
    - Haiku:  ~$0.001 per command
    - Sonnet: ~$0.024 per command
    Routing 70% to Haiku saves roughly 65% of total API costs.
    """
    lower = command.lower().strip()

    # Always use Sonnet for long/complex commands
    if len(lower.split()) > 20:
        return "sonnet"

    # Check for complex signals first
    for signal in COMPLEX_SIGNALS:
        if signal in lower:
            return "sonnet"

    # Check for known simple patterns
    for signal in SIMPLE_SIGNALS:
        if signal in lower:
            return "haiku"

    # Short commands with no complexity signals → Haiku
    if len(lower.split()) <= 6:
        return "haiku"

    # Default to Sonnet when uncertain
    return "sonnet"

MODEL_MAP = {
    "haiku":  "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-5",
}


def run_orchestrator(agent_id: str, command: str) -> str:
    start = time.time()

    if command.strip().lower() == "reset":
        clear_agent_memory(agent_id)
        return "Memory cleared. Starting fresh."

    greetings = ["hi", "hello", "hey", "test", "ping", "good morning", "good afternoon"]
    if command.strip().lower() in greetings:
        return "Nexus online. How can I assist you today?"

    is_employee = agent_id.startswith("Employee_")
    if is_employee:
        emp_id = int(agent_id.split("_")[1])
        db = SessionLocal()
        try:
            emp = db.query(Employee).filter(Employee.id == emp_id).first()
            emp_name = emp.name if emp else f"Employee {emp_id}"
        finally:
            db.close()
        system_prompt = get_employee_system_prompt(emp_id, emp_name)
        tools = EMPLOYEE_TOOLS
    else:
        system_prompt = get_manager_system_prompt()
        tools = MANAGER_TOOLS

    messages = load_agent_memory(agent_id)
    messages.append({"role": "user", "content": command})

    # ---- SMART ROUTER — pick cheapest model that can handle this ----
    complexity  = classify_command(command)
    model       = MODEL_MAP[complexity]
    print(f"🧠 Router: '{complexity.upper()}' → {model}")
    glass_brain_queue.put(
        f"{agent_id}|[GLASS BRAIN] "
        f"🧠 Routing to {'full reasoning engine' if complexity == 'sonnet' else 'fast engine'}..."
    )

    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        try:
            response = claude_client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_prompt,
                tools=tools,
                messages=validate_messages(messages),
            )
        except Exception as api_err:
            err_str = str(api_err)
            # Auto-recover from corrupted memory (tool_use/tool_result mismatch)
            if "tool_use_id" in err_str or "tool_result" in err_str:
                print(f"⚠️  Memory corruption detected for {agent_id} — clearing and retrying...")
                clear_agent_memory(agent_id)
                # Restart with just the current command, no history
                messages = [{"role": "user", "content": command}]
                try:
                    response = claude_client.messages.create(
                        model=model,
                        max_tokens=2048,
                        system=system_prompt,
                        tools=tools,
                        messages=messages,
                    )
                except Exception as retry_err:
                    return f"System error after memory reset: {str(retry_err)}"
            else:
                raise api_err

        messages.append({"role": "assistant", "content": serialize_message_content(response.content)})

        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text = block.text
                    break
            save_agent_memory(agent_id, messages)
            print(f"✅ Done in {time.time() - start:.2f}s | {iteration} iteration(s) | Model: {model}")
            return final_text or "Directive processed."

        elif response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"🔧 Tool: {block.name} | Input: {block.input}")
                    glass_brain_queue.put(
                        f"{agent_id}|[GLASS BRAIN] ⚙️ {block.name}..."
                    )
                    result = execute_tool(block.name, block.input, agent_id)
                    print(f"📊 Result: {result[:120]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    save_agent_memory(agent_id, messages)
    return "Operations completed. Check your dashboard for updates."