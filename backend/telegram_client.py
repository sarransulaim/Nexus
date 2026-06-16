"""
telegram_client.py — Telegram integration (graceful stub)
=========================================================
Telegram is not wired up for the pilot. This stub exists so the imports in
channels_router / autonomous_briefings / proactive_engine resolve and those
code paths DEGRADE GRACEFULLY ("not configured") instead of crashing with
ImportError → 500 on every Telegram webhook/link request.

Replace with a real implementation (mirroring twilio_client) when Telegram is
needed. The public surface below is exactly what the callers use:
  is_configured(), send_message(), send_verification_code(),
  verify_webhook_secret(), set_webhook()
"""
import os

BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")


def is_configured() -> bool:
    return bool(BOT_TOKEN)


def send_message(chat_id, text):
    """No-op until Telegram is implemented. Returns a result dict, never raises."""
    if not is_configured():
        return {"ok": False, "error": "Telegram not configured"}
    return {"ok": False, "error": "Telegram send not implemented"}


def send_verification_code(identifier, code, employee_name=None):
    return send_message(identifier, f"Your Nexus verification code is {code}.")


def verify_webhook_secret(header_secret) -> bool:
    """Reject inbound webhooks unless a secret is configured AND matches —
    fail closed, never trust an unconfigured Telegram webhook."""
    return bool(WEBHOOK_SECRET) and header_secret == WEBHOOK_SECRET


def set_webhook():
    return {"ok": False, "error": "Telegram not configured"}
