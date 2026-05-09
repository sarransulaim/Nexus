"""
Nexus Core — Complete Database Schema
======================================
Covers every feature in the roadmap:
  - Auth & Users
  - Organization & Teams
  - Projects, Tasks, Subtasks, Dependencies, Tags, Comments
  - Meetings, Transcripts, Action Items
  - Peer Requests, Delegations, Escalations
  - AI Agents: Memory, Action Log, Approval Workflows, Audit Trail
  - Omnichannel: Channel Connections, Message Log
  - Google Workspace: OAuth Tokens, Email Summaries
  - Integration Marketplace
  - Employee Preferences & Digital Twin
  - Analytics: Time Entries, Workload Snapshots, Daily Briefings
  - Goals & Goal-Task linking
  - Notifications
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey,
    DateTime, Text, Float, Table, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database.core import Base


# ===========================================================================
# JUNCTION TABLES (many-to-many)
# ===========================================================================

meeting_attendees = Table(
    "meeting_attendees", Base.metadata,
    Column("meeting_id",  Integer, ForeignKey("meetings.id",  ondelete="CASCADE"), primary_key=True),
    Column("employee_id", Integer, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
)

task_tags = Table(
    "task_tags", Base.metadata,
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",  Integer, ForeignKey("tags.id",  ondelete="CASCADE"), primary_key=True),
)

project_members = Table(
    "project_members", Base.metadata,
    Column("project_id",  Integer, ForeignKey("projects.id",  ondelete="CASCADE"), primary_key=True),
    Column("employee_id", Integer, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
)

goal_tasks = Table(
    "goal_tasks", Base.metadata,
    Column("goal_id", Integer, ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True),
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
)


# ===========================================================================
# TIER 1 — ORGANIZATION (multi-tenant foundation)
# ===========================================================================

class Organization(Base):
    __tablename__ = "organizations"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(200), nullable=False)
    plan         = Column(String(50), default="starter")  # starter / pro / enterprise
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    employees    = relationship("Employee",    back_populates="organization", cascade="all, delete-orphan")
    teams        = relationship("Team",        back_populates="organization", cascade="all, delete-orphan")
    projects     = relationship("Project",     back_populates="organization", cascade="all, delete-orphan")
    integrations = relationship("Integration", back_populates="organization", cascade="all, delete-orphan")


# ===========================================================================
# TIER 1 — TEAMS (proper table)
# ===========================================================================

class Team(Base):
    __tablename__ = "teams"

    id          = Column(Integer, primary_key=True, index=True)
    org_id      = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    name        = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="teams")
    members      = relationship("Employee", back_populates="team_obj")


# ===========================================================================
# TIER 1 — EMPLOYEES
# ===========================================================================

class Employee(Base):
    __tablename__ = "employees"

    id            = Column(Integer, primary_key=True, index=True)
    org_id        = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    team_id       = Column(Integer, ForeignKey("teams.id",         ondelete="SET NULL"), nullable=True)

    # Identity
    name          = Column(String(100), nullable=False, index=True)
    role          = Column(String(100))
    experience    = Column(Integer, default=0)
    skills        = Column(Text, nullable=True)
    gender        = Column(String(20), nullable=True)
    age           = Column(Integer, nullable=True)

    # Auth
    password_hash = Column(String(255), nullable=True)
    is_active     = Column(Boolean, default=True)
    last_login    = Column(DateTime(timezone=True), nullable=True)
    refresh_token = Column(Text, nullable=True)
    system_role   = Column(String(20), default="employee")  # "manager" / "employee"

    # Legacy — keep for backward compat with existing code
    team          = Column(String(100), nullable=True, default="Unassigned")

    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    organization       = relationship("Organization",      back_populates="employees")
    team_obj           = relationship("Team",              back_populates="members")
    tasks              = relationship("Task",              back_populates="owner",      cascade="all, delete-orphan")
    meetings           = relationship("Meeting",           secondary=meeting_attendees, back_populates="attendees")
    preferences        = relationship("EmployeePreference", back_populates="employee",  cascade="all, delete-orphan")
    channel_connections = relationship("ChannelConnection", back_populates="employee",  cascade="all, delete-orphan")
    oauth_tokens       = relationship("OAuthToken",        back_populates="employee",  cascade="all, delete-orphan")
    notifications      = relationship("Notification",      back_populates="recipient", cascade="all, delete-orphan",
                                      foreign_keys="Notification.recipient_id")
    goals              = relationship("Goal",              back_populates="employee",  cascade="all, delete-orphan")
    time_entries       = relationship("TimeEntry",         back_populates="employee",  cascade="all, delete-orphan")
    daily_briefings    = relationship("DailyBriefing",     back_populates="employee",  cascade="all, delete-orphan")
    email_summaries    = relationship("EmailSummary",      back_populates="employee",  cascade="all, delete-orphan")
    workload_snapshots = relationship("WorkloadSnapshot",  back_populates="employee",  cascade="all, delete-orphan")
    projects           = relationship("Project",           secondary=project_members,  back_populates="members")


# ===========================================================================
# TIER 1 — EMPLOYEE PREFERENCES (digital twin building block)
# ===========================================================================

class EmployeePreference(Base):
    """
    Key-value store for per-employee AI learning.
    Keys: focus_start, focus_end, meeting_max_per_day,
          communication_style, preferred_channel, timezone, etc.
    """
    __tablename__ = "employee_preferences"

    id          = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    pref_key    = Column(String(100), nullable=False)
    pref_value  = Column(Text, nullable=True)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    employee    = relationship("Employee", back_populates="preferences")

    __table_args__ = (UniqueConstraint("employee_id", "pref_key", name="uq_employee_pref"),)


# ===========================================================================
# TIER 2 — PROJECTS
# ===========================================================================

class Project(Base):
    __tablename__ = "projects"

    id          = Column(Integer, primary_key=True, index=True)
    org_id      = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    name        = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status      = Column(String(50), default="active")   # active / on_hold / completed / cancelled
    priority    = Column(String(20), default="Medium")
    due_date    = Column(String(50), nullable=True)
    created_by  = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="projects")
    tasks        = relationship("Task",    back_populates="project", cascade="all, delete-orphan")
    members      = relationship("Employee", secondary=project_members, back_populates="projects")


# ===========================================================================
# TIER 2 — TAGS
# ===========================================================================

class Tag(Base):
    __tablename__ = "tags"

    id     = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    name   = Column(String(50), nullable=False)
    color  = Column(String(20), default="#22d3ee")

    tasks  = relationship("Task", secondary=task_tags, back_populates="tags")


# ===========================================================================
# TIER 2 — TASKS
# ===========================================================================

class Task(Base):
    __tablename__ = "tasks"

    id              = Column(Integer, primary_key=True, index=True)
    project_id      = Column(Integer, ForeignKey("projects.id",  ondelete="SET NULL"), nullable=True)
    owner_id        = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    title           = Column(String(255), nullable=False, index=True)
    description     = Column(Text, nullable=True)
    is_completed    = Column(Boolean, default=False)
    priority        = Column(String(20), default="Medium")  # Low / Medium / High / Critical
    due_date        = Column(String(50), nullable=True)
    estimated_hours = Column(Float, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at    = Column(DateTime(timezone=True), nullable=True)

    project       = relationship("Project",    back_populates="tasks")
    owner         = relationship("Employee",   back_populates="tasks")
    subtasks      = relationship("Subtask",    back_populates="parent_task",  cascade="all, delete-orphan")
    peer_requests = relationship("PeerRequest", back_populates="task",        cascade="all, delete-orphan")
    comments      = relationship("TaskComment", back_populates="task",        cascade="all, delete-orphan")
    time_entries  = relationship("TimeEntry",  back_populates="task",         cascade="all, delete-orphan")
    tags          = relationship("Tag",        secondary=task_tags,           back_populates="tasks")
    goals         = relationship("Goal",       secondary=goal_tasks,          back_populates="tasks")
    dependencies  = relationship("TaskDependency", foreign_keys="TaskDependency.task_id",
                                 back_populates="task", cascade="all, delete-orphan")
    blocking      = relationship("TaskDependency", foreign_keys="TaskDependency.depends_on_id",
                                 back_populates="depends_on", cascade="all, delete-orphan")


# ===========================================================================
# TIER 2 — TASK DEPENDENCIES
# ===========================================================================

class TaskDependency(Base):
    """task_id CANNOT start until depends_on_id is complete."""
    __tablename__ = "task_dependencies"

    id            = Column(Integer, primary_key=True, index=True)
    task_id       = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    depends_on_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    task       = relationship("Task", foreign_keys=[task_id],       back_populates="dependencies")
    depends_on = relationship("Task", foreign_keys=[depends_on_id], back_populates="blocking")

    __table_args__ = (UniqueConstraint("task_id", "depends_on_id", name="uq_task_dependency"),)


# ===========================================================================
# TIER 2 — SUBTASKS
# ===========================================================================

class Subtask(Base):
    __tablename__ = "subtasks"

    id           = Column(Integer, primary_key=True, index=True)
    task_id      = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    title        = Column(String(255), nullable=False)
    is_completed = Column(Boolean, default=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    parent_task  = relationship("Task", back_populates="subtasks")


# ===========================================================================
# TIER 2 — TASK COMMENTS
# ===========================================================================

class TaskComment(Base):
    __tablename__ = "task_comments"

    id              = Column(Integer, primary_key=True, index=True)
    task_id         = Column(Integer, ForeignKey("tasks.id",     ondelete="CASCADE"), nullable=False)
    author_id       = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    content         = Column(Text, nullable=False)
    is_ai_generated = Column(Boolean, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("Task", back_populates="comments")


# ===========================================================================
# TIER 2 — MEETINGS
# ===========================================================================

class Meeting(Base):
    __tablename__ = "meetings"

    id               = Column(Integer, primary_key=True, index=True)
    org_id           = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    topic            = Column(String(255), nullable=False)
    scheduled_time   = Column(String(100))
    duration_minutes = Column(Integer, nullable=True)
    location         = Column(String(255), nullable=True)
    status           = Column(String(30), default="scheduled")  # scheduled/in_progress/completed/cancelled

    # Meeting intelligence
    transcript       = Column(Text, nullable=True)
    summary          = Column(Text, nullable=True)
    recording_url    = Column(String(500), nullable=True)

    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), onupdate=func.now())

    attendees    = relationship("Employee",          secondary=meeting_attendees, back_populates="meetings")
    action_items = relationship("MeetingActionItem", back_populates="meeting",    cascade="all, delete-orphan")


# ===========================================================================
# TIER 2 — MEETING ACTION ITEMS
# ===========================================================================

class MeetingActionItem(Base):
    __tablename__ = "meeting_action_items"

    id           = Column(Integer, primary_key=True, index=True)
    meeting_id   = Column(Integer, ForeignKey("meetings.id",  ondelete="CASCADE"),  nullable=False)
    task_id      = Column(Integer, ForeignKey("tasks.id",     ondelete="SET NULL"), nullable=True)
    assignee_id  = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    description  = Column(Text, nullable=False)
    is_converted = Column(Boolean, default=False)
    due_date     = Column(String(50), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    meeting = relationship("Meeting", back_populates="action_items")


# ===========================================================================
# TIER 2 — PEER REQUESTS
# ===========================================================================

class PeerRequest(Base):
    __tablename__ = "peer_requests"

    id           = Column(Integer, primary_key=True, index=True)
    sender_id    = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"))
    recipient_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"))
    task_id      = Column(Integer, ForeignKey("tasks.id",     ondelete="CASCADE"))
    topic        = Column(Text)
    status       = Column(String(20), default="Pending")  # Pending/Accepted/Declined/Completed
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    task = relationship("Task", back_populates="peer_requests")


# ===========================================================================
# TIER 2 — DELEGATIONS
# ===========================================================================

class Delegation(Base):
    __tablename__ = "delegations"

    id           = Column(Integer, primary_key=True, index=True)
    delegator_id = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    delegate_id  = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    task_id      = Column(Integer, ForeignKey("tasks.id",     ondelete="CASCADE"),  nullable=True)
    reason       = Column(Text, nullable=True)
    status       = Column(String(30), default="active")  # active / completed / revoked
    due_date     = Column(String(50), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ===========================================================================
# TIER 2 — ESCALATIONS
# ===========================================================================

class Escalation(Base):
    __tablename__ = "escalations"

    id            = Column(Integer, primary_key=True, index=True)
    from_agent_id = Column(String(100), nullable=False)
    to_agent_id   = Column(String(100), nullable=False)
    reason        = Column(Text, nullable=False)
    context_json  = Column(Text, nullable=True)
    status        = Column(String(30), default="pending")  # pending / resolved / dismissed
    resolved_by   = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at   = Column(DateTime(timezone=True), nullable=True)


# ===========================================================================
# TIER 3 — AI AGENT MEMORY
# ===========================================================================

class AgentMemory(Base):
    __tablename__ = "agent_memory"

    id            = Column(Integer, primary_key=True, index=True)
    agent_id      = Column(String(100), unique=True, index=True, nullable=False)
    memory_json   = Column(Text, nullable=True)
    summary       = Column(Text, nullable=True)   # AI-compressed long-term memory
    last_active   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    message_count = Column(Integer, default=0)


# ===========================================================================
# TIER 3 — AGENT ACTION LOG
# ===========================================================================

class AgentActionLog(Base):
    __tablename__ = "agent_action_log"

    id            = Column(Integer, primary_key=True, index=True)
    agent_id      = Column(String(100), nullable=False, index=True)
    model_used    = Column(String(50),  nullable=True)   # "gemini-2.5-pro" / "claude-sonnet-4"
    tool_name     = Column(String(100), nullable=True)
    input_params  = Column(Text, nullable=True)          # JSON
    output        = Column(Text, nullable=True)
    latency_ms    = Column(Integer, nullable=True)
    success       = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ===========================================================================
# TIER 3 — APPROVAL REQUESTS
# ===========================================================================

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id              = Column(Integer, primary_key=True, index=True)
    requested_by    = Column(String(100), nullable=False)    # agent_id
    action_type     = Column(String(100), nullable=False)
    action_payload  = Column(Text, nullable=False)           # JSON
    reason          = Column(Text, nullable=True)
    status          = Column(String(30), default="pending")  # pending / approved / rejected
    reviewed_by     = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    reviewer_note   = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at     = Column(DateTime(timezone=True), nullable=True)
    expires_at      = Column(DateTime(timezone=True), nullable=True)


# ===========================================================================
# TIER 3 — AUDIT LOG
# ===========================================================================

class AuditLog(Base):
    __tablename__ = "audit_log"

    id           = Column(Integer, primary_key=True, index=True)
    actor_id     = Column(String(100), nullable=True)
    actor_type   = Column(String(20),  nullable=True)   # "human" / "agent" / "system"
    action       = Column(String(200), nullable=False)  # "task.complete" / "meeting.delete"
    entity_type  = Column(String(50),  nullable=True)
    entity_id    = Column(Integer, nullable=True)
    before_state = Column(Text, nullable=True)           # JSON
    after_state  = Column(Text, nullable=True)           # JSON
    ip_address   = Column(String(50), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ===========================================================================
# TIER 3 — NOTIFICATIONS
# ===========================================================================

class Notification(Base):
    __tablename__ = "notifications"

    id          = Column(Integer, primary_key=True, index=True)
    recipient_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String(50), nullable=False)   # "peer_request"/"task_assigned"/"meeting"/"approval"
    title       = Column(String(255), nullable=False)
    message     = Column(Text, nullable=True)
    entity_type = Column(String(50),  nullable=True)
    entity_id   = Column(Integer, nullable=True)
    is_read     = Column(Boolean, default=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    recipient = relationship("Employee", back_populates="notifications", foreign_keys=[recipient_id])


# ===========================================================================
# TIER 3 — GOALS
# ===========================================================================

class Goal(Base):
    __tablename__ = "goals"

    id           = Column(Integer, primary_key=True, index=True)
    employee_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    title        = Column(String(255), nullable=False)
    description  = Column(Text, nullable=True)
    target_date  = Column(String(50), nullable=True)
    progress_pct = Column(Float, default=0.0)
    status       = Column(String(30), default="active")  # active / achieved / abandoned
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    employee = relationship("Employee", back_populates="goals")
    tasks    = relationship("Task", secondary=goal_tasks, back_populates="goals")


# ===========================================================================
# TIER 3 — TIME ENTRIES
# ===========================================================================

class TimeEntry(Base):
    __tablename__ = "time_entries"

    id               = Column(Integer, primary_key=True, index=True)
    employee_id      = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    task_id          = Column(Integer, ForeignKey("tasks.id",     ondelete="CASCADE"), nullable=True)
    start_time       = Column(DateTime(timezone=True), nullable=False)
    end_time         = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    notes            = Column(Text, nullable=True)
    is_billable      = Column(Boolean, default=False)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee", back_populates="time_entries")
    task     = relationship("Task",     back_populates="time_entries")


# ===========================================================================
# TIER 3 — WORKLOAD SNAPSHOTS
# ===========================================================================

class WorkloadSnapshot(Base):
    __tablename__ = "workload_snapshots"

    id                     = Column(Integer, primary_key=True, index=True)
    employee_id            = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    snapshot_date          = Column(String(20), nullable=False)   # "2026-01-15"
    task_count             = Column(Integer, default=0)
    completed_count        = Column(Integer, default=0)
    overdue_count          = Column(Integer, default=0)
    hours_logged           = Column(Float,   default=0.0)
    peer_requests_sent     = Column(Integer, default=0)
    peer_requests_received = Column(Integer, default=0)
    meetings_attended      = Column(Integer, default=0)

    employee = relationship("Employee", back_populates="workload_snapshots")

    __table_args__ = (UniqueConstraint("employee_id", "snapshot_date", name="uq_snapshot_per_day"),)


# ===========================================================================
# TIER 3 — DAILY BRIEFINGS
# ===========================================================================

class DailyBriefing(Base):
    __tablename__ = "daily_briefings"

    id               = Column(Integer, primary_key=True, index=True)
    employee_id      = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    briefing_date    = Column(String(20), nullable=False)   # "2026-01-15"
    content          = Column(Text, nullable=False)
    was_delivered    = Column(Boolean, default=False)
    delivery_channel = Column(String(50), nullable=True)   # "email" / "slack" / "whatsapp"
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee", back_populates="daily_briefings")

    __table_args__ = (UniqueConstraint("employee_id", "briefing_date", name="uq_briefing_per_day"),)


# ===========================================================================
# OMNICHANNEL — CHANNEL CONNECTIONS
# ===========================================================================

class ChannelConnection(Base):
    __tablename__ = "channel_connections"

    id              = Column(Integer, primary_key=True, index=True)
    employee_id     = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    channel_type    = Column(String(50), nullable=False)   # "slack"/"whatsapp"/"teams"/"telegram"
    channel_user_id = Column(String(255), nullable=True)
    access_token    = Column(Text, nullable=True)          # encrypted
    refresh_token   = Column(Text, nullable=True)          # encrypted
    is_active       = Column(Boolean, default=True)
    connected_at    = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee", back_populates="channel_connections")

    __table_args__ = (UniqueConstraint("employee_id", "channel_type", name="uq_channel_per_employee"),)


# ===========================================================================
# OMNICHANNEL — MESSAGE LOG
# ===========================================================================

class MessageLog(Base):
    __tablename__ = "message_log"

    id          = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    channel     = Column(String(50), nullable=False)     # "slack"/"whatsapp"/"dashboard"
    direction   = Column(String(10), nullable=False)     # "inbound"/"outbound"
    content     = Column(Text, nullable=False)
    agent_id    = Column(String(100), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ===========================================================================
# GOOGLE WORKSPACE — OAUTH TOKENS
# ===========================================================================

class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id            = Column(Integer, primary_key=True, index=True)
    employee_id   = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    provider      = Column(String(50), nullable=False, default="google")  # google / microsoft
    access_token  = Column(Text, nullable=False)    # encrypted
    refresh_token = Column(Text, nullable=True)     # encrypted
    token_expiry  = Column(DateTime(timezone=True), nullable=True)
    scope         = Column(Text, nullable=True)
    connected_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    employee = relationship("Employee", back_populates="oauth_tokens")

    __table_args__ = (UniqueConstraint("employee_id", "provider", name="uq_oauth_per_provider"),)


# ===========================================================================
# GOOGLE WORKSPACE — EMAIL SUMMARIES
# ===========================================================================

class EmailSummary(Base):
    __tablename__ = "email_summaries"

    id              = Column(Integer, primary_key=True, index=True)
    employee_id     = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    gmail_thread_id = Column(String(255), nullable=False)
    subject         = Column(String(500), nullable=True)
    summary         = Column(Text, nullable=True)
    action_items    = Column(Text, nullable=True)   # JSON list
    sender          = Column(String(255), nullable=True)
    received_at     = Column(String(100), nullable=True)
    is_actioned     = Column(Boolean, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    employee = relationship("Employee", back_populates="email_summaries")

    __table_args__ = (UniqueConstraint("employee_id", "gmail_thread_id", name="uq_email_per_employee"),)


# ===========================================================================
# INTEGRATION MARKETPLACE
# ===========================================================================

class Integration(Base):
    __tablename__ = "integrations"

    id            = Column(Integer, primary_key=True, index=True)
    org_id        = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    name          = Column(String(100), nullable=False)   # "Jira" / "Salesforce"
    type          = Column(String(50),  nullable=False)   # "project_mgmt"/"crm"/"hr"/"custom"
    api_key       = Column(Text, nullable=True)           # encrypted
    api_secret    = Column(Text, nullable=True)           # encrypted
    webhook_url   = Column(String(500), nullable=True)
    base_url      = Column(String(500), nullable=True)
    config_json   = Column(Text, nullable=True)
    is_active     = Column(Boolean, default=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="integrations")


# ===========================================================================
# MANAGER PROFILE & DRAFTS (unchanged — keeps existing tools working)
# ===========================================================================

class ManagerProfile(Base):
    __tablename__ = "manager_profile"

    id               = Column(Integer, primary_key=True, index=True)
    preference_key   = Column(String(100), unique=True, index=True)
    preference_value = Column(Text)


class ManagerDraft(Base):
    __tablename__ = "manager_drafts"

    id         = Column(Integer, primary_key=True, index=True)
    title      = Column(String(255), index=True)
    content    = Column(Text)
    priority   = Column(String(20), default="Medium")
    due_date   = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())






