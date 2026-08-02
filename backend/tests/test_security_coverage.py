"""
Standing security invariants — the tests that keep phases 0-5 from rotting.

Everything else in tests/test_security_phase*.py pins a SPECIFIC hole that was
found and closed. These two pin the RULES, so a route or tool added next month
can't quietly reopen the same class of hole:

  1. Every HTTP route requires authentication, unless it is on an explicit
     allowlist with a written reason.
  2. Every manager-only AI tool is refused for an employee caller, and the
     shared-channel Team agent is confined to its allow-list.

If you add a genuinely public route or change the tool tiers, these fail. That
is the point: update the allowlist deliberately, in a diff someone reviews.
"""
import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute


# These checks take the app from the `client` fixture rather than importing
# `main` themselves, so there is one unambiguous answer to "which app?" and no
# import ordering to get wrong.


# Dependencies that establish an authenticated caller.
_AUTH_DEPENDENCIES = {"get_current_user", "require_manager", "require_team_lead"}

# Routes that are intentionally reachable without a bearer token. Each one
# needs a reason, because "it was already like that" is how the first audit
# found /manager/command unauthenticated.
PUBLIC_ROUTES = {
    ("GET",  "/"):                        "service banner, no data",
    ("GET",  "/health"):                  "load-balancer probe, no data",
    ("POST", "/api/v1/auth/login"):       "issues the token; rate limited",
    ("POST", "/api/v1/auth/refresh"):     "authenticates with the refresh token in the body",
    ("POST", "/api/v1/auth/setup"):       "first-run bootstrap; gated on SETUP_SECRET + rate limited",
    ("GET",  "/api/v1/auth/status"):      "returns only {initialized: bool}",
    ("GET",  "/api/v1/google/callback"):  "OAuth redirect target; single-use signed state is the credential",
    ("GET",  "/api/v1/mcp/oauth/callback"): "OAuth redirect target; single-use expiring state is the credential",
    ("POST", "/api/v1/internal/sync"):    "in-process caller; gated on X-Internal-Token (hmac)",
}

# WebSockets authenticate inline (before accept) rather than via Depends,
# because the token arrives in the Sec-WebSocket-Protocol header.
WEBSOCKET_ROUTES = {"/api/v1/ws/{employee_id}", "/api/v1/admin/stream"}


def _auth_dependencies_of(dependant, seen=None) -> set:
    """Names of every dependency callable reachable from a route."""
    seen = seen if seen is not None else set()
    found = set()
    for dep in dependant.dependencies:
        if id(dep) in seen:
            continue
        seen.add(id(dep))
        if dep.call is not None:
            found.add(getattr(dep.call, "__name__", str(dep.call)))
        found |= _auth_dependencies_of(dep, seen)
    return found


def _names_of(dependency_list) -> set:
    """Callable names from a list of Depends() markers."""
    out = set()
    for dep in dependency_list or []:
        call = getattr(dep, "dependency", None)
        if call is not None:
            out.add(getattr(call, "__name__", str(call)))
    return out


def _walk(node, prefix="", inherited=frozenset()):
    """Yield (route, full_path, inherited_dependency_names) for every route.

    Has to recurse, and that is not a detail. FastAPI 0.141 stopped flattening
    include_router() into app.routes and now nests each one behind an
    _IncludedRouter holding `original_router` (relative paths) and
    `include_context` (the prefix, plus any dependencies applied at include
    time). A flat `for route in app.routes` sees 7 of this app's ~74 routes on
    that version — and would have reported "every route is authenticated"
    while inspecting almost none of them. Written against the shape rather
    than the version so it survives the next change.
    """
    context = getattr(node, "include_context", None)
    if context is not None:
        sub = getattr(node, "original_router", None)
        if sub is not None:
            yield from _walk(
                sub,
                prefix + (getattr(context, "prefix", "") or ""),
                inherited | _names_of(getattr(context, "dependencies", [])),
            )
        return

    if isinstance(node, (APIRoute, APIWebSocketRoute)):
        yield node, prefix + node.path, inherited
        return

    for route in getattr(node, "routes", []):
        if route is node:
            continue
        yield from _walk(route, prefix, inherited)


def _http_routes(app):
    for route, path, inherited in _walk(app):
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, path, route, inherited


def _websocket_paths(app):
    return {path for route, path, _ in _walk(app) if isinstance(route, APIWebSocketRoute)}


# ── invariant 1: no route is accidentally public ──────────────────
def test_every_route_requires_auth_or_is_explicitly_public(client):
    unprotected = []
    for method, path, route, inherited in _http_routes(client.app):
        if (method, path) in PUBLIC_ROUTES:
            continue
        # Auth can come from the route itself or from dependencies applied
        # when its router was included.
        if not ((_auth_dependencies_of(route.dependant) | inherited) & _AUTH_DEPENDENCIES):
            unprotected.append(f"{method} {path}")

    assert not unprotected, (
        "These routes have no authentication dependency and are not on the "
        "public allowlist:\n  " + "\n  ".join(sorted(unprotected)) +
        "\n\nAdd Depends(get_current_user) (or require_manager), or add the "
        "route to PUBLIC_ROUTES in this file with the reason it is safe."
    )


def test_public_allowlist_has_no_stale_entries(client):
    """A route removed or renamed must not leave a permanent hole in the
    allowlist for whatever takes its path later."""
    live = {(m, p) for m, p, _, _ in _http_routes(client.app)}
    stale = set(PUBLIC_ROUTES) - live
    assert not stale, (
        f"PUBLIC_ROUTES lists routes that no longer exist: {sorted(stale)}. "
        f"The app reports {len(live)} routes in total. If that number looks far too "
        f"small, the app under inspection is not fully built (a partially imported "
        f"or reloaded `main`) rather than the routes having been deleted."
    )


def test_allowlist_is_small_enough_to_review():
    """A guard on the guard: if this grows, someone is silencing the check
    rather than adding auth."""
    assert len(PUBLIC_ROUTES) <= 12, (
        f"{len(PUBLIC_ROUTES)} public routes — justify the growth before raising this bound."
    )


def test_websockets_are_accounted_for(client):
    live = _websocket_paths(client.app)
    assert live == WEBSOCKET_ROUTES, (
        f"WebSocket routes changed: {live ^ WEBSOCKET_ROUTES}. WS auth is inline "
        f"(see api/security.py::ws_token_from) — confirm the new socket "
        f"authenticates BEFORE accept(), then update this set. "
        f"If sockets appear MISSING rather than added, suspect the app object "
        f"rather than the routes: {len(_websocket_paths(client.app))} sockets found."
    )


def test_the_route_walk_actually_sees_the_whole_app(client):
    """A coverage guard that inspects nothing passes everything.

    When FastAPI 0.141 nested included routers, a flat walk of app.routes
    dropped from ~74 routes to 7 — and an auth check over 7 routes still
    reports success. Pin the floor so shrinkage fails loudly instead of
    quietly narrowing what is being checked.
    """
    seen = list(_http_routes(client.app))
    assert len(seen) >= 60, (
        f"the route walk found only {len(seen)} routes — it is no longer "
        f"traversing the whole app, so every check in this file is inspecting "
        f"a fraction of it. Fix _walk() before trusting these results."
    )
    paths = {p for _, p, _, _ in seen}
    for expected in ("/api/v1/auth/login", "/api/v1/tasks/", "/api/v1/employees/"):
        assert expected in paths, f"{expected} not found by the walk"


# ── invariant 2: the AI tool tiers hold ───────────────────────────
def test_manager_only_tools_are_all_refused_for_an_employee():
    """The B1 gate derives identity from the authenticated agent_id. Every
    manager-only tool must be refused for a plain employee — checked across
    the whole set, not a sample, so a tool added to MANAGER_TOOLS is covered
    the day it lands."""
    from api.claude_orchestrator import MANAGER_ONLY_TOOLS, LEAD_TOOL_NAMES
    from database.core import SessionLocal
    from database.models import Employee

    s = SessionLocal()
    try:
        emp = s.query(Employee).filter(
            Employee.system_role == "employee", Employee.is_active == True).first()
        assert emp, "no plain employee to test with"
        emp_id = emp.id
    finally:
        s.close()

    assert MANAGER_ONLY_TOOLS, "the manager-only set is empty — the gate would be a no-op"

    from api.claude_orchestrator import execute_tool
    leaked = []
    for tool in sorted(MANAGER_ONLY_TOOLS):
        # A team lead legitimately gets a curated subset; a plain employee
        # never does, and the fixture above is a plain employee.
        out = execute_tool(tool, {}, f"Employee_{emp_id}")
        if "requires a manager" not in (out or ""):
            leaked.append(f"{tool} -> {str(out)[:90]}")

    assert not leaked, (
        "These manager-only tools did NOT refuse a plain employee:\n  "
        + "\n  ".join(leaked)
    )


def test_lead_tools_are_a_subset_of_manager_only_tools():
    """LEAD_TOOL_NAMES carves an exception out of the manager-only gate. An
    entry that isn't manager-only is dead config; one that isn't a real tool
    is a typo that silently grants nothing — or, worse, hides a mistake."""
    from api.claude_orchestrator import (
        MANAGER_ONLY_TOOLS, LEAD_TOOL_NAMES, MANAGER_TOOLS,
    )
    all_tools = {t["name"] for t in MANAGER_TOOLS}
    unknown = LEAD_TOOL_NAMES - all_tools
    assert not unknown, f"LEAD_TOOL_NAMES references tools that don't exist: {sorted(unknown)}"
    not_gated = LEAD_TOOL_NAMES - MANAGER_ONLY_TOOLS
    assert not not_gated, (
        f"LEAD_TOOL_NAMES lists tools that aren't manager-only (no-op entries): {sorted(not_gated)}"
    )


def test_team_agent_is_confined_to_its_allowlist():
    """The shared-channel agent answers in a room anyone can post to, so its
    tool set is an allow-list, not a deny-list."""
    from api.claude_orchestrator import (
        TEAM_ALLOWED_TOOLS, MANAGER_TOOLS, EMPLOYEE_TOOLS, execute_tool,
    )
    every_tool = {t["name"] for t in MANAGER_TOOLS} | {t["name"] for t in EMPLOYEE_TOOLS}
    forbidden = every_tool - set(TEAM_ALLOWED_TOOLS)
    assert forbidden, "the team allow-list covers every tool — it isn't restricting anything"

    # Spot-check the ones that would matter most if the gate regressed.
    for tool in ("send_email", "set_employee_password", "delete_task",
                 "approve_action", "check_my_emails"):
        if tool not in every_tool:
            continue
        assert tool in forbidden, f"{tool} is reachable by the shared-channel agent"
        out = execute_tool(tool, {}, "Team_C123")
        assert "can't do that here" in (out or ""), \
            f"team agent was allowed to call {tool}: {str(out)[:90]}"


def test_team_allowlist_references_only_real_tools():
    from api.claude_orchestrator import TEAM_ALLOWED_TOOLS, MANAGER_TOOLS, EMPLOYEE_TOOLS
    every_tool = {t["name"] for t in MANAGER_TOOLS} | {t["name"] for t in EMPLOYEE_TOOLS}
    unknown = set(TEAM_ALLOWED_TOOLS) - every_tool
    assert not unknown, f"TEAM_ALLOWED_TOOLS references non-existent tools: {sorted(unknown)}"
