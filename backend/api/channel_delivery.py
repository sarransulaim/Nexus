"""
channel_delivery.py — one way to reach a person, wherever they are.
===================================================================
Before this, briefings and proactive alerts each had their own dispatch that
knew only WhatsApp and Telegram — so anyone who linked **Slack** (the channel
that actually works: WhatsApp is sandbox-limited and Telegram is a stub) got
nothing at all. Every outbound "tap on the shoulder" now goes through here.

Selection order: the employee's `is_primary` channel if set, otherwise the
cheapest reliable one — Slack DM (free, instant, where the team already is),
then WhatsApp, then Telegram.

Every send is best-effort and returns a status string; delivery must never
break the caller (a briefing still lands in-app if the channel is down).
"""
import logging
import os

from database.models import ChannelConnection, Employee

log = logging.getLogger("nexus.delivery")

# Slack DMs are free and expected; WhatsApp/Telegram cost money or are noisy,
# so those stay behind the existing opt-in flag.
PUSH_NON_SLACK = os.getenv("PROACTIVE_PUSH_CHANNEL", "0") == "1"

PRIORITY = ["slack", "whatsapp", "telegram"]


def linked_channels(employee: Employee, db) -> list:
    """Verified, active channels for this employee, best first."""
    conns = db.query(ChannelConnection).filter(
        ChannelConnection.employee_id == employee.id,
        ChannelConnection.verified == True,        # noqa: E712
        ChannelConnection.is_active == True,       # noqa: E712
    ).all()
    if not conns:
        return []
    primary = [c for c in conns if c.is_primary]
    rest = sorted(
        (c for c in conns if not c.is_primary),
        key=lambda c: PRIORITY.index(c.platform) if c.platform in PRIORITY else 99,
    )
    return primary + rest


def _send_one(conn: ChannelConnection, text: str) -> tuple[bool, str]:
    """Deliver on one channel. Returns (ok, status)."""
    platform = (conn.platform or "").lower()
    try:
        if platform == "slack":
            import slack_bot as sb
            res = sb.send_dm(conn.platform_user_id, text)
            return (bool(res.get("success")), "sent_slack" if res.get("success")
                    else f"slack_failed:{res.get('error', '')[:40]}")
        if platform == "whatsapp":
            if not PUSH_NON_SLACK:
                return (False, "whatsapp_skipped_opt_in")
            import twilio_client as tw
            if not tw.is_configured():
                return (False, "whatsapp_not_configured")
            res = tw.send_whatsapp(conn.platform_user_id, text)
            return (bool(res.get("success")), "sent_whatsapp" if res.get("success")
                    else f"whatsapp_failed:{res.get('error', '')[:40]}")
        if platform == "telegram":
            if not PUSH_NON_SLACK:
                return (False, "telegram_skipped_opt_in")
            import telegram_client as tg
            if not tg.is_configured():
                return (False, "telegram_not_configured")
            res = tg.send_message(conn.platform_user_id, text)
            return (bool(res.get("success")), "sent_telegram" if res.get("success")
                    else "telegram_failed")
    except Exception as e:
        log.warning(f"delivery on {platform} failed for employee {conn.employee_id}: {e}")
        return (False, f"{platform}_error")
    return (False, f"unsupported:{platform}")


def deliver(employee: Employee, text: str, db, all_channels: bool = False) -> str:
    """Send `text` to this employee on their best linked channel.

    Returns a status string ("sent_slack", "no_channel", ...). Best-effort:
    tries the next channel if the preferred one fails, so a dead WhatsApp
    sandbox can't swallow an alert when Slack is also linked.
    """
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
