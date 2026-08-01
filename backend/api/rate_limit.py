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

# Set NEXUS_DEBUG_PROXY=1 to log what each request actually carries. Getting
# TRUSTED_PROXY_HOPS wrong fails SILENTLY — limits still "work", they're just
# keyed on the wrong thing — so there has to be a way to see the real headers
# from inside the deployment rather than guessing at the platform's behaviour.
DEBUG_PROXY = os.getenv("NEXUS_DEBUG_PROXY", "") == "1"

_warned_missing_xff = False


def client_ip(request) -> str:
    """The caller's address, honouring X-Forwarded-For only as far as the
    number of proxies we've been told to trust."""
    global _warned_missing_xff
    peer = get_remote_address(request)
    forwarded = request.headers.get("x-forwarded-for", "")

    if DEBUG_PROXY:
        real = request.headers.get("x-real-ip", "")
        print(f"[proxy] peer={peer} xff={forwarded!r} x-real-ip={real!r} "
              f"hops={TRUSTED_PROXY_HOPS} path={request.url.path}")

    if TRUSTED_PROXY_HOPS <= 0:
        return peer

    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        # Configured to sit behind a proxy but nothing forwarded an address:
        # every limit is now keyed on the proxy's own (often rotating) address,
        # which is exactly the broken state this setting exists to fix. Say so
        # once — silence here reads as "rate limiting is fine" when it isn't.
        if not _warned_missing_xff:
            _warned_missing_xff = True
            print(f"⚠️  TRUSTED_PROXY_HOPS={TRUSTED_PROXY_HOPS} but no X-Forwarded-For on "
                  f"{request.url.path} (peer {peer}). Rate limits are keyed on the proxy, "
                  f"not the caller. Set NEXUS_DEBUG_PROXY=1 to inspect the headers.")
        return peer

    # N trusted proxies → the caller is hops[-N]. Clamp: a chain shorter than
    # advertised means someone stripped entries, so fall back to the leftmost
    # value we can see rather than indexing off the front of the list.
    return hops[max(0, len(hops) - TRUSTED_PROXY_HOPS)]


limiter = Limiter(key_func=client_ip)
