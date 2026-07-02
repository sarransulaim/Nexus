"""
token_crypto.py — Fernet encryption for secrets at rest (MCP tokens, Google
OAuth tokens, etc.).

Multi-key scheme: secrets are ENCRYPTED with a dedicated, STABLE key
(NEXUS_TOKEN_KEY) when set, and DECRYPTED with any known key — so rotating
JWT_SECRET (the auth-token signing key) never bricks stored credentials, while
legacy rows that were encrypted under JWT_SECRET still decrypt. Set
NEXUS_TOKEN_KEY in the environment, independent of JWT_SECRET, so credential
encryption is decoupled from JWT rotation.
"""
import os
import base64
import hashlib

from cryptography.fernet import Fernet, MultiFernet


def _derive(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _multi() -> MultiFernet:
    # MultiFernet ENCRYPTS with the first key and DECRYPTS with any. Order:
    # NEXUS_TOKEN_KEY first (stable → new writes use it), JWT_SECRET next (so
    # existing rows keep decrypting), the literal default last (dead while
    # security.py's import guard requires a real JWT_SECRET). De-duped so a
    # single configured secret doesn't build two identical keys.
    keys, seen = [], set()
    for s in (os.getenv("NEXUS_TOKEN_KEY"), os.getenv("JWT_SECRET"),
              "nexus_change_this_in_production"):
        if s and s not in seen:
            seen.add(s)
            keys.append(Fernet(_derive(s)))
    return MultiFernet(keys)


def encrypt_secret(plaintext: str) -> str:
    return _multi().encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    return _multi().decrypt(stored.encode()).decode()
