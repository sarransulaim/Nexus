"""
token_crypto.py — Fernet encryption for secrets at rest
=======================================================
Protects: MCP connector tokens (`auth_token_enc`, `refresh_token_enc`,
`oauth_client_secret_enc`) and the Google OAuth credential JSON
(`oauth_tokens.access_token`). google_auth.py aliases these functions, so this
is the single place credential encryption is defined.

Multi-key scheme: secrets are ENCRYPTED with the current primary key and
DECRYPTED with any key we still recognise. That means rotating `JWT_SECRET`
never bricks stored credentials, and rows written under an older derivation
keep working until they're re-encrypted.

Key derivation (v2, 2026-08-01)
-------------------------------
Keys were derived with a bare `sha256(secret)`. Two problems:

  * No domain separation. The same secret used for anything else derives the
    same bytes, so one leaked derived key is every derived key.
  * No KDF. SHA-256 is a hash, not a key-derivation function; with a
    low-entropy `NEXUS_TOKEN_KEY` there is nothing between a guess and the
    plaintext. (Ours is 64 random chars, so this was theoretical here — but
    the construction shouldn't depend on someone choosing a good value.)

v2 uses HKDF-SHA256 with a fixed salt and a version-bound `info` string.
Old derivations stay in the DECRYPT list so nothing already stored is lost;
`reencrypt_all()` migrates rows onto v2 when you're ready.

The hardcoded `"nexus_change_this_in_production"` fallback is GONE. It was
last in the key list, so it never encrypted anything while security.py's
import guard was in place — but a default key committed to a public repo is
not something to leave loaded, and if it ever HAD encrypted a row, that row
was readable by anyone who could read the source.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Fixed, non-secret salt. HKDF's salt does not need to be secret — its job is
# domain separation between uses of the same input keying material. Changing
# this string invalidates every v2 key, so it is versioned rather than edited.
_HKDF_SALT = b"nexus-command/token-crypto/v2"
_HKDF_INFO = b"credential-encryption"


def _derive_v2(secret: str) -> bytes:
    """HKDF-SHA256 → 32 bytes → urlsafe-b64 for Fernet."""
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(secret.encode())
    return base64.urlsafe_b64encode(raw)


def _derive_legacy(secret: str) -> bytes:
    """The original bare-SHA-256 derivation. Decrypt-only — kept so rows
    written before 2026-08-01 still open."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _key_material() -> list[str]:
    """Configured secrets, most-preferred first, de-duplicated."""
    out, seen = [], set()
    for s in (os.getenv("NEXUS_TOKEN_KEY"), os.getenv("JWT_SECRET")):
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _multi() -> MultiFernet:
    """MultiFernet ENCRYPTS with the first key and DECRYPTS with any of them.

    Order: v2 derivations first (so every new write uses HKDF), then the legacy
    SHA-256 derivations so existing rows keep decrypting.
    """
    secrets_in_use = _key_material()
    if not secrets_in_use:
        # Previously this silently fell back to a key published in the source.
        # Refusing is the only safe answer: encrypting with a known key looks
        # identical to encrypting properly until someone reads the repo.
        raise RuntimeError(
            "No credential-encryption key configured. Set NEXUS_TOKEN_KEY "
            "(preferred) or JWT_SECRET before storing or reading secrets."
        )
    keys = [Fernet(_derive_v2(s)) for s in secrets_in_use]
    keys += [Fernet(_derive_legacy(s)) for s in secrets_in_use]
    return MultiFernet(keys)


def encrypt_secret(plaintext: str) -> str:
    return _multi().encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    return _multi().decrypt(stored.encode()).decode()


# ---------------------------------------------------------------------------
# ROTATION / RE-ENCRYPTION
# ---------------------------------------------------------------------------
def reencrypt_all() -> dict:
    """Re-encrypt every stored credential under the CURRENT primary key.

    Run this after changing NEXUS_TOKEN_KEY, or to migrate rows off the legacy
    SHA-256 derivation. Safe to run repeatedly. Each row is handled
    independently: one undecryptable value is reported, not fatal — a single
    corrupt row must not stop the rest from migrating.

    Usage:  python -m api.token_crypto rotate
    """
    from database.core import SessionLocal
    from database.models import MCPConnection, OAuthToken

    fernet = _multi()
    stats = {"migrated": 0, "already_current": 0, "failed": 0, "empty": 0}
    db = SessionLocal()
    try:
        targets = [
            (MCPConnection, ("auth_token_enc", "refresh_token_enc", "oauth_client_secret_enc")),
            (OAuthToken,    ("access_token",)),
        ]
        for model, columns in targets:
            for row in db.query(model).all():
                for column in columns:
                    stored = getattr(row, column, None)
                    if not stored:
                        stats["empty"] += 1
                        continue
                    try:
                        plaintext = fernet.decrypt(stored.encode())
                    except Exception:
                        # Written under a key we no longer hold. Leave it be —
                        # blanking it would silently disconnect an integration.
                        stats["failed"] += 1
                        print(f"  ! {model.__name__}.{column} id={row.id} won't decrypt — left as is")
                        continue
                    # Fernet ciphertext is non-deterministic, so "is it already
                    # current?" can't be answered by comparing bytes. Re-encrypt
                    # unconditionally; the primary key is the current one.
                    setattr(row, column, fernet.encrypt(plaintext).decode())
                    stats["migrated"] += 1
        db.commit()
    finally:
        db.close()
    return stats


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "rotate":
        print("Re-encrypting stored credentials under the current primary key…")
        print(reencrypt_all())
    else:
        print("usage: python -m api.token_crypto rotate")
