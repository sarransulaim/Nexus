"""
channel_delivery.py — one way to reach a person, wherever they are.
===================================================================
Slack is the single messaging channel (WhatsApp and Telegram were removed
2026-07-09: the WhatsApp sandbox needed a re-join every 72h and Telegram was
never more than a stub). Every outbound "tap on the shoulder" — morning
briefings, proactive alerts — goes through here, so there is exactly one
place that knows how to reach somebody.

Best-effort by design: a send failure returns a status string and never
breaks the caller (a briefing still lands in-app if Slack is down).
"""
import logging

from database.models import ChannelConnection, Employee

log = logging.getLogger("nexus.delivery")


def linked_channels(employee: Employee, db) -> list:
    """Verified, active channels for this employee, primary first."""
    conns = db.query(ChannelConnection).filter(
        ChannelConnection.employee_id == employee.id,
        ChannelConnection.platform == "slack",
        ChannelConnection.verified == True,        # noqa: E712
        ChannelConnection.is_active == True,       # noqa: E712
    ).all()
    if not conns:
        return []
    return sorted(conns, key=lambda c: (not c.is_primary,))


def _send_one(conn: ChannelConnection, text: str) -> tuple[bool, str]:
    """Deliver on one channel. Returns (ok, status)."""
    try:
        import slack_bot as sb
        res = sb.send_dm(conn.platform_user_id, text)
        return (bool(res.get("success")), "sent_slack" if res.get("success")
                else f"slack_failed:{res.get('error', '')[:40]}")
    except Exception as e:
        log.warning(f"Slack delivery failed for employee {conn.employee_id}: {e}")
        return (False, "slack_error")


def deliver(employee: Employee, text: str, db, all_channels: bool = False) -> str:
    """Send `text` to this employee on Slack. Returns a status string
    ("sent_slack", "no_channel", ...). Never raises."""
    conns = linked_channels(employee, db)
    if not conns:
        return "no_channel"
    statuses = []
    for c in conns:
        ok, status = _send_one(c, text)
        statuses.append(status)
        if ok and not all_channels:
            return status
    return ";".join(statuses) if statuses else "no_channel"
