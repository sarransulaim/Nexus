"""
The agent's knowledge of what it can connect to.

This exists because of a live failure: asked "how many apps can you connect
with", the assistant answered "three" and told the user Jira wasn't
integrated — while Jira sat in the catalogue on the Connections page. The
prompt simply had nothing about integrations, so the model improvised.

A stale copy of the catalogue reproduces exactly that bug, so the first test
pins the backend list to the one users actually see.
"""
import pathlib
import re

import pytest

from api.connector_catalog import (
    CATALOG, NATIVE_CHANNELS, static_capability_note, current_connections_note,
)


def _frontend_catalog():
    f = (pathlib.Path(__file__).resolve().parent.parent.parent
         / "frontend" / "src" / "pages" / "ConnectionsPage.jsx")
    if not f.exists():
        pytest.skip("frontend source not available")
    return re.findall(r"label:\s*'([^']+)'", f.read_text(encoding="utf-8"))


def test_backend_catalog_matches_what_users_can_actually_click():
    """The frontend is the source of truth for what is connectable. If this
    drifts, the AI tells people an app isn't supported when it is — which is
    the bug this module was written to fix."""
    frontend = _frontend_catalog()
    assert frontend, "found no connectors in ConnectionsPage.jsx — has the shape changed?"
    missing = [c for c in frontend if c not in CATALOG]
    extra = [c for c in CATALOG if c not in frontend]
    assert not missing and not extra, (
        f"catalogue drift — the AI would misdescribe the product.\n"
        f"  in the UI but not in the agent's list: {missing}\n"
        f"  in the agent's list but not in the UI: {extra}"
    )


def test_the_note_names_the_apps_people_actually_ask_about():
    note = static_capability_note()
    for app in ("Jira", "Linear", "Notion", "GitHub", "Asana"):
        assert app in note, f"{app} missing from the capability note"
    assert str(len(CATALOG)) in note, "the note doesn't state how many connectors exist"


def test_the_note_tells_the_agent_where_to_send_people():
    note = static_capability_note()
    assert "Connections" in note and "Apps & data" in note


def test_the_note_forbids_the_exact_wrong_answer():
    """The failure mode was the assistant saying an app 'isn't integrated'
    when it is in the catalogue."""
    note = static_capability_note().lower()
    assert "isn't integrated" in note or "not that it isn" in note, \
        "nothing steers the agent away from the answer that caused this"


def test_native_channels_are_listed_and_current():
    note = static_capability_note()
    for channel in ("Slack", "Gmail", "Google Calendar"):
        assert channel in note
    # WhatsApp and Telegram were removed from the product — the agent must not
    # offer them either.
    assert "WhatsApp" not in note and "Telegram" not in note


def test_live_connection_note_is_honest_when_nothing_is_connected():
    note = current_connections_note(company_id=1, employee_id=None)
    assert note.strip(), "no live connection note produced"
    lowered = note.lower()
    assert ("connected apps you can use right now" in lowered
            or "no outside apps are connected" in lowered)


def test_live_connection_note_never_raises():
    """Best-effort context must not be able to break a turn."""
    assert isinstance(current_connections_note(company_id=999_999), str)
