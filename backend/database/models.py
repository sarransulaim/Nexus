"""
models.py — Nexus Command Complete Data Model v3
=================================================
Verified clean — no duplicate index names, no __import__ hacks,
self-referential Message relationship fixed.

Rules applied:
  - index=True on column → auto-names ix_{table}_{column}
  - Index("name", col) in __table_args__ → explicit name
  - Never have BOTH for the same column with the same auto-name
  - Composite indexes only in __table_args__ (they can't be on column)
  - Single-column indexes either on column (index=True) OR in __table_args__,
    never both with the same resulting name
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, Float,
    DateTime, Date, ForeignKey, JSON, BigInteger,
    UniqueConstraint, Index, Table,
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def _now():
    return datetime.now(timezone.utc)


# ── Junction tables (defined before classes that use them) ────

project_members = Table(
    "project_members", Base.metadata,
    Column("project_id",  Integer, ForeignKey("projects.id",  ondelete="CASCADE"), primary_key=True),
    Column("employee_id", Integer, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
)

task_tags = Table(
    "task_tags", Base.metadata,
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",  Integer, ForeignKey("tags.id",  ondelete="CASCADE"), primary_key=True),
)

meeting_attendees = Table(
    "meeting_attendees", Base.metadata,
    Column("meeting_id",  Integer, ForeignKey("meetings.id",  ondelete="CASCADE"), primary_key=True),
    Column("employee_id", Integer, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
    Column("joined_at",   DateTime(timezone=True), nullable=True),
    Column("left_at",     DateTime(timezone=True), nullable=True),
)


# ═══════════════════════════════════════════════════════════════
# COMPANY
# ═══════════════════════════════════════════════════════════════

class Company(Base):
    __tablename__ = "companies"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(200), nullable=False)
    slug       = Column(String(100), unique=True, nullable=False, index=True)
    plan       = Column(String(50), default="starter")
    settings   = Column(JSON, default=dict)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    employees = relationship("Employee", back_populates="company", cascade="all, delete-orphan")
    teams     = relationship("Team",     back_populates="company", cascade="all, delete-orphan")
    projects  = relationship("Project",  back_populates="company", cascade="all, delete-orphan")


# ═══════════════════════════════════════════════════════════════
# TEAMS
# ═══════════════════════════════════════════════════════════════

class Team(Base):
    __tablename__ = "teams"

    id          = Column(Integer, primary_key=True, index=True)
    company_id  = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name        = Column(String(100), nullable=False)
    description = Column(Text, default="")
    lead_id     = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    created_at  = Column(DateTime(timezone=True), default=_now)

    company = relationship("Company", back_populates="teams")
    lead    = relationship("Employee", foreign_keys=[lead_id], post_update=True)
    members = relationship("Employee", foreign_keys="Employee.team_id", back_populates="team_obj")

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_team_company_name"),
        # No Index here — company_id already has index=True above
    )


# ═══════════════════════════════════════════════════════════════
# EMPLOYEES
# ═══════════════════════════════════════════════════════════════

class Employee(Base):
    __tablename__ = "employees"

    id            = Column(Integer, primary_key=True, index=True)
    company_id    = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name          = Column(String(200), nullable=False)
    email         = Column(String(254), nullable=True, index=True)
    role          = Column(String(200), default="Employee")
    system_role   = Column(String(50), default="employee")
    team_id       = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    team          = Column(String(100), nullable=True)
    password_hash = Column(String(256), nullable=True)
    refresh_token = Column(String(512), nullable=True)
    # Rotation: /auth/refresh issues a NEW refresh token every time and keeps
    # the outgoing one here briefly, so an in-flight retry (or a second tab
    # racing the same refresh) isn't punished. Presenting the PREVIOUS token
    # after the grace window means two parties hold it → treated as theft.
    refresh_token_prev       = Column(String(512), nullable=True)
    refresh_token_rotated_at = Column(DateTime(timezone=True), nullable=True)
    is_active     = Column(Boolean, default=True, index=True)
    last_login    = Column(DateTime(timezone=True), nullable=True)
    age           = Column(Integer, nullable=True)
    experience    = Column(Integer, default=0)
    skills        = Column(Text, default="")
    gender        = Column(String(50), default="Unspecified")
    avatar_url    = Column(String(500), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=_now)

    company       = relationship("Company", back_populates="employees")
    team_obj      = relationship("Team", foreign_keys=[team_id], back_populates="members")
    tasks         = relationship("Task", back_populates="owner", foreign_keys="Task.owner_id", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="recipient", cascade="all, delete-orphan")
    preferences   = relationship("EmployeePreference", back_populates="employee", cascade="all, delete-orphan")
    meetings      = relationship("Meeting", secondary="meeting_attendees", back_populates="attendees")
    goals         = relationship("Goal", back_populates="employee", cascade="all, delete-orphan")
    time_entries  = relationship("TimeEntry", back_populates="employee", cascade="all, delete-orphan")

    __table_args__ = (
        # Composite indexes — these cannot be on the column, must be here
        Index("ix_employees_company_active", "company_id", "is_active"),
        Index("ix_employees_company_role",   "company_id", "system_role"),
    )


# ═══════════════════════════════════════════════════════════════
# PROJECTS
# ═══════════════════════════════════════════════════════════════

class Project(Base):
    __tablename__ = "projects"

    id             = Column(Integer, primary_key=True, index=True)
    company_id     = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name           = Column(String(300), nullable=False)
    description    = Column(Text, default="")
    status         = Column(String(50), default="active")
    priority       = Column(String(50), default="Medium")
    due_date       = Column(Date, nullable=True)
    created_by     = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    source_file_id = Column(Integer, ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True)
    created_at     = Column(DateTime(timezone=True), default=_now)

    company = relationship("Company", back_populates="projects")
    tasks   = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    members = relationship("Employee", secondary=project_members)


# ═══════════════════════════════════════════════════════════════
# TASKS
# ═══════════════════════════════════════════════════════════════

class Task(Base):
    __tablename__ = "tasks"

    id              = Column(Integer, primary_key=True, index=True)
    company_id      = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id      = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    title           = Column(String(500), nullable=False)
    description     = Column(Text, default="")
    owner_id        = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True)
    priority        = Column(String(50), default="Medium")
    is_completed    = Column(Boolean, default=False, index=True)
    due_date        = Column(Date, nullable=True)           # NO index=True here — covered by __table_args__
    completed_at    = Column(DateTime(timezone=True), nullable=True)
    estimated_hours = Column(Float, nullable=True)
    created_at      = Column(DateTime(timezone=True), default=_now)
    updated_at      = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    owner         = relationship("Employee", back_populates="tasks", foreign_keys=[owner_id])
    project       = relationship("Project", back_populates="tasks")
    subtasks      = relationship("Subtask", back_populates="task", cascade="all, delete-orphan")
    comments      = relationship("TaskComment", back_populates="task", cascade="all, delete-orphan")
    peer_requests = relationship("PeerRequest", back_populates="task", cascade="all, delete-orphan")
    dependencies  = relationship("TaskDependency", foreign_keys="TaskDependency.task_id", cascade="all, delete-orphan")
    tags          = relationship("Tag", secondary=task_tags)
    time_entries  = relationship("TimeEntry", back_populates="task", cascade="all, delete-orphan")
    goal_links    = relationship("GoalTask", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tasks_company_owner",     "company_id", "owner_id"),
        Index("ix_tasks_company_completed", "company_id", "is_completed"),
        Index("ix_tasks_due_date",          "due_date"),   # Single-column here, NOT on column above
    )


class Subtask(Base):
    __tablename__ = "subtasks"

    id           = Column(Integer, primary_key=True, index=True)
    task_id      = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    title        = Column(String(500), nullable=False)
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)

    task = relationship("Task", back_populates="subtasks")


class TaskComment(Base):
    __tablename__ = "task_comments"

    id              = Column(Integer, primary_key=True, index=True)
    task_id         = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id       = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    content         = Column(Text, nullable=False)
    is_ai_generated = Column(Boolean, default=False)
    created_at      = Column(DateTime(timezone=True), default=_now)

    task = relationship("Task", back_populates="comments")


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    id            = Column(Integer, primary_key=True, index=True)
    task_id       = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    depends_on_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (UniqueConstraint("task_id", "depends_on_id", name="uq_task_dependency"),)


class Tag(Base):
    __tablename__ = "tags"

    id         = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name       = Column(String(100), nullable=False)
    color      = Column(String(20), default="#6366f1")

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_tag_company_name"),)


# ═══════════════════════════════════════════════════════════════
# MEETINGS
# ═══════════════════════════════════════════════════════════════

class Meeting(Base):
    __tablename__ = "meetings"

    id                 = Column(Integer, primary_key=True, index=True)
    company_id         = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    topic              = Column(String(500), nullable=False)
    scheduled_time     = Column(String(100), nullable=True)
    scheduled_date     = Column(Date, nullable=True, index=True)
    duration_minutes   = Column(Integer, nullable=True)
    location           = Column(String(300), nullable=True)
    status             = Column(String(50), default="scheduled")
    livekit_room_name  = Column(String(200), nullable=True, unique=True)
    livekit_started_at = Column(DateTime(timezone=True), nullable=True)
    livekit_ended_at   = Column(DateTime(timezone=True), nullable=True)
    # Google Calendar event backing this meeting (the Meet link lives in
    # `location`). Set when the organizer had Google connected at creation;
    # lets reschedule/cancel in Nexus also move/cancel the Google event.
    google_event_id    = Column(String(300), nullable=True)
    recording_path     = Column(String(500), nullable=True)
    transcript         = Column(Text, nullable=True)
    summary            = Column(Text, nullable=True)
    created_by         = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    created_at         = Column(DateTime(timezone=True), default=_now)

    attendees    = relationship("Employee", secondary="meeting_attendees", back_populates="meetings")
    action_items = relationship("MeetingActionItem", back_populates="meeting", cascade="all, delete-orphan")


class MeetingActionItem(Base):
    __tablename__ = "meeting_action_items"

    id           = Column(Integer, primary_key=True, index=True)
    meeting_id   = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    description  = Column(Text, nullable=False)
    assignee_id  = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    due_date     = Column(Date, nullable=True)
    is_converted = Column(Boolean, default=False)
    task_id      = Column(Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)

    meeting = relationship("Meeting", back_populates="action_items")


# ═══════════════════════════════════════════════════════════════
# CHAT — Channels, Messages, Reactions, Reads
# ═══════════════════════════════════════════════════════════════

class Channel(Base):
    __tablename__ = "channels"

    id          = Column(Integer, primary_key=True, index=True)
    company_id  = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text, default="")
    type        = Column(String(20), default="public")   # public / private / dm / project / ai
    project_id  = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    created_by  = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    is_archived = Column(Boolean, default=False)
    created_at  = Column(DateTime(timezone=True), default=_now)

    members  = relationship("ChannelMember", back_populates="channel", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("company_id", "name", "type", name="uq_channel_company_name_type"),
    )


class ChannelMember(Base):
    __tablename__ = "channel_members"

    id           = Column(Integer, primary_key=True, index=True)
    channel_id   = Column(Integer, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    joined_at    = Column(DateTime(timezone=True), default=_now)
    last_read_at = Column(DateTime(timezone=True), nullable=True)

    channel  = relationship("Channel", back_populates="members")
    employee = relationship("Employee")

    __table_args__ = (UniqueConstraint("channel_id", "employee_id", name="uq_channel_member"),)


class Message(Base):
    __tablename__ = "messages"

    id           = Column(Integer, primary_key=True, index=True)
    channel_id   = Column(Integer, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id    = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    content      = Column(Text, nullable=False)
    message_type = Column(String(30), default="text")   # text / file / ai / system / action_item
    reply_to_id  = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    is_edited    = Column(Boolean, default=False)
    is_deleted   = Column(Boolean, default=False)
    ai_agent_id  = Column(String(50), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)
    updated_at   = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    channel   = relationship("Channel", back_populates="messages")
    sender    = relationship("Employee", foreign_keys=[sender_id])
    reactions = relationship("MessageReaction", back_populates="message", cascade="all, delete-orphan")
    reads     = relationship("MessageRead", back_populates="message", cascade="all, delete-orphan")
    files     = relationship("MessageFile", back_populates="message", cascade="all, delete-orphan")

    # Self-referential: reply_to_id → parent message
    # Using primaryjoin string to avoid chicken-and-egg class reference issue
    reply_to = relationship(
        "Message",
        primaryjoin="Message.reply_to_id == remote(Message.id)",
        foreign_keys="[Message.reply_to_id]",
        uselist=False,
    )

    __table_args__ = (
        # Composite index on (channel_id, created_at) for paginated chat queries
        Index("ix_messages_channel_created", "channel_id", "created_at"),
    )


class MessageReaction(Base):
    __tablename__ = "message_reactions"

    id          = Column(Integer, primary_key=True, index=True)
    message_id  = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    emoji       = Column(String(20), nullable=False)
    created_at  = Column(DateTime(timezone=True), default=_now)

    message  = relationship("Message", back_populates="reactions")
    employee = relationship("Employee")

    __table_args__ = (UniqueConstraint("message_id", "employee_id", "emoji", name="uq_reaction"),)


class MessageRead(Base):
    __tablename__ = "message_reads"

    id          = Column(Integer, primary_key=True, index=True)
    message_id  = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    read_at     = Column(DateTime(timezone=True), default=_now)

    message = relationship("Message", back_populates="reads")

    __table_args__ = (UniqueConstraint("message_id", "employee_id", name="uq_message_read"),)


class MessageFile(Base):
    __tablename__ = "message_files"

    id               = Column(Integer, primary_key=True, index=True)
    message_id       = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_file_id = Column(Integer, ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True)
    filename         = Column(String(300), nullable=False)
    file_url         = Column(String(500), nullable=False)
    file_size        = Column(BigInteger, nullable=True)
    file_type        = Column(String(100), nullable=True)
    created_at       = Column(DateTime(timezone=True), default=_now)

    message = relationship("Message", back_populates="files")


# ═══════════════════════════════════════════════════════════════
# FILE INTELLIGENCE
# ═══════════════════════════════════════════════════════════════

class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id                = Column(Integer, primary_key=True, index=True)
    company_id        = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_id       = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    original_filename = Column(String(500), nullable=False)
    stored_filename   = Column(String(500), nullable=False)
    file_path         = Column(String(1000), nullable=False)
    file_size         = Column(BigInteger, nullable=True)
    file_type         = Column(String(100), nullable=True)
    source            = Column(String(50), default="dashboard")
    ai_analyzed       = Column(Boolean, default=False)
    extracted_text    = Column(Text, nullable=True)
    created_at        = Column(DateTime(timezone=True), default=_now)

    extractions = relationship("FileExtraction", back_populates="file", cascade="all, delete-orphan")


class FileExtraction(Base):
    __tablename__ = "file_extractions"

    id              = Column(Integer, primary_key=True, index=True)
    file_id         = Column(Integer, ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False, index=True)
    extraction_type = Column(String(50), default="project")
    result_json     = Column(JSON, nullable=False)
    confirmed_by    = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    confirmed_at    = Column(DateTime(timezone=True), nullable=True)
    executed        = Column(Boolean, default=False)
    executed_at     = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), default=_now)


    file = relationship("UploadedFile", back_populates="extractions")


# ═══════════════════════════════════════════════════════════════
# OMNICHANNEL
# ═══════════════════════════════════════════════════════════════
class ChannelConnection(Base):
    __tablename__ = "channel_connections"

    id                = Column(Integer, primary_key=True, index=True)
    company_id        = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id       = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    platform          = Column(String(50), nullable=False)
    platform_user_id  = Column(String(300), nullable=False)
    platform_username = Column(String(300), nullable=True)
    platform_phone    = Column(String(30), nullable=True)
    verified                = Column(Boolean, default=False, nullable=False)
    verification_code       = Column(String(10), nullable=True)
    verification_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_primary        = Column(Boolean, default=False, nullable=False)
    is_active         = Column(Boolean, default=True)
    connected_at      = Column(DateTime(timezone=True), default=_now)
    last_message_at   = Column(DateTime(timezone=True), nullable=True)

    employee = relationship("Employee")

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_channel_platform_user"),
        Index("ix_channel_connections_platform_user", "platform", "platform_user_id"),
    )


class ChannelMessageLog(Base):
    __tablename__ = "channel_message_logs"

    id                  = Column(Integer, primary_key=True, index=True)
    company_id          = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id         = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    platform            = Column(String(50), nullable=False)
    direction           = Column(String(10), nullable=False)
    content             = Column(Text, nullable=False)
    platform_message_id = Column(String(300), nullable=True, index=True)
    status              = Column(String(20), default="received")
    created_at          = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_channel_message_log_emp_platform", "employee_id", "platform"),
    )


# ═══════════════════════════════════════════════════════════════
# PEER REQUESTS, DELEGATIONS, ESCALATIONS
# ═══════════════════════════════════════════════════════════════

class PeerRequest(Base):
    __tablename__ = "peer_requests"

    id            = Column(Integer, primary_key=True, index=True)
    company_id    = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id       = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id     = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    recipient_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    topic         = Column(Text, nullable=False)
    status        = Column(String(50), default="Pending")
    ai_negotiated = Column(Boolean, default=False)
    created_at    = Column(DateTime(timezone=True), default=_now)

    task      = relationship("Task", back_populates="peer_requests")
    sender    = relationship("Employee", foreign_keys=[sender_id])
    recipient = relationship("Employee", foreign_keys=[recipient_id])


class Delegation(Base):
    __tablename__ = "delegations"

    id           = Column(Integer, primary_key=True, index=True)
    company_id   = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    delegator_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    delegate_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    task_id      = Column(Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    reason       = Column(Text, nullable=True)
    status       = Column(String(50), default="active")
    due_date     = Column(Date, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)


class Escalation(Base):
    __tablename__ = "escalations"

    id            = Column(Integer, primary_key=True, index=True)
    company_id    = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    from_agent_id = Column(String(100), nullable=False)
    to_agent_id   = Column(String(100), nullable=False)
    reason        = Column(Text, nullable=False)
    context_json  = Column(JSON, nullable=True)
    status        = Column(String(50), default="pending")
    resolved_by   = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    resolved_at   = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_escalations_company_status", "company_id", "status"),)


# ═══════════════════════════════════════════════════════════════
# GOALS
# ═══════════════════════════════════════════════════════════════

class Goal(Base):
    __tablename__ = "goals"

    id           = Column(Integer, primary_key=True, index=True)
    company_id   = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    title        = Column(String(500), nullable=False)
    description  = Column(Text, default="")
    progress_pct = Column(Float, default=0.0)
    status       = Column(String(50), default="active")
    target_date  = Column(Date, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)

    employee   = relationship("Employee", back_populates="goals")
    task_links = relationship("GoalTask", back_populates="goal", cascade="all, delete-orphan")


class GoalTask(Base):
    __tablename__ = "goal_tasks"

    id         = Column(Integer, primary_key=True, index=True)
    goal_id    = Column(Integer, ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id    = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    goal = relationship("Goal", back_populates="task_links")
    task = relationship("Task", back_populates="goal_links")

    __table_args__ = (UniqueConstraint("goal_id", "task_id", name="uq_goal_task"),)


# ═══════════════════════════════════════════════════════════════
# TIME TRACKING
# ═══════════════════════════════════════════════════════════════

class TimeEntry(Base):
    __tablename__ = "time_entries"

    id               = Column(Integer, primary_key=True, index=True)
    employee_id      = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id          = Column(Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    start_time       = Column(DateTime(timezone=True), nullable=False)
    end_time         = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    notes            = Column(Text, default="")
    created_at       = Column(DateTime(timezone=True), default=_now)

    employee = relationship("Employee", back_populates="time_entries")
    task     = relationship("Task", back_populates="time_entries")


# ═══════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

class Notification(Base):
    __tablename__ = "notifications"

    id           = Column(Integer, primary_key=True, index=True)
    company_id   = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    type         = Column(String(100), nullable=False)
    title        = Column(String(300), nullable=False)
    message      = Column(Text, default="")
    is_read      = Column(Boolean, default=False, index=True)
    entity_type  = Column(String(50), nullable=True)
    entity_id    = Column(Integer, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)

    recipient = relationship("Employee", back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_recipient_unread", "recipient_id", "is_read"),
    )


# ═══════════════════════════════════════════════════════════════
# AUTH & OAUTH
# ═══════════════════════════════════════════════════════════════

class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id           = Column(Integer, primary_key=True, index=True)
    employee_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    provider     = Column(String(50), nullable=False)
    access_token = Column(Text, nullable=False)
    token_expiry = Column(DateTime(timezone=True), nullable=True)
    scope        = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now)
    updated_at   = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("employee_id", "provider", name="uq_oauth_employee_provider"),)


class AppConnection(Base):
    __tablename__ = "app_connections"

    id              = Column(Integer, primary_key=True, index=True)
    company_id      = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id     = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=True)
    app_name        = Column(String(100), nullable=False)
    connection_data = Column(JSON, nullable=True)
    is_active       = Column(Boolean, default=True)
    connected_at    = Column(DateTime(timezone=True), default=_now)
    expires_at      = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("company_id", "employee_id", "app_name", name="uq_app_connection"),)


# ═══════════════════════════════════════════════════════════════
# AI AGENT MEMORY
# ═══════════════════════════════════════════════════════════════

class AgentMemory(Base):
    __tablename__ = "agent_memory"

    id            = Column(Integer, primary_key=True, index=True)
    company_id    = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id      = Column(String(100), nullable=False, index=True)
    memory_json   = Column(Text, default="[]")
    message_count = Column(Integer, default=0)
    last_updated  = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("company_id", "agent_id", name="uq_agent_memory"),)


# ═══════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════

class AiSpend(Base):
    """One row per model API call, with its measured cost.

    Cost was previously emitted to the event bus only — visible on the live
    circuit board and gone the moment the process restarted. Nothing could
    answer "what did we spend yesterday", and nothing could STOP a runaway:
    one account could issue commands until the API bill said otherwise.
    Persisted so it can be both queried and enforced (see api/spend.py).
    """
    __tablename__ = "ai_spend"

    id             = Column(Integer, primary_key=True, index=True)
    company_id     = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    # Nullable: background jobs (digests, drift alerts) spend money without a
    # person behind them. They count toward the company total, not a user's.
    employee_id    = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    agent_id       = Column(String(100), nullable=True)
    model          = Column(String(100), nullable=True)
    input_tokens   = Column(Integer, default=0)
    output_tokens  = Column(Integer, default=0)
    cache_read_tokens  = Column(Integer, default=0)
    cache_write_tokens = Column(Integer, default=0)
    cost_usd       = Column(Float, default=0.0)
    created_at     = Column(DateTime(timezone=True), default=_now, index=True)

    __table_args__ = (
        # The enforcement query is "spend for this person since midnight" and
        # "spend for this company since midnight" — both want these composites.
        Index("ix_spend_company_created", "company_id", "created_at"),
        Index("ix_spend_employee_created", "employee_id", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id             = Column(Integer, primary_key=True, index=True)
    company_id     = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id       = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    actor_agent_id = Column(String(100), nullable=True)
    action         = Column(String(200), nullable=False)
    entity_type    = Column(String(100), nullable=True)
    entity_id      = Column(Integer, nullable=True)
    old_value      = Column(JSON, nullable=True)
    new_value      = Column(JSON, nullable=True)
    ip_address     = Column(String(45), nullable=True)
    created_at     = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_audit_company_created", "company_id", "created_at"),
        Index("ix_audit_entity",          "entity_type", "entity_id"),
    )


# ═══════════════════════════════════════════════════════════════
# APPROVAL REQUESTS
# ═══════════════════════════════════════════════════════════════

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id            = Column(Integer, primary_key=True, index=True)
    company_id    = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by  = Column(String(100), nullable=False)
    action_type   = Column(String(200), nullable=False)
    payload       = Column(JSON, nullable=True)
    status        = Column(String(50), default="pending")
    reviewer_id   = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    reviewer_note = Column(Text, nullable=True)
    reviewed_at   = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=_now)


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE PREFERENCES & MANAGER PROFILES
# ═══════════════════════════════════════════════════════════════

class EmployeePreference(Base):
    __tablename__ = "employee_preferences"

    id          = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    pref_key    = Column(String(200), nullable=False)
    pref_value  = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), default=_now)

    employee = relationship("Employee", back_populates="preferences")

    __table_args__ = (UniqueConstraint("employee_id", "pref_key", name="uq_employee_pref"),)


class ManagerProfile(Base):
    __tablename__ = "manager_profiles"

    id               = Column(Integer, primary_key=True, index=True)
    company_id       = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    preference_key   = Column(String(200), nullable=False)
    preference_value = Column(Text, nullable=True)
    created_at       = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("company_id", "preference_key", name="uq_manager_pref"),)


class ManagerDraft(Base):
    __tablename__ = "manager_drafts"

    id         = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    title      = Column(String(500), nullable=False)
    content    = Column(Text, nullable=False)
    priority   = Column(String(50), default="Medium")
    due_date   = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


# ═══════════════════════════════════════════════════════════════
# DAILY BRIEFINGS & WORKLOAD SNAPSHOTS
# ═══════════════════════════════════════════════════════════════

class DailyBriefing(Base):
    __tablename__ = "daily_briefings"

    id            = Column(Integer, primary_key=True, index=True)
    employee_id   = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    briefing_date = Column(Date, nullable=False)
    content       = Column(Text, nullable=False)
    was_delivered = Column(Boolean, default=False)
    created_at    = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("employee_id", "briefing_date", name="uq_briefing_date"),)


class WorkloadSnapshot(Base):
    __tablename__ = "workload_snapshots"

    id              = Column(Integer, primary_key=True, index=True)
    company_id      = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id     = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    date            = Column(Date, nullable=False)
    active_tasks    = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    created_at      = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("employee_id", "date", name="uq_workload_snapshot"),)


# ═══════════════════════════════════════════════════════════════
# RAG — KNOWLEDGE EMBEDDINGS (semantic memory for the digital twin)
# Tenant-scoped: every row carries company_id and retrieval filters on it,
# so this is correct the day the system goes multi-tenant. The vector lives
# in a JSON column and similarity is computed in-process (see rag.py) — no
# pgvector dependency; swap the search internals for a vector DB later.
# ═══════════════════════════════════════════════════════════════

class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"

    id          = Column(Integer, primary_key=True, index=True)
    company_id  = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False)   # uploaded_file / task / message / meeting / goal
    source_id   = Column(Integer, nullable=True)       # PK of the source row (NULL for ad-hoc text)
    chunk_index = Column(Integer, default=0)
    content     = Column(Text, nullable=False)         # the chunk text, returned to the agent verbatim
    embedding   = Column(JSON, nullable=False)         # list[float], length = EMBED_DIM
    embed_model = Column(String(100), nullable=False)  # model that produced it — guards against dim mismatch
    meta        = Column(JSON, nullable=True)          # title, filename, owner, etc.
    created_at  = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_knowledge_company_source", "company_id", "source_type"),
        UniqueConstraint("company_id", "source_type", "source_id", "chunk_index",
                         name="uq_knowledge_chunk"),
    )


# ═══════════════════════════════════════════════════════════════
# CONTRACTS — the interface promise between two people's work
# A contract says "the PRODUCER task gives the CONSUMER task X, in this shape."
# baseline_at captures the producer's state when the contract was agreed; if the
# producer changes after that, the contract is at risk of drift (the seed of
# real integration-risk detection — see project_digest.run_drift_alerts).
# ═══════════════════════════════════════════════════════════════

class Contract(Base):
    __tablename__ = "contracts"

    id               = Column(Integer, primary_key=True, index=True)
    company_id       = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id       = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    producer_task_id = Column(Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    consumer_task_id = Column(Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    name             = Column(String(300), nullable=False)   # "auth API response shape"
    description      = Column(Text, default="")              # the promise / interface details
    status           = Column(String(50), default="active")  # active / at_risk / broken / fulfilled
    baseline_at      = Column(DateTime(timezone=True), default=_now)  # producer state captured here
    # The producer's CONTENT (title/description/due) at baseline — what semantic
    # drift detection diffs against, so a typo fix no longer flags the contract.
    baseline_snapshot = Column(Text, nullable=True)
    created_by       = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    created_at       = Column(DateTime(timezone=True), default=_now)
    updated_at       = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    producer = relationship("Task", foreign_keys=[producer_task_id])
    consumer = relationship("Task", foreign_keys=[consumer_task_id])

    __table_args__ = (
        Index("ix_contracts_company_status", "company_id", "status"),
    )


class MCPConnection(Base):
    """A connected enterprise app / data source exposed over MCP (Model Context
    Protocol). Company-scoped; the auth token is Fernet-encrypted at rest. These
    are fed into the orchestrator's Claude calls so the AI can read real artifacts
    (code, schema, issues) instead of only task text."""
    __tablename__ = "mcp_connections"

    id             = Column(Integer, primary_key=True, index=True)
    company_id     = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    app            = Column(String(80), nullable=False)    # catalog key: "github", "notion", "custom", ...
    label          = Column(String(120), nullable=False)   # display name
    url            = Column(String(500), nullable=False)   # the MCP server URL (must be publicly reachable)
    auth_token_enc = Column(Text, nullable=True)           # Fernet-encrypted token (never returned to clients)
    enabled        = Column(Boolean, default=True)
    # ── One-click OAuth (MCP authorization spec) ──────────────────
    # auth_type "oauth" rows hold a refreshable token obtained via the MCP
    # OAuth flow (DCR + PKCE); "token" rows are legacy pasted API keys.
    auth_type              = Column(String(20), default="token")
    refresh_token_enc      = Column(Text, nullable=True)
    token_expires_at       = Column(DateTime(timezone=True), nullable=True)
    oauth_client_id        = Column(String(300), nullable=True)
    oauth_client_secret_enc = Column(Text, nullable=True)
    oauth_token_endpoint   = Column(String(500), nullable=True)
    # NULL owner = company-shared (legacy). OAuth connections are PER-USER —
    # the token represents that person's consent at the provider.
    owner_id       = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True)
    # Consecutive attach/refresh failures. Incremented when this connector
    # breaks an AI call or a token refresh; reset on success. At the disable
    # threshold the connector is turned off and its owner notified — one dead
    # connector must not keep taxing every command with a failed attach.
    fail_count     = Column(Integer, default=0)
    created_at     = Column(DateTime(timezone=True), default=_now)
    updated_at     = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("company_id", "app", "owner_id", name="uq_mcp_company_app_owner"),)
