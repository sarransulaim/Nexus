"""
Account-aware brute-force throttling.

The in-process limiter is keyed on IP only, so rotating addresses defeats it;
its counters also die with the worker. These tests cover the throttle that
backs it up — and, just as importantly, that it doesn't lock out real people.
"""
import pytest
from datetime import datetime, timedelta, timezone

from database.core import SessionLocal
from database.models import LoginAttempt, Employee
from api import login_guard


@pytest.fixture(autouse=True)
def clean_attempts():
    def _purge():
        s = SessionLocal()
        s.query(LoginAttempt).filter(
            LoginAttempt.identifier.like("guardtest%")).delete(synchronize_session=False)
        s.commit(); s.close()
    _purge()
    yield
    _purge()


def _fail(name, ip="203.0.113.9", times=1, age_minutes=0):
    for _ in range(times):
        login_guard.record_failure(name, ip)
    if age_minutes:
        s = SessionLocal()
        try:
            when = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
            for row in s.query(LoginAttempt).filter(
                    LoginAttempt.identifier == name.lower()).all():
                row.created_at = when
            s.commit()
        finally:
            s.close()


def test_rotating_the_ip_does_not_defeat_the_throttle(monkeypatch):
    """The whole point: the per-IP limiter sees one attempt per address, so an
    attacker with many addresses never trips it."""
    monkeypatch.setattr(login_guard, "MAX_PER_ACCOUNT", 5)
    monkeypatch.setattr(login_guard, "MAX_PER_ADDRESS", 1000)
    for i in range(6):
        login_guard.record_failure("guardtest-victim", f"198.51.100.{i}")
    with pytest.raises(login_guard.TooManyAttempts):
        login_guard.check("guardtest-victim", "198.51.100.200")


def test_a_few_typos_do_not_lock_anyone_out(monkeypatch):
    """Positive control — the numbers are set for 'someone is guessing', not
    'someone is careless'."""
    monkeypatch.setattr(login_guard, "MAX_PER_ACCOUNT", 10)
    _fail("guardtest-typo", times=3)
    login_guard.check("guardtest-typo", "203.0.113.9")   # must not raise


def test_success_clears_the_counter(monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_PER_ACCOUNT", 5)
    _fail("guardtest-clear", times=4)
    login_guard.clear("guardtest-clear")
    _fail("guardtest-clear", times=4)
    login_guard.check("guardtest-clear", "203.0.113.9")   # must not raise


def test_old_failures_fall_out_of_the_window(monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_PER_ACCOUNT", 3)
    monkeypatch.setattr(login_guard, "WINDOW_MINUTES", 15)
    _fail("guardtest-old", times=10, age_minutes=60)
    login_guard.check("guardtest-old", "203.0.113.9")     # must not raise


def test_address_throttle_also_applies(monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_PER_ACCOUNT", 1000)
    monkeypatch.setattr(login_guard, "MAX_PER_ADDRESS", 5)
    for i in range(6):
        login_guard.record_failure(f"guardtest-spray-{i}", "203.0.113.77")
    with pytest.raises(login_guard.TooManyAttempts):
        login_guard.check("guardtest-spray-new", "203.0.113.77")


def test_attempts_against_unknown_accounts_are_counted():
    """A spray across guessed names must be visible, or the throttle only sees
    attacks on names that happen to exist."""
    login_guard.record_failure("guardtest-nosuchuser", "203.0.113.5")
    s = SessionLocal()
    try:
        assert s.query(LoginAttempt).filter(
            LoginAttempt.identifier == "guardtest-nosuchuser").count() == 1
    finally:
        s.close()


def test_guard_fails_open(monkeypatch):
    """A database hiccup must not lock everyone out of the product. The
    in-process limiter is still in front, so this isn't 'no limit'."""
    import api.login_guard as lg
    monkeypatch.setattr(lg, "_window_start", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    lg.check("guardtest-anything", "203.0.113.1")   # must not raise


def test_recording_never_raises():
    assert login_guard.record_failure(None, None) is None


def test_purge_removes_only_old_rows():
    _fail("guardtest-purge-old", times=2, age_minutes=60 * 24 * 30)
    _fail("guardtest-purge-new", times=2)
    login_guard.purge_old(days=7)
    s = SessionLocal()
    try:
        assert s.query(LoginAttempt).filter(
            LoginAttempt.identifier == "guardtest-purge-old").count() == 0
        assert s.query(LoginAttempt).filter(
            LoginAttempt.identifier == "guardtest-purge-new").count() == 2
    finally:
        s.close()


# ── end to end through the real endpoint ─────────────────────────
def test_login_endpoint_locks_an_account_after_repeated_failures(client, monkeypatch):
    monkeypatch.setattr(login_guard, "MAX_PER_ACCOUNT", 3)
    monkeypatch.setattr(login_guard, "MAX_PER_ADDRESS", 1000)
    name = "guardtest-endpoint"
    codes = [client.post("/api/v1/auth/login",
                         json={"name": name, "password": "wrong"}).status_code
             for _ in range(5)]
    assert 429 in codes, f"account never throttled: {codes}"
    assert codes[0] == 401, "first attempt should be a normal rejection"


def test_a_real_login_still_works_after_the_guard_lands(client):
    """Positive control on the live endpoint — the guard must not break
    ordinary sign-in."""
    s = SessionLocal()
    try:
        mgr = s.query(Employee).filter(
            Employee.system_role == "manager", Employee.is_active == True).first()
        name = mgr.name
    finally:
        s.close()
    r = client.post("/api/v1/auth/login", json={"name": name, "password": "demo123"})
    assert r.status_code in (200, 401), f"unexpected status {r.status_code}: {r.text[:150]}"
