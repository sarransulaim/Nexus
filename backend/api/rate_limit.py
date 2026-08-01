"""
rate_limit.py — Shared rate limiter
====================================
Extracted to its own file to avoid circular imports.

main.py registers app.state.limiter and the exception handler.
Routers import `limiter` from here and apply @limiter.limit() decorators.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# How many reverse proxies sit in front of us and APPEND to X-Forwarded-For.
# 0 (the default, and correct for a direct local run) = never trust the header.
#
# Why this is not optional in the cloud: slowapi's get_remote_address returns
# request.client.host, which behind a proxy is the PROXY's address, not the
# user's. On Railway that address is drawn from an internal pool (100.64.0.13,
# .14, .15 … observed in the logs), so every limit was being keyed on a
# rotating handful of proxy IPs — one attacker got several buckets, and
# unrelated users were randomly forced to share one. Rate limiting was
# effectively decorative in production.
#
# Trusting X-Forwarded-For naively is worse than not trusting it: the header is
# attacker-supplied, so a spoofed value would hand out a fresh bucket per
# request. The safe reading is positional. A proxy APPENDS the address it saw,
# so with N trusted proxies the caller is the Nth entry FROM THE RIGHT —
# everything to the left of that is client-supplied and unverifiable. An
# attacker sending "X-Forwarded-For: 1.2.3.4" just gets "1.2.3.4, <their real
# ip>", and we still read their real one.
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))


def client_ip(request) -> str:
    """The caller's address, honouring X-Forwarded-For only as far as the
    number of proxies we've been told to trust."""
    peer = get_remote_address(request)
    if TRUSTED_PROXY_HOPS <= 0:
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return peer

    # N trusted proxies → the caller is hops[-N]. Clamp: a chain shorter than
    # advertised means someone stripped entries, so fall back to the leftmost
    # value we can see rather than indexing off the front of the list.
    return hops[max(0, len(hops) - TRUSTED_PROXY_HOPS)]


limiter = Limiter(key_func=client_ip)
