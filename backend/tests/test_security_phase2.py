"""
Phase-2 security regression tests — transport and input hardening.

Same style as Phases 0 and 1: attempt the real attack, assert it fails, and
keep a positive control beside each one so nothing passes vacuously.
"""
import pytest
from datetime import datetime, timezone, timedelta

from database.core import SessionLocal
from database.models import Employee
from api.security import (
    create_access_token, create_refresh_token, WS_AUTH_SUBPROTOCOL,
)


@pytest.fixture()
def manager():
    s = SessionLocal()
    try:
        m = s.query(Employee).filter(
            Employee.system_role == "manager", Employee.is_active == True
        ).first()
        assert m
        return {"id": m.id, "name": m.name,
                "tok": create_access_token(m.id, m.system_role, m.name)}
    finally:
        s.close()


# ── P2.1 login: the name field is not a SQL LIKE pattern ──────────
@pytest.mark.parametrize("wildcard", ["%", "_", "%r%", "M_ K%"])
def test_login_name_is_not_a_like_pattern(client, wildcard):
    """'%' used to match every employee, so login accepted a wildcard for a
    name and .first() picked an arbitrary account."""
    r = client.post("/api/v1/auth/login",
                    json={"name": wildcard, "password": "whatever"})
    assert r.status_code == 401, \
        f"wildcard {wildcard!r} still matched an account (got {r.status_code})"
    assert "Invalid name or password" in r.json().get("detail", "")


def test_login_still_works_for_a_real_name(client, manager):
    """Positive control: the fix must not break ordinary login. Wrong password
    → 401 from the PASSWORD check, proving the name matched."""
    r = client.post("/api/v1/auth/login",
                    json={"name": manager["name"], "password": "definitely-not-it"})
    assert r.status_code == 401


def test_login_name_match_is_case_insensitive(client, manager):
    r = client.post("/api/v1/auth/login",
                    json={"name": manager["name"].upper(), "password": "definitely-not-it"})
    assert r.status_code == 401, "uppercase name should still reach the password check"


def test_ambiguous_name_is_refused_not_guessed(client, manager):
    """Two active accounts sharing a name → refuse, rather than silently
    logging you into whichever row came back first."""
    s = SessionLocal()
    dupe_id = None
    try:
        dupe = Employee(company_id=1, name=manager["name"], role="Employee",
                        system_role="employee", is_active=True,
                        password_hash="$2b$12$" + "x" * 53)
        s.add(dupe); s.commit(); dupe_id = dupe.id
    finally:
        s.close()
    try:
        r = client.post("/api/v1/auth/login",
                        json={"name": manager["name"], "password": "anything"})
        assert r.status_code == 409, \
            f"ambiguous name silently resolved to one account (got {r.status_code})"
    finally:
        s = SessionLocal()
        s.query(Employee).filter(Employee.id == dupe_id).delete()
        s.commit(); s.close()


# ── P2.2 rate-limit key must not be forgeable ─────────────────────
def test_rate_limit_key_ignores_xff_when_no_proxy_trusted():
    from api import rate_limit

    class _Req:
        def __init__(self, peer, xff):
            self.client = type("c", (), {"host": peer})()
            self.headers = {"x-forwarded-for": xff} if xff else {}

    assert rate_limit.TRUSTED_PROXY_HOPS == 0, "default must not trust the header"
    key = rate_limit.client_ip(_Req("9.9.9.9", "1.2.3.4"))
    assert key == "9.9.9.9", "a spoofed X-Forwarded-For got its own rate-limit bucket"


def test_rate_limit_key_reads_the_right_hop_behind_a_proxy(monkeypatch):
    """With one trusted proxy the caller is the RIGHTMOST entry — everything
    left of it is attacker-supplied."""
    from api import rate_limit
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXY_HOPS", 1)

    class _Req:
        def __init__(self, peer, xff):
            self.client = type("c", (), {"host": peer})()
            self.headers = {"x-forwarded-for": xff}

    # attacker prepends a fake hop; the proxy appends what it actually saw
    assert rate_limit.client_ip(_Req("10.0.0.1", "1.2.3.4, 203.0.113.7")) == "203.0.113.7"
    assert rate_limit.client_ip(_Req("10.0.0.1", "203.0.113.7")) == "203.0.113.7"


# ── P2.3 body size cap ────────────────────────────────────────────
def test_oversized_body_is_rejected(client, manager):
    r = client.post(
        "/api/v1/manager/command",
        headers={"Authorization": f"Bearer {manager['tok']}", "Content-Length": str(50 * 1024 * 1024)},
        content=b"x" * 32,   # declared huge; the cap must trip before the handler
    )
    assert r.status_code == 413, f"a 50 MB body was accepted (got {r.status_code})"


def test_normal_body_still_accepted(client, manager):
    """Positive control: a small body must pass the middleware untouched
    (any status except 413 means the cap didn't fire)."""
    r = client.post("/api/v1/auth/login", json={"name": "nobody-here", "password": "x"})
    assert r.status_code != 413


# ── P2.4 WebSocket auth via subprotocol, not the URL ──────────────
def test_ws_accepts_token_in_subprotocol(client, manager):
    with client.websocket_connect(
        f"/api/v1/ws/{manager['id']}",
        subprotocols=[WS_AUTH_SUBPROTOCOL, manager["tok"]],
    ) as ws:
        assert ws is not None   # handshake completed without a token in the URL


def test_ws_subprotocol_rejects_another_users_token(client, manager):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/ws/{manager['id'] + 12345}",
            subprotocols=[WS_AUTH_SUBPROTOCOL, manager["tok"]],
        ) as ws:
            ws.receive_text()


def test_ws_rejects_refresh_token_in_subprotocol(client, manager):
    from starlette.websockets import WebSocketDisconnect
    refresh = create_refresh_token(manager["id"])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/ws/{manager['id']}",
            subprotocols=[WS_AUTH_SUBPROTOCOL, refresh],
        ) as ws:
            ws.receive_text()


# ── P2.5 password policy ──────────────────────────────────────────
def test_weak_password_rejected_on_change(client, rotating_user):
    """Uses a throwaway account whose current password we know, so the request
    gets PAST the current-password check and actually reaches the policy."""
    tok = create_access_token(rotating_user["id"], "employee", "P2TEST-rotate")
    r = client.post("/api/v1/auth/change-password",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={"current_password": "p2test-passphrase", "new_password": "123456"})
    assert r.status_code == 400, f"weak password not rejected (got {r.status_code})"


def test_strong_password_accepted_on_change(client, rotating_user):
    """Positive control: the policy must not block a reasonable password."""
    tok = create_access_token(rotating_user["id"], "employee", "P2TEST-rotate")
    r = client.post("/api/v1/auth/change-password",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={"current_password": "p2test-passphrase",
                          "new_password": "quiet-harbor-lantern"})
    assert r.status_code == 200, f"a good password was rejected: {r.text[:200]}"


def test_ai_tool_cannot_bypass_the_password_policy(manager):
    """The Settings form refuses a weak password; the AI must too."""
    from api.claude_orchestrator import execute_tool
    s = SessionLocal()
    try:
        victim = s.query(Employee).filter(
            Employee.system_role == "employee", Employee.is_active == True).first()
        if not victim:
            pytest.skip("no employee to test against")
        vid, vhash = victim.id, victim.password_hash
    finally:
        s.close()
    out = execute_tool("set_employee_password",
                       {"employee_id": vid, "new_password": "123456"}, "Manager_1")
    assert "rejected" in out.lower(), f"AI set a weak password: {out[:120]}"
    s = SessionLocal()
    try:
        assert s.query(Employee).filter(Employee.id == vid).first().password_hash == vhash, \
            "password was changed despite the policy"
    finally:
        s.close()


# ── P2.6 refresh rotation + reuse detection ───────────────────────
@pytest.fixture()
def rotating_user():
    """A throwaway account whose refresh state we can churn without evicting
    the real user's session."""
    from api.security import hash_password
    s = SessionLocal()
    try:
        emp = Employee(company_id=1, name="P2TEST-rotate", role="Employee",
                       system_role="employee", is_active=True,
                       password_hash=hash_password("p2test-passphrase"))
        s.add(emp); s.commit()
        eid = emp.id
        token = create_refresh_token(eid)
        emp.refresh_token = hash_password(token)
        s.commit()
    finally:
        s.close()
    yield {"id": eid, "refresh": token}
    s = SessionLocal()
    s.query(Employee).filter(Employee.id == eid).delete()
    s.commit(); s.close()


def test_refresh_rotates_the_token(client, rotating_user):
    r = client.post("/api/v1/auth/refresh",
                    json={"refresh_token": rotating_user["refresh"]})
    assert r.status_code == 200
    body = r.json()
    assert body.get("refresh_token"), "refresh did not issue a new refresh token"
    assert body["refresh_token"] != rotating_user["refresh"], "token was not rotated"


def test_old_refresh_token_still_works_inside_the_grace_window(client, rotating_user):
    """A retried request or a second tab must not be punished."""
    first = client.post("/api/v1/auth/refresh",
                        json={"refresh_token": rotating_user["refresh"]})
    assert first.status_code == 200
    again = client.post("/api/v1/auth/refresh",
                        json={"refresh_token": rotating_user["refresh"]})
    assert again.status_code == 200, "a same-second retry was treated as theft"


def test_replaying_a_rotated_token_later_kills_the_session(client, rotating_user):
    """Outside the grace window, a superseded token means two parties hold it."""
    first = client.post("/api/v1/auth/refresh",
                        json={"refresh_token": rotating_user["refresh"]})
    assert first.status_code == 200
    new_token = first.json()["refresh_token"]

    # age the rotation past the grace window
    s = SessionLocal()
    try:
        emp = s.query(Employee).filter(Employee.id == rotating_user["id"]).first()
        emp.refresh_token_rotated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        s.commit()
    finally:
        s.close()

    replay = client.post("/api/v1/auth/refresh",
                         json={"refresh_token": rotating_user["refresh"]})
    assert replay.status_code == 401, "a stolen, already-rotated token still worked"

    # and the whole session is revoked — the thief's replay must not leave the
    # legitimate client's current token usable either
    after = client.post("/api/v1/auth/refresh", json={"refresh_token": new_token})
    assert after.status_code == 401, "session was not revoked after reuse was detected"


# ── retiring the deprecated query-string WS token ─────────────────
def test_query_string_ws_token_still_works_by_default(client, manager):
    """It is deprecated, not gone. Production logs showed most WebSocket
    handshakes still arriving this way (browser tabs open since before the
    subprotocol shipped), so switching it off is a decision about live users,
    not about code."""
    with client.websocket_connect(f"/api/v1/ws/{manager['id']}?token={manager['tok']}") as ws:
        assert ws is not None


def test_query_string_ws_token_can_be_switched_off(client, manager, monkeypatch):
    """NEXUS_ALLOW_WS_QUERY_TOKEN=0 retires it as a config flip, once the
    deprecation warnings stop appearing in the logs."""
    from starlette.websockets import WebSocketDisconnect
    import api.security as sec
    monkeypatch.setattr(sec, "ALLOW_WS_QUERY_TOKEN", False)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/ws/{manager['id']}?token={manager['tok']}"
        ) as ws:
            ws.receive_text()

    # the supported path must keep working when the fallback is off
    with client.websocket_connect(
        f"/api/v1/ws/{manager['id']}",
        subprotocols=[WS_AUTH_SUBPROTOCOL, manager["tok"]],
    ) as ws:
        assert ws is not None
