"""
twilio_client.py — Twilio Wrapper for WhatsApp
================================================
Thin wrapper around the Twilio Python SDK.

Handles:
  - Sending WhatsApp messages
  - Verifying webhook signatures (security)
  - Normalizing phone numbers (whatsapp:+12345 → +12345)

Environment variables required:
  TWILIO_ACCOUNT_SID   — starts with AC...
  TWILIO_AUTH_TOKEN    — 32 char string
  TWILIO_WHATSAPP_FROM — default: whatsapp:+14155238886 (sandbox)
  TWILIO_WEBHOOK_BASE  — your ngrok URL (e.g. https://abc.ngrok-free.app)
"""

import os
import logging
from typing import Optional

log = logging.getLogger("nexus.twilio")

# ── Settings ──────────────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
TWILIO_WEBHOOK_BASE  = os.getenv("TWILIO_WEBHOOK_BASE", "")   # ngrok URL
# Hard cap on every outbound Twilio HTTP call so a stalled request can't hang the
# calling thread (background sender / webhook reply path) forever (#17).
_HTTP_TIMEOUT        = int(os.getenv("TWILIO_HTTP_TIMEOUT", "10"))


# Lazy-init the client so the app doesn't crash if Twilio isn't configured yet
_client = None
_validator = None


def _get_client():
    global _client
    if _client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            raise RuntimeError(
                "Twilio credentials not configured. Set TWILIO_ACCOUNT_SID and "
                "TWILIO_AUTH_TOKEN in your .env file."
            )
        from twilio.rest import Client
        try:
            from twilio.http.http_client import TwilioHttpClient
            # Default timeout for all Twilio requests. No auto-retry: message
            # sends are non-idempotent, and a blind retry could double-send.
            http_client = TwilioHttpClient(timeout=_HTTP_TIMEOUT)
            _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, http_client=http_client)
        except Exception as e:
            # Older SDK without TwilioHttpClient — fall back to the plain client.
            log.warning(f"Twilio timeout client unavailable ({e}); using default client.")
            _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _client


def _get_validator():
    global _validator
    if _validator is None:
        from twilio.request_validator import RequestValidator
        _validator = RequestValidator(TWILIO_AUTH_TOKEN)
    return _validator


# ═══════════════════════════════════════════════════════════════
# PHONE NUMBER HELPERS
# ═══════════════════════════════════════════════════════════════

def normalize_phone(raw: str) -> str:
    """
    Convert any phone format to E.164 standard.

    Examples:
      'whatsapp:+12602469163' → '+12602469163'
      '+12602469163'          → '+12602469163'
      '+1 (260) 246-9163'     → '+12602469163'
      '12602469163'           → '+12602469163'
    """
    if not raw:
        return ""

    # Strip whatsapp: prefix
    if raw.startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):]

    # Strip whitespace, dashes, parens
    cleaned = "".join(c for c in raw if c.isdigit() or c == "+")

    # Ensure leading +
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    return cleaned


def to_whatsapp_format(phone: str) -> str:
    """Add the 'whatsapp:' prefix Twilio requires for sending."""
    normalized = normalize_phone(phone)
    if not normalized:
        return ""
    return f"whatsapp:{normalized}"


# ═══════════════════════════════════════════════════════════════
# SEND MESSAGE
# ═══════════════════════════════════════════════════════════════

def send_whatsapp(to_phone: str, body: str) -> dict:
    """
    Send a WhatsApp message via Twilio.

    Args:
      to_phone: phone number (any format, will be normalized)
      body: message text (max ~1600 chars; longer ones are truncated)

    Returns:
      {'success': True, 'sid': 'SM...'} on success
      {'success': False, 'error': '...'} on failure
    """
    if not body:
        return {"success": False, "error": "empty body"}

    # WhatsApp max is around 4096 chars but keep it sane
    if len(body) > 1500:
        body = body[:1497] + "..."

    to = to_whatsapp_format(to_phone)
    if not to:
        return {"success": False, "error": "invalid phone number"}

    try:
        client = _get_client()
        message = client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=to,
            body=body,
        )
        # WhatsApp failures are often ASYNC: Twilio ACCEPTS the message and it
        # fails a moment later (e.g. 63015 = recipient hasn't joined the
        # sandbox). Without this check the caller reports "sent" while nothing
        # ever arrives. Poll briefly for a terminal status so failures surface.
        import time as _time
        status, err_code = message.status, None
        try:
            for _ in range(2):
                if status in ("failed", "undelivered", "sent", "delivered", "read"):
                    break
                _time.sleep(1.5)
                m2 = client.messages(message.sid).fetch()
                status, err_code = m2.status, m2.error_code
        except Exception:
            pass  # status polling is best-effort — never turn a sent message into an error
        if status in ("failed", "undelivered"):
            if err_code == 63015:
                return {"success": False, "error": (
                    "this number hasn't joined the Twilio WhatsApp sandbox (or the join "
                    "expired — it lapses after 72h of inactivity). From WhatsApp, send "
                    "'join <your-sandbox-phrase>' to +1 415 523 8886, then try again.")}
            return {"success": False, "error": f"message {status} (Twilio error {err_code})"}
        log.info(f"WhatsApp sent to {to}: SID={message.sid} status={status}")
        return {"success": True, "sid": message.sid, "status": status}
    except Exception as e:
        log.error(f"WhatsApp send failed to {to}: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# WEBHOOK SIGNATURE VERIFICATION
# ═══════════════════════════════════════════════════════════════

def verify_signature(url: str, params: dict, signature: str) -> bool:
    """
    Verify that an incoming webhook is really from Twilio.

    Args:
      url: the full URL Twilio POSTed to (use TWILIO_WEBHOOK_BASE + path)
      params: the form data from the POST
      signature: value of the X-Twilio-Signature header

    Returns:
      True if signature is valid, False otherwise.

    In dev mode (no TWILIO_AUTH_TOKEN set), returns True to allow testing.
    """
    if not TWILIO_AUTH_TOKEN:
        # Fail CLOSED: with no auth token we cannot verify the sender, so reject
        # rather than trust a spoofable inbound webhook (which would drive the AI
        # as a real employee). Set TWILIO_ALLOW_UNVERIFIED=1 ONLY for local tests.
        if os.getenv("TWILIO_ALLOW_UNVERIFIED") == "1":
            log.warning("⚠️  Twilio signature check bypassed (TWILIO_ALLOW_UNVERIFIED=1) — dev only")
            return True
        log.error("Twilio signature check failing closed: TWILIO_AUTH_TOKEN not set — rejecting webhook")
        return False

    if not signature:
        log.warning("Missing X-Twilio-Signature header")
        return False

    try:
        validator = _get_validator()
        return validator.validate(url, params, signature)
    except Exception as e:
        log.error(f"Signature validation error: {e}")
        return False


def verify_signature_multi(urls: list, params: dict, signature: str) -> bool:
    """
    Like verify_signature but tries several candidate public URLs, accepting if
    ANY validates.

    Twilio signs the EXACT public URL it POSTed to. Behind ngrok / a reverse
    proxy that URL can differ from both what the app sees (request.url is the
    internal address) and a possibly-stale TWILIO_WEBHOOK_BASE — so a single
    reconstructed URL silently rejects every legitimate webhook (#16). Checking
    multiple candidates is safe: each still needs a signature that matches the
    HMAC of (url, params) under the secret auth token, which an attacker cannot
    forge, so accepting extra candidate URLs never weakens verification.
    """
    if not TWILIO_AUTH_TOKEN:
        if os.getenv("TWILIO_ALLOW_UNVERIFIED") == "1":
            log.warning("⚠️  Twilio signature check bypassed (TWILIO_ALLOW_UNVERIFIED=1) — dev only")
            return True
        log.error("Twilio signature check failing closed: TWILIO_AUTH_TOKEN not set — rejecting webhook")
        return False

    if not signature:
        log.warning("Missing X-Twilio-Signature header")
        return False

    try:
        validator = _get_validator()
    except Exception as e:
        log.error(f"Signature validator init error: {e}")
        return False

    tried = []
    for u in urls:
        if not u or u in tried:
            continue
        tried.append(u)
        try:
            if validator.validate(u, params, signature):
                return True
        except Exception as e:
            log.error(f"Signature validation error for {u}: {e}")
    log.warning(f"Twilio signature did not match any candidate URL: {tried}")
    return False


def build_webhook_url(path: str) -> str:
    """
    Build the public URL Twilio will POST to.

    In dev: TWILIO_WEBHOOK_BASE=https://abc.ngrok-free.app
    Returns: https://abc.ngrok-free.app/api/v1/channels/whatsapp/inbound
    """
    if not TWILIO_WEBHOOK_BASE:
        return path
    base = TWILIO_WEBHOOK_BASE.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


# ═══════════════════════════════════════════════════════════════
# VERIFICATION CODES
# ═══════════════════════════════════════════════════════════════

import secrets
import string


def generate_verification_code(length: int = 6) -> str:
    """Generate a numeric verification code for phone linking."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_verification_code(to_phone: str, code: str, employee_name: str = "") -> dict:
    """Send a verification code via WhatsApp."""
    name_part = f", {employee_name}" if employee_name else ""
    body = (
        f"Welcome to Nexus{name_part}.\n\n"
        f"Your verification code is: {code}\n\n"
        f"Enter this in Nexus to link your phone.\n"
        f"Code expires in 10 minutes."
    )
    return send_whatsapp(to_phone, body)


# ═══════════════════════════════════════════════════════════════
# HEALTH / CONFIG CHECK
# ═══════════════════════════════════════════════════════════════

def is_configured() -> bool:
    """Quick check from other modules — is Twilio ready to use?"""
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)


def config_summary() -> dict:
    """For admin/health endpoint."""
    return {
        "configured":    is_configured(),
        "account_sid":   TWILIO_ACCOUNT_SID[:8] + "..." if TWILIO_ACCOUNT_SID else "",
        "whatsapp_from": TWILIO_WHATSAPP_FROM,
        "webhook_base":  TWILIO_WEBHOOK_BASE or "(not set — webhook signature verification will fail)",
    }