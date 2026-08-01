"""
Phase-5 security regression tests — credential encryption at rest.

The risk here is two-sided: a weak derivation is a security bug, but a
derivation change that drops backward compatibility silently disconnects
every integration. Both directions are tested.
"""
import base64
import os

import pytest
from cryptography.fernet import Fernet

from api.token_crypto import (
    encrypt_secret, decrypt_secret, _derive_v2, _derive_legacy, _multi,
)


def _configured_key() -> str:
    key = os.getenv("NEXUS_TOKEN_KEY") or os.getenv("JWT_SECRET")
    assert key, "no credential-encryption key configured for the test run"
    return key


# ── backward compatibility: nothing already stored may be lost ────
def test_rows_written_with_the_legacy_derivation_still_decrypt():
    """Every MCP token and Google credential in the DB predates v2. If this
    fails, deploying disconnects every integration."""
    blob = Fernet(_derive_legacy(_configured_key())).encrypt(b"legacy-value").decode()
    assert decrypt_secret(blob) == "legacy-value"


def test_rows_written_under_jwt_secret_still_decrypt():
    jwt_secret = os.getenv("JWT_SECRET")
    if not jwt_secret:
        pytest.skip("JWT_SECRET not set")
    blob = Fernet(_derive_legacy(jwt_secret)).encrypt(b"jwt-era-value").decode()
    assert decrypt_secret(blob) == "jwt-era-value"


# ── new writes must use HKDF, not the bare hash ───────────────────
def test_new_writes_use_hkdf():
    blob = encrypt_secret("fresh-value")
    assert decrypt_secret(blob) == "fresh-value"
    # readable with the v2 key…
    assert Fernet(_derive_v2(_configured_key())).decrypt(blob.encode()) == b"fresh-value"
    # …and NOT with the legacy one, proving the primary key actually changed
    with pytest.raises(Exception):
        Fernet(_derive_legacy(_configured_key())).decrypt(blob.encode())


def test_hkdf_and_legacy_derivations_differ():
    key = _configured_key()
    assert _derive_v2(key) != _derive_legacy(key)


def test_derivation_is_domain_separated():
    """HKDF's info/salt must bind the key to this use. A bare sha256 of the
    secret would collide with any other component deriving from the same
    secret; HKDF output must not equal that."""
    key = _configured_key()
    import hashlib
    bare = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    assert _derive_v2(key) != bare


# ── the published default key must be gone ────────────────────────
def test_hardcoded_fallback_key_is_not_loaded(monkeypatch):
    """It shipped in the source, so anything encrypted under it was readable
    by anyone who could read the repo."""
    import api.token_crypto as tc
    src = open(tc.__file__, encoding="utf-8").read()
    # It may appear in the module docstring explaining its removal, but must
    # not be part of the key list.
    assert "nexus_change_this_in_production" not in src.split('"""', 2)[2], \
        "the hardcoded fallback key is still in the code"


def test_no_key_configured_fails_loudly(monkeypatch):
    """Silently encrypting with a known key looks identical to encrypting
    properly until someone reads the source."""
    monkeypatch.delenv("NEXUS_TOKEN_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="No credential-encryption key"):
        _multi()


# ── rotation must be safe and idempotent ──────────────────────────
def test_reencrypt_all_is_idempotent_and_preserves_values():
    from database.core import SessionLocal
    from database.models import MCPConnection
    from api.token_crypto import reencrypt_all

    s = SessionLocal()
    try:
        c = MCPConnection(company_id=1, app="p5test", label="P5TEST",
                          url="https://mcp.notion.com/mcp",
                          auth_token_enc=encrypt_secret("p5-secret-value"),
                          enabled=True, auth_type="token")
        s.add(c); s.commit(); cid = c.id
    finally:
        s.close()
    try:
        for _ in range(2):          # twice — rotation must be re-runnable
            stats = reencrypt_all()
            assert stats["failed"] == 0, f"rotation failed on a row: {stats}"
        s = SessionLocal()
        try:
            row = s.query(MCPConnection).filter(MCPConnection.id == cid).first()
            assert decrypt_secret(row.auth_token_enc) == "p5-secret-value", \
                "rotation changed the stored value"
        finally:
            s.close()
    finally:
        s = SessionLocal()
        s.query(MCPConnection).filter(MCPConnection.id == cid).delete()
        s.commit(); s.close()


def test_reencrypt_leaves_undecryptable_rows_alone():
    """A row encrypted under a key we no longer hold must be reported, not
    blanked — blanking silently disconnects an integration."""
    from database.core import SessionLocal
    from database.models import MCPConnection
    from api.token_crypto import reencrypt_all

    foreign = Fernet(Fernet.generate_key()).encrypt(b"unknown-key-value").decode()
    s = SessionLocal()
    try:
        c = MCPConnection(company_id=1, app="p5test-foreign", label="P5TEST",
                          url="https://mcp.notion.com/mcp",
                          auth_token_enc=foreign, enabled=True, auth_type="token")
        s.add(c); s.commit(); cid = c.id
    finally:
        s.close()
    try:
        stats = reencrypt_all()
        assert stats["failed"] >= 1
        s = SessionLocal()
        try:
            row = s.query(MCPConnection).filter(MCPConnection.id == cid).first()
            assert row.auth_token_enc == foreign, "an unreadable credential was overwritten"
        finally:
            s.close()
    finally:
        s = SessionLocal()
        s.query(MCPConnection).filter(MCPConnection.id == cid).delete()
        s.commit(); s.close()
