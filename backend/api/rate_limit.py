"""
rate_limit.py — Shared rate limiter
====================================
Extracted to its own file to avoid circular imports.

main.py registers app.state.limiter and the exception handler.
Routers import `limiter` from here and apply @limiter.limit() decorators.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
