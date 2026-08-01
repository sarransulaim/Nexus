"""
Phase-4 security regression tests — web surface.

Same style as the earlier phases: attempt the real attack, assert it fails,
with positive controls so nothing passes vacuously.
"""
import pytest

from database.core import SessionLocal
from database.models import Employee
from api.security import create_access_token


@pytest.fixture()
def manager():
    s = SessionLocal()
    try:
        m = s.query(Employee).filter(
            Employee.system_role == "manager", Employee.is_active == True).first()
        assert m
        return {"id": m.id, "tok": create_access_token(m.id, m.system_role, m.name)}
    finally:
        s.close()


# ── P4.1 reflected XSS on the OAuth callback ──────────────────────
def test_oauth_callback_escapes_the_error_parameter(client):
    """`error` is attacker-controlled via a crafted link and was interpolated
    raw into the page — script running on the BACKEND's origin."""
    payload = '<img src=x onerror="alert(1)">'
    r = client.get("/api/v1/mcp/oauth/callback", params={"error": payload})
    assert r.status_code == 200
    body = r.text
    assert payload not in body, "the raw script payload was reflected into the page"
    assert "&lt;img" in body, "payload was not HTML-escaped"


def test_oauth_callback_page_carries_a_nonce_csp(client):
    r = client.get("/api/v1/mcp/oauth/callback", params={"error": "nope"})
    csp = r.headers.get("content-security-policy", "")
    assert "nonce-" in csp, "callback page has no nonce CSP"
    assert "'unsafe-inline'" not in csp.split("style-src")[0], \
        "script-src still allows unsafe-inline"


def test_oauth_callback_still_renders_its_message(client):
    """Positive control: escaping must not blank the page."""
    r = client.get("/api/v1/mcp/oauth/callback", params={"error": "access_denied"})
    assert "access_denied" in r.text
    assert "Connection cancelled" in r.text


# ── P4.2 security headers ─────────────────────────────────────────
@pytest.mark.parametrize("header,expected", [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
])
def test_security_headers_present(client, header, expected):
    r = client.get("/health")
    assert r.headers.get(header) == expected, f"{header} missing"


def test_csp_denies_by_default(client):
    r = client.get("/health")
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_breaking_headers_are_not_set(client):
    """COOP would sever window.opener and break the MCP OAuth popup; CORP
    same-site would block the frontend (a different site) from loading
    /manager/speak audio. Both must stay off."""
    r = client.get("/health")
    assert "cross-origin-opener-policy" not in {k.lower() for k in r.headers}
    assert "cross-origin-resource-policy" not in {k.lower() for k in r.headers}


def test_hsts_not_sent_over_plain_http(client):
    """Asserting HSTS from a local http server would pin localhost to https in
    the developer's browser for a year."""
    r = client.get("/health")
    assert "strict-transport-security" not in {k.lower() for k in r.headers}


def test_hsts_sent_when_proxy_reports_https(client):
    r = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in r.headers.get("strict-transport-security", "")


# ── P4.3 docs must not be public in production ────────────────────
@pytest.mark.parametrize("env,expected", [
    ({"RAILWAY_ENVIRONMENT": "production"},            False),   # the real deployment
    ({"NEXUS_ENV": "production"},                      False),
    ({"NEXUS_ENV": "PRODUCTION"},                      False),   # case-insensitive
    ({},                                               True),    # local dev keeps docs
    ({"NEXUS_ENV": "development"},                     True),
    ({"NEXUS_ENV": "production", "NEXUS_PUBLIC_DOCS": "1"}, True),  # explicit override
])
def test_docs_visibility_decision(env, expected):
    """The interactive docs enumerate every route, parameter and schema — a
    complete map of the attack surface — so they must be off in production.

    Tested as a pure function of the environment. The first version of this
    test re-imported `main` under a patched environment, which mutates
    sys.modules for every test that runs afterwards; a boolean does not need
    that much machinery to verify.
    """
    import main
    assert main.docs_enabled(env) is expected


def test_running_app_matches_the_decision(client):
    """The app must actually be wired to that decision, not just agree with it
    in the abstract."""
    import main
    if main.docs_enabled():
        assert main.app.docs_url == "/docs"
        assert main.app.openapi_url == "/openapi.json"
    else:
        assert main.app.docs_url is None
        assert main.app.openapi_url is None


# ── P4.4 unbounded limit parameters ───────────────────────────────
def test_recent_files_limit_is_capped(client, manager):
    r = client.get("/api/v1/files/recent", params={"limit": 100000},
                   headers={"Authorization": f"Bearer {manager['tok']}"})
    assert r.status_code == 422, f"an unbounded limit was accepted (got {r.status_code})"


def test_recent_files_normal_limit_still_works(client, manager):
    r = client.get("/api/v1/files/recent", params={"limit": 5},
                   headers={"Authorization": f"Bearer {manager['tok']}"})
    assert r.status_code == 200


def test_chat_messages_limit_is_capped(client, manager):
    r = client.get("/api/v1/chat/1/messages", params={"limit": 999999},
                   headers={"Authorization": f"Bearer {manager['tok']}"})
    assert r.status_code == 422, f"an unbounded limit was accepted (got {r.status_code})"


# ── P4.5 the TTS endpoint must not leak temp files ────────────────
def test_speak_cleans_up_its_temp_file():
    import inspect
    from api.routers import ai_commands
    src = inspect.getsource(ai_commands.generate_speech)
    assert "BackgroundTask(_unlink_quietly" in src, \
        "the generated mp3 is never deleted — unbounded disk growth"
    # every failure path must clean up too, not just the success path
    assert src.count("_unlink_quietly") >= 3, "a failure path still leaks the temp file"


def test_unlink_quietly_is_idempotent(tmp_path):
    from api.routers.ai_commands import _unlink_quietly
    f = tmp_path / "x.mp3"
    f.write_bytes(b"data")
    _unlink_quietly(str(f))
    assert not f.exists()
    _unlink_quietly(str(f))   # second call must not raise
