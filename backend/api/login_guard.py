"""
login_guard.py — brute-force throttling that an IP change doesn't defeat
=======================================================================
The slowapi limiter on /auth/login is keyed on the caller's address. That
stops one impatient client; it does nothing against the attack it exists to
stop, because an attacker who rotates addresses — trivial from any cloud
provider — simply never reaches the limit. Its counters also live in the
worker process and reset on every restart and deploy.

This adds a second, database-backed throttle keyed on the ACCOUNT as well as
the address, so:

  * guessing many passwords for one account is limited no matter how many
    addresses it comes from, and
  * the count survives a restart.

Deliberate trade-off, stated rather than buried: throttling per account means
someone who knows a colleague's name can deliberately push that account into
its cool-off and keep them out for the window. That is the standard trade and
it is the right way round — an account lockable for fifteen minutes is a
nuisance, an account guessable at unlimited speed is a breach. It is kept
tolerable by counting only FAILED attempts, expiring them on a short window,
and clearing them the moment a real sign-in succeeds.
"""

import os
from datetime import datetime, timedelta, timezone

# Generous by design: a person mistyping a password a few times must never
# meet this, so the numbers are set for "someone is guessing", not "someone is
# careless".
WINDOW_MINUTES     = int(os.getenv("NEXUS_LOGIN_WINDOW_MINUTES", "15"))
MAX_PER_ACCOUNT    = int(os.getenv("NEXUS_LOGIN_MAX_PER_ACCOUNT", "10"))
MAX_PER_ADDRESS    = int(os.getenv("NEXUS_LOGIN_MAX_PER_ADDRESS", "30"))


class TooManyAttempts(Exception):
    """Raised with a message suitable for showing to whoever is knocking."""


def _window_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)


def record_failure(identifier: str, ip_address: str | None) -> None:
    """Note one failed sign-in. Never raises — a bookkeeping failure must not
    turn a wrong password into a 500."""
    try:
        from database.core import SessionLocal
        from database.models import LoginAttempt
        db = SessionLocal()
        try:
            db.add(LoginAttempt(
                identifier=(identifier or "")[:200].strip().lower(),
                ip_address=(ip_address or "")[:45] or None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def clear(identifier: str) -> None:
    """Forget an account's failures after a successful sign-in, so a run of
    typos followed by success can't strand someone later in the window."""
    try:
        from database.core import SessionLocal
        from database.models import LoginAttempt
        db = SessionLocal()
        try:
            db.query(LoginAttempt).filter(
                LoginAttempt.identifier == (identifier or "").strip().lower()
            ).delete()
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def check(identifier: str, ip_address: str | None) -> None:
    """Raise TooManyAttempts if this account or address is in its cool-off.

    Fails OPEN: if the table can't be read we allow the attempt rather than
    locking everyone out of the product over a database hiccup. The in-process
    limiter is still in front of this, so failing open here is not "no limit".
    """
    try:
        from sqlalchemy import func
        from database.core import SessionLocal
        from database.models import LoginAttempt

        since = _window_start()
        name = (identifier or "").strip().lower()
        db = SessionLocal()
        try:
            by_account = db.query(func.count(LoginAttempt.id)).filter(
                LoginAttempt.identifier == name,
                LoginAttempt.created_at >= since,
            ).scalar() or 0

            by_address = 0
            if ip_address:
                by_address = db.query(func.count(LoginAttempt.id)).filter(
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.created_at >= since,
                ).scalar() or 0
        finally:
            db.close()
    except Exception:
        return

    if by_account >= MAX_PER_ACCOUNT:
        raise TooManyAttempts(
            f"Too many failed sign-in attempts for this account. "
            f"Try again in {WINDOW_MINUTES} minutes."
        )
    if MAX_PER_ADDRESS and by_address >= MAX_PER_ADDRESS:
        raise TooManyAttempts(
            f"Too many failed sign-in attempts from this location. "
            f"Try again in {WINDOW_MINUTES} minutes."
        )


def purge_old(days: int = 7) -> int:
    """Drop attempts older than the retention window. The table is only useful
    for the current window plus a little history for incident review; without
    this it grows forever."""
    try:
        from database.core import SessionLocal
        from database.models import LoginAttempt
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        db = SessionLocal()
        try:
            n = db.query(LoginAttempt).filter(LoginAttempt.created_at < cutoff).delete()
            db.commit()
            return int(n or 0)
        finally:
            db.close()
    except Exception:
        return 0
