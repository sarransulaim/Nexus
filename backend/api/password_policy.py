"""
password_policy.py — one place that decides whether a password is acceptable
============================================================================
Every path that SETS a password calls `validate_password()`: first-run setup,
manager-created accounts, manager password resets, self-service change, and
the AI's set_employee_password tool. Login is deliberately NOT validated —
the rule is about what may be stored, and applying it at login would turn a
policy change into a lockout for everyone holding an older password.

Deliberately modest. A long list of composition rules ("one symbol, one
uppercase, no repeats") mostly produces P@ssw0rd! and a sticky note; length
plus a ban on the obvious choices is what actually resists guessing.
"""

import re

MIN_LENGTH = 10

# The passwords an attacker tries first. Kept short on purpose — this is a
# speed bump for the worst choices, not a substitute for rate limiting.
_COMMON = {
    "password", "password1", "password123", "passw0rd", "p@ssword",
    "12345678", "123456789", "1234567890", "qwerty123", "qwertyuiop",
    "letmein123", "welcome123", "admin123", "administrator", "iloveyou",
    "demo1234", "demo12345", "changeme", "changeme123", "secret123",
    "nexus123", "nexuscommand", "abc12345", "111111111", "trustno1",
}


class WeakPassword(ValueError):
    """Raised with a message meant to be shown straight to the user."""


def validate_password(password: str, *, name: str | None = None) -> None:
    """Raise WeakPassword if `password` may not be stored. Returns None if fine."""
    pw = password or ""

    if len(pw) < MIN_LENGTH:
        raise WeakPassword(
            f"Password must be at least {MIN_LENGTH} characters "
            f"(this one is {len(pw)}). A short phrase you'll remember works well."
        )

    if len(pw) > 200:
        # bcrypt only reads the first 72 bytes; anything past that is a waste
        # of a hash cycle and an easy way to make the endpoint expensive.
        raise WeakPassword("Password must be at most 200 characters.")

    lowered = pw.lower()

    if lowered in _COMMON:
        raise WeakPassword("That password is one of the most commonly used ones. Pick another.")

    if re.fullmatch(r"(.)\1*", pw):
        raise WeakPassword("Password can't be the same character repeated.")

    if _is_sequential(lowered):
        raise WeakPassword("Password can't be a simple sequence like 1234567890 or abcdefghij.")

    if name and lowered.replace(" ", "") == name.strip().lower().replace(" ", ""):
        raise WeakPassword("Password can't be your own name.")

    return None


def _is_sequential(s: str) -> bool:
    """True for runs like 'abcdefghij' or '9876543210' (the whole string)."""
    if len(s) < 4:
        return False
    deltas = {ord(b) - ord(a) for a, b in zip(s, s[1:])}
    return deltas in ({1}, {-1})
