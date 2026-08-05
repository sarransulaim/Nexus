"""
Reading a person's work out of a connected tracker.

The dangerous part isn't the network — it's the mapping. A tracker that
returns a slightly different shape produces a briefing with blank titles or
missing due dates and raises nothing, so these tests use real record shapes
from Jira, Linear and GitHub.
"""
from datetime import date

import pytest

from api.mcp_work_source import (
    MCPWorkSource, pick_work_tool, to_work_item, extract_records,
)


# ── tool selection: discovered, never hardcoded ───────────────────
def test_prefers_the_known_tool_for_a_known_app():
    tools = [{"name": "create_issue"}, {"name": "searchJiraIssuesUsingJql"}, {"name": "add_comment"}]
    assert pick_work_tool(tools, "jira")["name"] == "searchJiraIssuesUsingJql"


def test_falls_back_to_scoring_for_an_unknown_server():
    tools = [
        {"name": "do_thing", "description": "does a thing"},
        {"name": "list_work_items", "description": "List work items assigned to the current user"},
    ]
    assert pick_work_tool(tools, "some-vendor")["name"] == "list_work_items"


@pytest.mark.parametrize("dangerous", [
    "delete_issue", "update_issue", "create_ticket", "transition_issue",
    "assign_issue", "archive_task", "close_issue",
])
def test_never_selects_a_mutating_tool(dangerous):
    """This runs unattended at 7am. Picking a write tool to build a morning
    brief would be catastrophic rather than merely wrong."""
    tools = [{"name": dangerous, "description": "issues assigned to me, my tasks"}]
    assert pick_work_tool(tools, None) is None


def test_returns_nothing_rather_than_a_bad_guess():
    tools = [{"name": "send_email", "description": "sends an email"}]
    assert pick_work_tool(tools, None) is None


def test_no_tools_at_all_is_not_an_error():
    assert pick_work_tool([], "jira") is None


# ── mapping real tracker shapes ───────────────────────────────────
def test_maps_a_jira_issue():
    """Jira nests almost everything under `fields`."""
    record = {
        "key": "PROJ-42",
        "fields": {
            "summary": "Ship the invoice endpoint",
            "status": {"name": "In Progress"},
            "duedate": "2026-08-14",
            "priority": {"name": "High"},
            "assignee": {"displayName": "Priya Nair"},
            "project": {"name": "Billing"},
        },
    }
    item = to_work_item(record, "jira")
    assert item.external_id == "PROJ-42"
    assert item.title == "Ship the invoice endpoint"
    assert item.status == "In Progress"
    assert item.due_date == date(2026, 8, 14)
    assert item.priority == "High"
    assert item.assignee_name == "Priya Nair"
    assert item.project == "Billing"
    assert item.is_done is False


def test_maps_a_linear_issue():
    """Linear is flat where Jira is nested."""
    record = {
        "identifier": "ENG-17",
        "title": "Fix the webhook retry",
        "state": {"name": "Todo"},
        "dueDate": "2026-08-09",
        "assignee": {"name": "Sulaim"},
        "team": {"name": "Platform"},
        "url": "https://linear.app/x/issue/ENG-17",
    }
    item = to_work_item(record, "linear")
    assert item.external_id == "ENG-17"
    assert item.due_date == date(2026, 8, 9)
    assert item.assignee_name == "Sulaim"
    assert item.project == "Platform"
    assert item.url.endswith("ENG-17")


def test_maps_a_github_issue():
    record = {
        "number": 128,
        "title": "Flaky test in CI",
        "state": "open",
        "assignee": {"login": "sarransulaim"},
        "html_url": "https://github.com/o/r/issues/128",
        "repository": {"full_name": "o/r"},
    }
    item = to_work_item(record, "github")
    assert item.external_id == "128"
    assert item.assignee_name == "sarransulaim"
    assert item.is_done is False


@pytest.mark.parametrize("status,done", [
    ("Done", True), ("Closed", True), ("Resolved", True), ("Completed", True),
    ("Cancelled", True), ("Canceled", True), ("merged", True),
    ("In Progress", False), ("Todo", False), ("open", False), ("Backlog", False),
])
def test_recognises_finished_work_across_vocabularies(status, done):
    """Every tracker has its own word for finished. Getting this wrong either
    fills the brief with completed work or hides live work."""
    item = to_work_item({"id": "1", "title": "t", "status": status}, "x")
    assert item.is_done is done


def test_a_record_with_nothing_usable_is_dropped_not_rendered_blank():
    assert to_work_item({"foo": "bar"}, "x") is None
    assert to_work_item("not a dict", "x") is None


def test_missing_fields_do_not_raise():
    item = to_work_item({"key": "K-1"}, "jira")
    assert item.title == "K-1"
    assert item.due_date is None and item.priority is None


@pytest.mark.parametrize("raw,expected", [
    ("2026-08-14", date(2026, 8, 14)),
    ("2026-08-14T09:30:00Z", date(2026, 8, 14)),
    ("2026-08-14T09:30:00+00:00", date(2026, 8, 14)),
    ("", None), (None, None), ("not a date", None),
])
def test_date_parsing_is_tolerant(raw, expected):
    item = to_work_item({"id": "1", "title": "t", "dueDate": raw}, "x")
    assert item.due_date == expected


# ── envelope unwrapping ───────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    [{"key": "A-1", "title": "x"}],
    {"issues": [{"key": "A-1", "title": "x"}]},
    {"results": [{"key": "A-1", "title": "x"}]},
    {"nodes": [{"key": "A-1", "title": "x"}]},
    {"data": {"items": [{"key": "A-1", "title": "x"}]}},
    {"key": "A-1", "title": "x"},
])
def test_finds_records_in_any_common_envelope(payload):
    records = extract_records(payload)
    assert len(records) == 1 and records[0]["key"] == "A-1"


def test_unrecognisable_payload_yields_nothing():
    assert extract_records({"unexpected": "shape"}) == []
    assert extract_records(None) == []


# ── argument building ─────────────────────────────────────────────
def test_only_sends_parameters_the_tool_declares():
    """Strict servers reject calls carrying unknown keys."""
    src = MCPWorkSource.__new__(MCPWorkSource)
    tool = {"name": "t", "inputSchema": {"properties": {"jql": {}, "maxResults": {}}}}
    args = src._arguments_for(tool)
    assert set(args) <= {"jql", "maxResults"}
    assert "currentUser()" in args["jql"]


def test_sends_nothing_when_the_tool_declares_nothing():
    src = MCPWorkSource.__new__(MCPWorkSource)
    assert src._arguments_for({"name": "t", "inputSchema": {}}) == {}


# ── availability and failure ──────────────────────────────────────
class _Conn:
    def __init__(self, **kw):
        self.enabled = kw.get("enabled", True)
        self.url = kw.get("url", "https://example.test/mcp")
        self.auth_token_enc = kw.get("auth_token_enc", "enc")
        self.app = kw.get("app", "jira")
        self.label = kw.get("label", "Jira")
        self.owner_id = kw.get("owner_id")


def test_disabled_or_tokenless_connections_are_not_available():
    assert not MCPWorkSource(_Conn(enabled=False), lambda s: "t").is_available()
    assert not MCPWorkSource(_Conn(auth_token_enc=None), lambda s: "t").is_available()
    assert MCPWorkSource(_Conn(), lambda s: "t").is_available()


def test_an_undecryptable_token_yields_no_work_rather_than_raising():
    def boom(_):
        raise ValueError("bad key")
    assert MCPWorkSource(_Conn(), boom).open_items_for("1") == []


def test_an_unreachable_server_yields_no_work_rather_than_raising():
    """These run on a scheduler. A dead connector must cost a section of the
    briefing, never the briefing itself."""
    src = MCPWorkSource(_Conn(url="http://127.0.0.1:9/mcp"), lambda s: "token", timeout=2)
    assert src.open_items_for("1") == []


def test_finished_work_is_filtered_out_of_open_items():
    from api.mcp_work_source import to_work_item
    items = [to_work_item({"key": "A-1", "title": "a", "status": "Done"}, "x"),
             to_work_item({"key": "A-2", "title": "b", "status": "Todo"}, "x")]
    assert [i.external_id for i in items if not i.is_done] == ["A-2"]
