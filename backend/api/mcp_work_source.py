"""
mcp_work_source.py — read a person's work out of a connected tracker
====================================================================
A WorkSource backed by a live MCP connector, so a briefing can be built from
Jira, Linear or GitHub without anything being migrated into Nexus.

Two decisions shape this file.

**Tools are discovered, not hardcoded.** Every MCP server names things
differently — `jira_search`, `search_issues`, `list_my_issues` — and the same
vendor renames them between versions. Hardcoding a tool name produces an
integration that works the day it ships and breaks silently later, which is
the worst failure mode for something that runs unattended at 7am. Instead we
call `tools/list` and pick the best match by name and description, with
per-app hints to break ties.

**The token is the identity.** Connections authenticate per person, so
"what is assigned to me" is answerable without mapping a Nexus employee to a
Jira account id. That sidesteps an identity-reconciliation problem that would
otherwise have to be solved before the first briefing could exist — and it is
also the safer default, since the connector can only ever return what that
person is already allowed to see.
"""

from __future__ import annotations

import logging
import re

from api.mcp_client import MCPClient, MCPError
from api.work_sources import MeetingItem, WorkItem

log = logging.getLogger("nexus.mcp_work_source")

# Tool names we prefer, per app, most-specific first. Hints only — if none
# match, scoring falls back to the generic patterns below.
PREFERRED_TOOLS = {
    "jira": ["searchJiraIssuesUsingJql", "jira_search_issues", "jira_search", "search_issues"],
    "linear": ["list_my_issues", "list_issues", "search_issues"],
    "github": ["list_issues", "search_issues", "issues_list_for_authenticated_user"],
    "asana": ["search_tasks", "list_tasks"],
    "clickup": ["get_tasks", "search_tasks"],
    "monday.com": ["get_items", "search_items"],
}

# Generic scoring for unknown servers.
_WANTED = re.compile(r"\b(issue|ticket|task|work ?item)s?\b", re.I)
_ACTIONY = re.compile(r"\b(search|list|get|find|my|assigned)\b", re.I)
_UNWANTED = re.compile(r"\b(create|update|delete|comment|transition|add|remove|close|"
                       r"assign|move|archive|attach|webhook)\b", re.I)


def _words(text: str) -> str:
    """Split an identifier into space-separated words for word-boundary matching.

    `\\b` does not work on tool names: `_` is a word character, so `\\bclose\\b`
    never matches `close_issue` — and MCP tools are overwhelmingly snake_case or
    camelCase. Without this the mutating-tool guard below silently matches
    nothing, which is how a morning brief ends up calling `delete_issue`.
    """
    spaced = re.sub(r"[_\-./]+", " ", text or "")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)   # camelCase -> camel Case
    return spaced.lower()


def _score_tool(tool: dict) -> int:
    """How likely is this tool to answer 'what work is assigned to me'?"""
    name = _words(tool.get("name") or "")
    description = tool.get("description") or ""
    blob = f"{name} {description}"

    # A mutating tool must never be selected: this runs unattended, and picking
    # `delete_issue` to build a morning brief would be catastrophic rather than
    # merely wrong. Reject on the NAME only — descriptions of read tools often
    # mention the write verbs they pair with.
    if _UNWANTED.search(name):
        return -1

    score = 0
    if _WANTED.search(blob):
        score += 3
    if _ACTIONY.search(name):
        score += 2
    if re.search(r"\b(assigned|assignee|my|me|current user)\b", blob, re.I):
        score += 3
    if re.search(r"\b(jql|query|filter)\b", blob, re.I):
        score += 1
    return score


def pick_work_tool(tools: list[dict], app: str | None = None) -> dict | None:
    """Choose the tool most likely to list the caller's open work."""
    if not tools:
        return None

    by_name = {(t.get("name") or "").lower(): t for t in tools}
    for preferred in PREFERRED_TOOLS.get((app or "").lower(), []):
        hit = by_name.get(preferred.lower())
        if hit:
            return hit

    ranked = sorted(tools, key=_score_tool, reverse=True)
    best = ranked[0]
    return best if _score_tool(best) > 0 else None


# ── normalising whatever the tracker hands back ───────────────────
def _first(record: dict, *paths, default=None):
    """Read the first path that resolves. Paths are dotted: 'fields.status.name'.

    Trackers disagree about everything — Jira nests under `fields`, Linear is
    flat, GitHub uses different words again — so every field is a list of
    candidates rather than one lookup.
    """
    for path in paths:
        cursor = record
        ok = True
        for part in path.split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                ok = False
                break
        if ok and cursor not in (None, "", []):
            return cursor
    return default


def _as_date(value):
    from datetime import date, datetime

    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    return None


def _looks_done(status: str | None) -> bool:
    if not status:
        return False
    return bool(re.search(r"\b(done|closed|complete|completed|resolved|shipped|merged|cancell?ed)\b",
                          str(status), re.I))


def to_work_item(record: dict, source: str) -> WorkItem | None:
    """Map one tracker record onto the shape the engines consume."""
    if not isinstance(record, dict):
        return None

    title = _first(record, "title", "summary", "fields.summary", "name", "text")
    external_id = _first(record, "key", "identifier", "id", "number", "gid", "fields.key")
    if not title and not external_id:
        return None

    status = _first(record, "status", "state.name", "fields.status.name", "state",
                    "fields.status.statusCategory.name")
    if isinstance(status, dict):
        status = status.get("name") or status.get("id")

    assignee = _first(record, "assignee.displayName", "fields.assignee.displayName",
                      "assignee.name", "assignee.login", "assignee", "fields.assignee.name")
    if isinstance(assignee, dict):
        assignee = assignee.get("displayName") or assignee.get("name") or assignee.get("login")

    priority = _first(record, "priority.name", "fields.priority.name", "priority",
                      "priorityLabel")
    if isinstance(priority, dict):
        priority = priority.get("name")

    project = _first(record, "project.name", "fields.project.name", "project.key",
                     "team.name", "repository.name", "repository.full_name")
    if isinstance(project, dict):
        project = project.get("name") or project.get("key")

    return WorkItem(
        source=source,
        external_id=str(external_id or title)[:200],
        title=str(title or external_id),
        is_done=_looks_done(status) or bool(_first(record, "completedAt", "closed_at")),
        status=str(status) if status is not None else None,
        assignee_name=str(assignee) if assignee else None,
        due_date=_as_date(_first(record, "dueDate", "duedate", "fields.duedate",
                                 "due_on", "due_date", "targetDate")),
        priority=str(priority) if priority else None,
        project=str(project) if project else None,
        url=_first(record, "url", "html_url", "self", "permalink", "browseUrl"),
    )


def extract_records(payload) -> list[dict]:
    """Find the list of items in whatever envelope the tool returned."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("issues", "results", "items", "nodes", "tasks", "data", "values",
                "records", "workItems"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        if isinstance(value, dict):
            nested = extract_records(value)
            if nested:
                return nested
    # A single record handed back bare.
    if any(k in payload for k in ("key", "id", "identifier", "title", "summary")):
        return [payload]
    return []


class MCPWorkSource:
    """A WorkSource backed by one connected MCP server."""

    def __init__(self, connection, decrypt, timeout: int = 20):
        self._conn = connection
        self._decrypt = decrypt
        self._timeout = timeout
        self.name = (connection.label or connection.app or "mcp").lower()

    def is_available(self) -> bool:
        return bool(self._conn.enabled and self._conn.url and self._conn.auth_token_enc)

    def _token(self) -> str | None:
        try:
            return self._decrypt(self._conn.auth_token_enc)
        except Exception:
            return None

    def open_items_for(self, person_ref: str) -> list[WorkItem]:
        """Open work for the person whose credential this connection holds.

        `person_ref` is accepted for interface compatibility but not used to
        filter: the OAuth token already scopes the answer to its owner, and
        asking a tracker for someone else's work would both need an identity
        map and hand back data the caller may not be entitled to.
        """
        token = self._token()
        if not token:
            return []

        try:
            with MCPClient(self._conn.url, token, timeout=self._timeout) as client:
                tool = pick_work_tool(client.list_tools(), self._conn.app)
                if not tool:
                    log.info("no work-listing tool found on %s", self.name)
                    return []
                result = client.call_tool(tool["name"], self._arguments_for(tool))
                records = extract_records(MCPClient.data_of(result))
        except MCPError as e:
            log.warning("%s unavailable: %s", self.name, e)
            return []
        except Exception as e:                      # network, TLS, parsing …
            log.warning("%s failed: %s: %s", self.name, type(e).__name__, e)
            return []

        items = [to_work_item(r, self.name) for r in records]
        return [i for i in items if i and not i.is_done]

    def _arguments_for(self, tool: dict) -> dict:
        """Best-effort arguments, built from the tool's declared schema.

        Only fills parameters the tool actually advertises — sending unknown
        keys makes strict servers reject the call outright.
        """
        schema = (tool.get("inputSchema") or {}).get("properties") or {}
        args: dict = {}

        if "jql" in schema:
            args["jql"] = "assignee = currentUser() AND resolution = Unresolved ORDER BY duedate ASC"
        for key in ("assignee", "assignedTo", "assignee_id"):
            if key in schema and "jql" not in args:
                args[key] = "me"
                break
        for key in ("filter", "state", "status"):
            if key in schema and key not in args:
                args[key] = "open"
                break
        for key in ("maxResults", "limit", "per_page", "first", "pageSize"):
            if key in schema:
                args[key] = 50
                break
        return args

    def meetings_today(self, person_ref: str) -> list[MeetingItem]:
        # Trackers don't hold meetings; calendar sources will implement this.
        return []


def mcp_sources_for(db, company_id: int = 1, employee_id: int | None = None) -> list[MCPWorkSource]:
    """Every enabled connector this person may read through."""
    try:
        from database.models import MCPConnection
        from api.token_crypto import decrypt_secret
    except Exception:
        return []

    try:
        rows = db.query(MCPConnection).filter(
            MCPConnection.company_id == company_id,
            MCPConnection.enabled == True,          # noqa: E712
        ).all()
    except Exception:
        return []

    # Same visibility rule as the orchestrator: a per-user connection belongs
    # to whoever consented; an ownerless one is company-shared.
    mine = [c for c in rows
            if c.owner_id is None or employee_id is None or c.owner_id == employee_id]
    return [MCPWorkSource(c, decrypt_secret) for c in mine]
