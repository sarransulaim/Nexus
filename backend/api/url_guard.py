"""
url_guard.py — refuse to let the server fetch its own network
=============================================================
Connecting an MCP server means handing us a URL that the BACKEND then fetches
(OAuth discovery, dynamic client registration, token exchange). That is a
server-side request forgery primitive: whoever can submit the URL borrows the
backend's network position, which on a cloud host reaches things the public
internet cannot —

  http://169.254.169.254/…    cloud instance metadata (credentials)
  http://localhost:8000/…     our own API, from inside the trust boundary
  http://10.x / 172.16.x / 192.168.x   anything else in the private network
  http://[::1] / [fd00::]/…   the IPv6 equivalents

`validate_outbound_url` resolves the hostname and checks EVERY address it
resolves to, because a public name is free to point at 127.0.0.1 — the check
has to happen on the resolved address, not the string.

Known limit, stated rather than hidden: this validates at submission time, and
a name can resolve differently when the request is actually made (DNS
rebinding). Closing that needs pinning the resolved IP into the connection
itself. Blocking the obvious cases is worth doing on its own.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Escape hatch for local development, where MCP servers legitimately run on
# localhost. Never set this in a deployment.
ALLOW_PRIVATE = os.getenv("MCP_ALLOW_PRIVATE_URLS", "") == "1"


class UnsafeURL(ValueError):
    """Raised with a message suitable for showing to the user."""


def _is_blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private          # 10/8, 172.16/12, 192.168/16, fd00::/8
        or ip.is_loopback      # 127/8, ::1
        or ip.is_link_local    # 169.254/16 — cloud metadata lives here
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        # 100.64/10 (CGNAT): Railway's internal service mesh uses it, so
        # reaching it from here is reaching our own infrastructure.
        or ip in ipaddress.ip_network("100.64.0.0/10")
    )


def validate_outbound_url(url: str, *, require_https: bool = True) -> str:
    """Return the URL if the backend may fetch it; raise UnsafeURL otherwise."""
    if not url or not url.strip():
        raise UnsafeURL("A URL is required.")
    url = url.strip()

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL("URL must start with http:// or https://.")
    if not parsed.hostname:
        raise UnsafeURL("URL has no hostname.")

    if ALLOW_PRIVATE:
        return url

    if require_https and parsed.scheme != "https":
        raise UnsafeURL("URL must use https.")

    host = parsed.hostname
    try:
        # Every address the name resolves to, v4 and v6. A name that resolves
        # to both a public and a private address must be rejected.
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeURL(f"Could not resolve '{host}'. Check the URL.")

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeURL(f"Could not resolve '{host}'.")

    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])   # strip any zone id
        except ValueError:
            raise UnsafeURL(f"'{host}' resolved to an address we can't parse.")
        if _is_blocked(ip):
            raise UnsafeURL(
                f"'{host}' resolves to {ip}, which is inside a private or "
                f"link-local range. Connectors must point at a publicly "
                f"reachable server."
            )

    return url
