"""
connector_catalog.py — what Nexus can be connected to, and what it is connected to
=================================================================================
The agent used to have no idea any of this existed. Asked "how many apps can
you connect with", it had nothing in its prompt about integrations, so it
improvised from the Gmail/Calendar tools it could see and answered "three" —
then told the user Jira "isn't integrated yet" when Jira is in the catalogue
and two clicks away on the Connections page. An assistant that talks a user
out of a capability the product has is worse than one that stays quiet.

Two different facts have to reach the prompt, and they belong in different
places:

  * WHAT CAN BE CONNECTED is constant, so it goes in the cached static block.
  * WHAT IS CONNECTED RIGHT NOW changes per company and per person, so it goes
    in the uncached dynamic block alongside the rest of the context snapshot.
"""

# Mirrors the catalogue the user actually sees in ConnectionsPage.jsx. The
# frontend is the source of truth for what is clickable; this is the agent's
# copy of it, and a test asserts the two lists stay in step — a stale copy here
# means the AI misinforms people about the product, which is exactly the bug
# this module exists to fix.
CATALOG = [
    "Linear", "Jira / Confluence", "Asana", "Monday.com", "ClickUp",
    "Slack (search & history)", "GitHub", "Sentry", "Notion", "Figma",
    "Canva", "Box", "HubSpot", "Intercom", "Stripe", "PayPal", "Square",
    "Zapier", "Postgres", "Custom MCP server",
]

# Built in, always available — not MCP connectors.
NATIVE_CHANNELS = [
    "Slack — read channels, post messages, DM people",
    "Gmail — read the inbox, draft replies, queue sends for approval",
    "Google Calendar — read events, schedule meetings, create Meet links",
]


def static_capability_note() -> str:
    """Constant text for the cached part of the system prompt."""
    return (
        "\n\nWHAT YOU CAN BE CONNECTED TO\n"
        "Built in and always available:\n"
        + "\n".join(f"  - {c}" for c in NATIVE_CHANNELS)
        + "\n\nBeyond those, Nexus connects to outside apps through MCP. "
        f"{len(CATALOG)} are in the catalogue: "
        + ", ".join(CATALOG) + ".\n"
        "A manager connects them under Connections -> 'Apps & data', and most use "
        "OAuth so each person connects their own account and you see exactly what "
        "that person can see. When one is connected its tools appear alongside "
        "your own and you can use them directly.\n"
        "So when someone asks whether you work with a particular app: if it is in "
        "that list, the answer is that it is supported and takes a moment to "
        "connect — NOT that it isn't integrated. Only say something isn't "
        "supported if it genuinely isn't in the list."
    )


def current_connections_note(company_id: int = 1, employee_id: int = None) -> str:
    """Live text for the uncached part of the prompt: what is actually wired up.

    Best-effort — a failure here must never block a turn, so it returns an
    empty string rather than raising.
    """
    try:
        from database.core import SessionLocal
        from database.models import MCPConnection, OAuthToken

        db = SessionLocal()
        try:
            q = db.query(MCPConnection).filter(
                MCPConnection.company_id == company_id,
                MCPConnection.enabled == True,          # noqa: E712
            )
            # A per-user connection belongs to the person who consented; a row
            # with no owner is the company-shared one.
            rows = [c for c in q.all()
                    if c.owner_id is None or employee_id is None
                    or c.owner_id == employee_id]
            connected = sorted({(c.label or c.app) for c in rows})

            google = False
            if employee_id is not None:
                google = db.query(OAuthToken).filter(
                    OAuthToken.employee_id == employee_id,
                    OAuthToken.provider == "google",
                ).first() is not None
        finally:
            db.close()

        lines = []
        if connected:
            lines.append("Connected apps you can use right now: " + ", ".join(connected) + ".")
        else:
            lines.append(
                "No outside apps are connected right now. If someone asks about one "
                "from the catalogue, say it's supported and point them at "
                "Connections -> 'Apps & data' — don't say it isn't integrated."
            )
        if employee_id is not None:
            lines.append("Google account: " + ("connected." if google else
                         "not connected, so Gmail and Calendar actions won't work until it is."))
        return "\n" + "\n".join(lines)
    except Exception:
        return ""
