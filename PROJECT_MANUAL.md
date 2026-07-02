# Nexus Command — Complete Project Manual

> **What this file is.** The single, authoritative reference for the entire Nexus Command
> system: what every part does, how the pieces connect, how each tool works, how routing is
> done, and every operational detail. It is a **living document** — whenever the code changes,
> this file must be updated in the same change so it never drifts from reality.
>
> **Last updated:** 2026-07-02
> **Scope:** the whole project (`backend/` + `frontend/`).

---

## Table of Contents

1. [What Nexus Is](#1-what-nexus-is)
2. [How to Run It](#2-how-to-run-it)
3. [High-Level Architecture & Request Flow](#3-high-level-architecture--request-flow)
4. [Tech Stack](#4-tech-stack)
5. [Repository Layout (every file)](#5-repository-layout-every-file)
6. [The AI Orchestrator (the brain)](#6-the-ai-orchestrator-the-brain)
7. [Complete Tool Catalog](#7-complete-tool-catalog)
8. [The AI/Model Routing Layer (ai_router)](#8-the-aimodel-routing-layer-ai_router)
9. [API Reference (every route)](#9-api-reference-every-route)
10. [Data Model (43 tables)](#10-data-model-43-tables)
11. [Authentication & Security](#11-authentication--security)
12. [RAG — Semantic Memory](#12-rag--semantic-memory)
13. [The Coordination Layer (digest, drift, contracts)](#13-the-coordination-layer-digest-drift-contracts)
14. [Real-Time / WebSocket Protocol](#14-real-time--websocket-protocol)
15. [Background Jobs & Schedulers](#15-background-jobs--schedulers)
16. [External Integrations](#16-external-integrations)
17. [Frontend](#17-frontend)
18. [Event Bus, Admin & Observability](#18-event-bus-admin--observability)
19. [Testing](#19-testing)
20. [Known Gaps & Deferred Work](#20-known-gaps--deferred-work)
21. [How to Keep This Manual Updated](#21-how-to-keep-this-manual-updated)

---

## 1. What Nexus Is

Nexus Command is an **"AI Chief of Staff" operating system for a company/team**. It is two products in one shell:

- **For the manager** → a Chief-of-Staff AI (77 tools) that runs the org by natural language ("reassign Sarah's overdue work", "audit the team", "post this to #engineering").
- **For each employee** → a personal AI co-pilot / "digital twin" (36 tools) that manages their tasks, drafts their email, and *negotiates with colleagues' AIs on their behalf*.

**The differentiating vision** (the direction the product is being built toward): kill **integration risk** on team projects. When many people build interdependent pieces of one project, Nexus keeps the pieces compatible by (a) capturing dependency **contracts** between tasks, (b) detecting **drift**, and (c) surfacing "what changed in things you depend on" to each person — via a daily **digest** in chat plus directed **drift alerts**. Future MCP integrations (GitHub/Figma/Docs) will feed the real pushed work into this same pipeline.

Two AI hallmarks make it distinct from a normal PM tool:
- **AI-to-AI negotiation** — an employee's agent consults colleagues' agents (checking their workload/calendar) *before* a human is interrupted.
- **Per-person agents + a central brain + RAG** — only this architecture can infer, maintain, and police interdependencies automatically.

---

## 2. How to Run It

**Prerequisites**
- Python 3.10+ with the backend virtualenv at `backend/venv/`.
- Node.js + npm (frontend).
- PostgreSQL running locally (the app reads `DATABASE_URL`; default `postgresql://postgres:postgres@localhost:5432/nexus_core`).
- [Ollama](https://ollama.com) running locally with two models pulled:
  - `ollama pull nomic-embed-text` — **required for RAG** (768-dim embeddings). If this model disappears, semantic search/ingest silently breaks; `main.py` prints a startup warning when it's missing.
  - `ollama pull qwen2.5:7b` — used for chat summaries, the AI-narrated digest, briefings, and preference extraction.
- Optional: `ollama pull qwen2.5:3b` (faster small model used by some Ollama defaults).

**Environment (`backend/.env`, git-ignored)** — keys consumed by the code:
`GEMINI_API_KEY`, `CLAUDE_API_KEY`, `DATABASE_URL`, `JWT_SECRET`, `SETUP_SECRET`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`,
`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `OLLAMA_TIMEOUT`,
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `TWILIO_WEBHOOK_BASE`.
**Recommended: `NEXUS_TOKEN_KEY`** — a stable secret-at-rest key, independent of `JWT_SECRET`. Token encryption is **multi-key** (`api/token_crypto.py`): it ENCRYPTS with `NEXUS_TOKEN_KEY` and DECRYPTS with any known key, so rotating `JWT_SECRET` no longer bricks stored MCP/Google tokens (and legacy rows still decrypt). If unset it falls back to `JWT_SECRET` — then a `JWT_SECRET` rotation WOULD brick tokens, so set it. Also: `OLLAMA_HOST`,
`ALLOWED_ORIGINS`, `EMBED_MODEL`, `EMBED_DIM`, `TWILIO_ALLOW_UNVERIFIED` (dev only),
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `SLACK_ENABLED` (default `true`; set **`false`** to skip auto-starting the Slack Socket-Mode bot — on networks that drop its WebSocket, its reconnect storm has crashed the backend with a segfault).

**REQUIRED, fail-closed (2026-06-23 hardening):** `JWT_SECRET` must be a strong random value **≥32 chars** — `api/security.py` **raises at import** if it's unset, a known default, or too short (the old silent default is gone). `SETUP_SECRET` must be set (**≥16 chars**, not the old `NEXUS_SETUP_2026`) or `POST /auth/setup` returns **503** (rate-limited 5/hour, `hmac.compare_digest`). Generate each, per instance: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Rotating `JWT_SECRET` invalidates all existing tokens (everyone re-logs-in).

**First-time DB setup**
```
cd backend
venv\Scripts\python.exe create_tables.py    # creates all tables (idempotent)
venv\Scripts\python.exe seed_demo.py         # loads the demo org (company_id=1; everyone's password = "demo123")
```

**Run the backend** (FastAPI on :8000, auto-reload):
```
cd backend
venv\Scripts\python.exe main.py
```
> ⚠️ **Production: a SINGLE uvicorn worker only** (the default — never run with `--workers N>1`). The slowapi rate-limiter, the Google-OAuth `_pending_flows` store (`api/google_auth.py`), and the proactive/scheduler loops all keep state **in-process**; multiple workers would split that state across processes and silently break rate-limiting and OAuth-callback matching.

**Run the frontend** (Vite dev server on :5173):
```
cd frontend
npm install
npm run dev
```

**Run the tests**:
```
cd backend
venv\Scripts\python.exe -m pytest tests/ -q
```

---

## 3. High-Level Architecture & Request Flow

```
 Browser (React/Vite)
   │  axios + raw fetch (Bearer JWT)        WebSocket (?token=)
   ▼                                         ▼
 FastAPI (main.py)  ──includes──►  routers (auth, tasks, employees, meetings, …)
   │                                  │
   │  POST /api/v1/manager/command    │  CRUD + reads (each behind get_current_user)
   ▼                                  ▼
 run_orchestrator()  ◄── the AI brain (api/claude_orchestrator.py)
   │  classify → pick model → load memory + cached system prompt + live snapshot
   │  Claude tool-use loop → execute_tool() per tool call (≈110 tools)
   ▼
 PostgreSQL (SQLAlchemy)   +   Ollama (embeddings, summaries)   +   Gemini (Google)   +   Claude (orchestration)
   │
   ▼  on DB change → notifier.broadcast("SYNC_REQUIRED") → WS → frontend refetches
```

**The AI command flow (the heart):**
1. Frontend `POST /api/v1/manager/command` with `{command_text}` and a Bearer token.
2. `process_command` ([ai_commands.py](backend/api/routers/ai_commands.py)) **derives the agent id from the token** (`Manager_1` for managers, `Employee_<id>` for employees) — the body is not trusted.
3. `run_orchestrator(agent_id, command)`:
   - Greetings/`reset` short-circuit without calling Claude.
   - `classify_command()` picks **haiku** (cheap/simple) or **sonnet** (complex) — see §6.
   - Loads conversation memory (`agent_memory` table) + builds the system prompt (static cached persona + learned personality + a **live context snapshot** assembled by re-running read tools).
   - Runs a tool-use loop (max 10 iterations): Claude responds → if `tool_use`, `execute_tool()` runs each tool and feeds results back → repeat until `end_turn`.
   - Streams reasoning to the UI via the **Glass Brain** queue.
   - Saves memory; every 5 turns triggers background preference learning; emits cost to the admin board.
4. A `SYNC_REQUIRED` WebSocket broadcast tells the frontend to refetch dashboard data.

---

## 4. Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python, FastAPI, Starlette, Uvicorn, SQLAlchemy, slowapi (rate limiting) |
| DB | PostgreSQL (via `psycopg2`) |
| Orchestration AI | Anthropic Claude — `claude-sonnet-4-5` (complex), `claude-haiku-4-5` (simple) |
| Google AI | Google Gemini `gemini-2.5-pro` (Gmail/Calendar reasoning) |
| Local AI (free) | Ollama — `qwen2.5:7b` (summaries/briefings/digest), `nomic-embed-text` (RAG embeddings) |
| Auth | JWT (PyJWT, HS256), bcrypt (passlib) |
| Token encryption | `cryptography` (Fernet) for OAuth tokens at rest |
| Files | pymupdf, python-docx, pandas/openpyxl, Pillow, python-magic |
| Omnichannel | slack-bolt/slack-sdk, twilio (WhatsApp/SMS) |
| Real-time | WebSockets (Starlette) |
| Frontend | React 19, Vite, axios, recharts, react-force-graph-2d/3d, three.js, Tailwind v4 |
| Tests | pytest |

---

## 5. Repository Layout (every file)

### `backend/`
| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, router wiring, lifespan (starts background loops/schedulers), `/health`, the employee WebSocket endpoint, admin demo triggers, OAUTHLIB dev gating, RAG health warning. |
| `api/claude_orchestrator.py` | **The brain.** Tool definitions (`MANAGER_TOOLS`, `EMPLOYEE_TOOLS`), `execute_tool()`, `run_orchestrator()`, system prompts, smart model router, prompt caching, context snapshot, agent memory, `_parse_date`, the contract handlers, AI-to-AI negotiation. |
| `api/ai_router.py` | Central multi-provider router — routes each task type to the cheapest capable model (Claude/Gemini/Ollama) with fallbacks. |
| `api/security.py` | JWT create/decode, bcrypt hashing, `get_current_user`, `require_manager`. Single source of truth for `JWT_SECRET`/`JWT_ALGORITHM`. |
| `api/ws_manager.py` | `ConnectionManager` (`notifier`) — room-based WebSocket: per-employee connections, channel/meeting rooms, broadcast/notify/chat/thought helpers. |
| `api/file_intelligence.py` | Extract text from uploads (PDF/DOCX/XLSX/CSV/images), Claude-analyze into structured `{type, title, summary, proposed_actions, …}`. |
| `api/google_auth.py` | Google OAuth2 flow + token storage. **OAuth tokens are Fernet-encrypted at rest** (key from `NEXUS_TOKEN_KEY`/`JWT_SECRET`; legacy plaintext auto-migrates). |
| `api/google_services.py` | Gmail/Calendar operations reasoned through Gemini (read/summarize email, draft, send, calendar, availability, focus time). |
| `api/google_router.py` | Google REST endpoints (connect/callback/status/disconnect/emails/calendar/availability/focus-time). Data endpoints require auth + self-or-manager. |
| `api/ollama_client.py` | Thin HTTP wrapper for Ollama (`generate`, `embed`, health). |
| `api/rate_limit.py` | slowapi limiter instance. |
| `api/routers/auth.py` | Login, setup (first manager), refresh, logout, me, employee create, set/change password. |
| `api/routers/tasks.py` | `GET /tasks/` (auth + company-scoped). |
| `api/routers/employees.py` | `GET /employees/` (auth + company-scoped; includes assisting peer requests). |
| `api/routers/meetings.py` | Meetings CRUD (GET auth; create/update/delete require_manager). |
| `api/routers/notifications.py` | List/read/read-all notifications (auth + identity-checked). |
| `api/routers/analytics.py` | `/summary`, `/team/{name}`, `/employee/{id}` (auth). |
| `api/routers/peer_requests.py` | Respond to a peer request (Accepted/Declined/Completed); auth + recipient/manager check. |
| `api/routers/files.py` | Upload (+AI analyze, auto-index to RAG), list recent, get, delete, execute proposed actions. |
| `api/routers/ai_commands.py` | `/manager/command` (the AI entry point), `/trigger-audit`, `/command-history/{agent}`, `/speak` (TTS). |
| `admin_router.py` | Manager-only admin/observability: metrics, health, agents, recent-errors, live `/stream` WebSocket (circuit board). |
| `channels_router.py` | Omnichannel link/verify + WhatsApp/Telegram/Slack inbound webhooks. |
| `chat_router.py` | Team chat: ensure-project-channel, my-channels (with unread), messages, send, summarize. `post_ai_message()` (AI posts + broadcasts + indexes). `_channel_for_member` membership guard. |
| `event_bus.py` | In-process pub/sub `event_bus` + cost estimation (cache-aware) + convenience emitters (feeds the admin circuit board). |
| `negotiation_engine.py` | Background workload-rebalancing engine (AI-to-AI), runs every 30 min + on `rebalance_team`. |
| `proactive_engine.py` | Periodic watcher (every 15 min): overdue/deadline/overload alerts (deterministic, no LLM). |
| `autonomous_briefings.py` | Mon-Fri morning briefings (APScheduler) — composes + delivers per-person briefings (in-app + WhatsApp/Telegram). |
| `project_digest.py` | **Daily Project Digest + dependency-drift + contract-drift.** `build_project_digest`, `_narrate` (Ollama), `run_drift_alerts`, `_propose_and_surface_fix`, `run_all_digests`, scheduler. |
| `resolution_engine.py` | **NEW (2026-06-24).** `propose_resolution(contract_id)` — on contract drift, asks Claude for the single cheapest correct fix; surfaced as an `ApprovalRequest` by `run_drift_alerts`. |
| `rag.py` | RAG: `embed_text`, `chunk_text`, `index_content`, `index_async`, `search`, `backfill`. numpy cosine over a JSON `knowledge_embeddings` table (no pgvector). |
| `dependency_inference.py` | **Automatic dependency mapping (shipped).** `propose_dependencies(project_id)` infers the dependency graph + interface contracts from a project's task text via one Claude call (`claude-sonnet-4-5`, read-only). `map_project(project_id)` stores each inferred edge as a **provisional** `Contract` (`status="proposed"`), posts the readable map into the project channel (as "Nexus Coordinator"), and notifies the manager. `auto_map_unmapped_projects()` runs this for every project with ≥2 tasks and no contracts. `start/stop_mapping_scheduler()` = 30-min background pass. Provisional contracts are **not** drift-watched until the manager confirms (`confirm_dependency_map` tool → proposed→active). Advise-before-dictate. |
| `preference_learner.py` | Builds the per-person "digital twin": extracts behavioral preferences (Ollama) every ~5 turns; injects a personality blurb into the system prompt. |
| `slack_bot.py` | Slack Socket-Mode bot — DMs route to the user's agent; runs in a background thread. |
| `twilio_client.py` | WhatsApp/SMS send + **fail-closed** webhook signature verification. |
| `telegram_client.py` | Graceful stub (`is_configured`/`send_message`/…) so Telegram paths degrade instead of crashing. |
| `database/core.py` | SQLAlchemy engine, `SessionLocal`, `get_db` dependency. |
| `database/models.py` | All 43 tables (ORM models). |
| `create_tables.py` | `Base.metadata.create_all` (idempotent). |
| `seed_demo.py` | Wipes + seeds company_id=1 (3 teams, 1 manager + 8 employees, 3 projects, ~22 tasks with deliberate overload/overdue/deadline/dependency cases). |
| `clear_memory.py` | Utility to clear agent memory. |
| `tests/` | pytest suite (`conftest.py`, `test_auth.py`, `test_rag.py`, `test_coordination.py`). |

### `frontend/src/`
| File | Responsibility |
|---|---|
| `main.jsx` | React entrypoint. |
| `App.jsx` | App shell: sidebar (role-based nav), top bar, notification bell, task modal, page router (tab-based), system-audit button. |
| `context/NexusContext.jsx` | **Global state + logic**: auth (login/logout, token refresh interceptor), WebSocket (auto-reconnect + 60s polling fallback), data fetching (tasks/employees/meetings/notifications), the AI command sender, voice (speech-in + TTS), all WS message parsing. |
| `components/ui/SharedUI.jsx` | Icons, badges, avatars, spinners, progress bars. |
| `components/FileUploadCard.jsx` | Manager-only file-intelligence drop card (mounted in the Commands page). |
| `components/WorkMap.jsx` | 3D/2D force-graph org map. |
| `utils/helpers.jsx` | `ErrorBoundary`, `safeStr`, date formatting. |
| `pages/Dashboard.jsx` | Manager dashboard (org overview, telemetry). |
| `pages/Commands.jsx` | AI Commands page — voice waveform, command input, Glass Brain stream, command history, the file-upload card. |
| `pages/ChatPage.jsx` | Team chat — channel list with unread badges, messages with timestamps, send (optimistic + honest failure), Summarize + manager "Post Digest" buttons. |
| `pages/TeamMatrix.jsx` | Team workload matrix. |
| `pages/Directives.jsx` | Employee view (tasks/meetings/peer requests). |
| `pages/Database.jsx` | Raw schema / AI-driven CRUD view. |
| `pages/Analytics.jsx` | Performance metrics + charts (recharts). |
| `pages/AdminPage.jsx` | Admin circuit board (metrics, agents, live stream). |
| `pages/ConnectionsPage.jsx` | **The single integration hub (2026-06-24):** clean card grid — Google OAuth, messaging channels (Slack/WhatsApp/Telegram), and MCP app/data-source connectors. The old Integrations page was merged in here. |
| `pages/Login.jsx` | Login screen. |
| `pages/SettingsPage.jsx`, `ApprovalsPage.jsx`, `GoalsPage.jsx`, `MeetingsPage.jsx` | **Wired to real data (2026-06-23):** Settings (profile + change-passcode + Google status), Approvals (manager approve/reject via `/approvals`), Goals (list/create/progress via `/goals`), Meetings (live `/meetings`). |
| `pages/GoogleWorkspace.jsx` | **Wired to real data (2026-06-24):** live Google connection status + a working connect + Co-Pilot quick-actions. |
| `pages/Integrations.jsx` | **Retired 2026-06-24** — merged into ConnectionsPage (the unified hub); removed from the nav. File kept but unrouted; the `/integrations/status` endpoint still exists. |
| `pages/placeholder_pages.jsx` | Legacy duplicate stub definitions — **not routed** by `App.jsx` (kept only for reference). |

---

## 6. The AI Orchestrator (the brain)

File: `backend/api/claude_orchestrator.py`.

**`run_orchestrator(agent_id, command) -> str`** — the single entry for every AI command (web, Slack, WhatsApp, the negotiation engine all call it).

1. **Short-circuits**: `reset` clears memory; greetings ("hi", "hello", …) return a canned reply without calling Claude.
2. **Identity** (three roles): `Employee_<id>` → employee tools + employee prompt; `Team_<channel>` → **TEAM/COORDINATOR** tools + team prompt (see below); otherwise manager tools + manager prompt. An optional `extra_context` arg appends caller-supplied volatile context (the Slack channel roster + recent messages) to the dynamic block.

   **Team/Coordinator tier (added 2026-06-25):** a third role *between* employee and manager, for shared channels (Slack channels, later Nexus group chat). `TEAM_TOOLS` (14) is a strict **read-mostly allow-list** built by reusing existing schemas: project/task reads, team status/workload/completion, `view_task_dependencies`/`view_contracts` (the coordination differentiator), `search_knowledge`, and **`create_escalation` as the ONLY write** (flag a blocker to the manager, attributed to `Team_<channel>`). It has **no** task writes, **no** approvals/HR/company commands, and **no** personal data (emails/calendar/personal tasks/DM memory). Enforced server-side in `execute_tool` (`caller_is_team` → hard allow-list + strips self-identity) — defense in depth, not just the handed toolset. Forced to **sonnet**; team snapshot = team status + overdue + at-risk contracts. **Security-review hardening (2026-06-28):** because the team gate strips `employee_id`, four tools that silently fall back to WHOLE-COMPANY data when unscoped were REMOVED from the allow-list — `view_goals` (everyone's personal OKRs), `view_task_comments`, `view_meetings`, `search_employees` (full directory) — they leaked personal data into public channels. Also: `slack_bot.handle_mention` now requires the mentioner be a recognized employee (`get_employee_by_slack_id`) before running the team agent; `goals.update_progress` + the `update_goal_progress` AI tool are now owner-scoped (an employee can't overwrite another's goal); the employee identity-strip writes `from_agent_id` as the string `Employee_<id>` (was an int → broke `create_escalation` on Postgres); and `slack/start-link` generates a code with no collision among live pending codes. **Robustness/security pass (2026-06-28):** MCP is now **role-gated** — company MCP servers are NO LONGER attached to this Team tier (`mcp_servers = [] if is_team else _load_mcp_servers()`); since MCP tools run server-side outside the allow-list, a public channel can no longer reach the company's connectors (only manager + employee, authenticated/private, get MCP). Also fixed this pass: the insecure `I am <name>` Slack self-link (**impersonation-by-name** — anyone could DM "I am [colleague]" and hijack their agent) was **removed** — linking is code-only; `_pending_flows` now evicts expired OAuth entries (memory leak); drift→resolution proposals are **decoupled** (own short session, no pooled connection held across the ~45s Claude call, capped 8/run, de-dupe re-checked to close a race); and the Slack keep-alive loop breaks after 3 consecutive `is_connected()` errors instead of spinning forever.
3. **System prompt** = `static persona` (frozen, **prompt-cached**) + learned personality (`preference_learner.get_personality_context`) + a **dynamic live snapshot** (`assemble_context_snapshot` re-runs read tools so the AI starts already briefed). Current time and the snapshot live in the *uncached* dynamic block so they never invalidate the cache.
4. **Model routing** — `classify_command()`:
   - `> 20 words` → **sonnet**.
   - Contains any `COMPLEX_SIGNALS` (59 terms: all/analyze/plan/reassign/email/slack/yes/overdue/assign/create/delete/…) → **sonnet**.
   - Contains any `SIMPLE_SIGNALS` (21 terms: "my tasks"/"team status"/"show me"/"hi"/…) → **haiku**.
   - `<= 6 words` → **haiku**; else **sonnet**. `MODEL_MAP = {haiku: claude-haiku-4-5, sonnet: claude-sonnet-4-5}`.
5. **Prompt caching** — `cache_control` breakpoints on the last tool, the static system block, and the last message block (`_refresh_cache_breakpoint`). Cache reads bill ~0.1×; verified live (e.g. `cache_read=8538`).
6. **Tool-use loop** (`max_iterations=10`): `claude_client.messages.create(model, system=system_blocks, tools, messages)` → if `stop_reason=="tool_use"`, run each tool via `execute_tool` and append `tool_result`s → repeat until `end_turn`. `_log_usage` prints/emits per-call token + cache usage.
7. **Memory**: `load_agent_memory`/`save_agent_memory` persist a text-normalized conversation in `agent_memory` (per company_id+agent_id). On corruption it self-heals (clears and retries).
8. **Preference learning**: every 5 user turns, `maybe_extract_in_background` runs (Ollama) to update the twin.
9. **Real-time**: `glass_brain_queue` carries reasoning lines (`"Agent_ID|[GLASS BRAIN] …"`) drained by `glass_brain_loop` in `main.py` and routed to the right user's WebSocket.

**`execute_tool(tool_name, tool_input, agent_id) -> str`** — a large if/elif dispatch (~110 handlers). It opens its own DB session, emits tool events to the event bus, returns a **string** (never raises to the loop — errors are caught and returned as a clean message), broadcasts `SYNC_REQUIRED` after DB writes (`_broadcast_sync`), and closes the session in `finally`. Date inputs go through `_parse_date` so the AI's free-text dates never crash a `Date` column.

**AI-to-AI negotiation** (`negotiate_peer_help` handler): scores colleagues by workload + skill, then **synchronously calls `run_orchestrator` for each candidate's agent** (their AI checks its own tasks/calendar and replies ACCEPT/DECLINE). On ACCEPT it creates a `PeerRequest` + notifies both parties; if all decline it escalates to the manager. A **thread-local recursion guard** (`_negotiation_local`) prevents a candidate's agent from starting a nested negotiation.

---

## 7. Complete Tool Catalog

Tools are JSON schemas the AI can call; handlers live in `execute_tool`. **Manager = 77 tools, Employee = 36 tools.** The last tools carry the prompt-cache marker.

> **TEAM-LEAD TIER (2026-07-02).** A fourth role: `Employee.system_role == "team_lead"` (promoted/demoted by the manager via the new `set_team_lead` tool, which also syncs `Team.lead_id`). A lead is a normal employee (own tasks, own agent `Employee_{id}`, employee dashboard) whose AI **additionally** gets 18 manager tools **hard-scoped to their own team server-side** (`LEAD_TOOL_NAMES` / `LEAD_EXTRA_TOOLS` in `claude_orchestrator.py`): team-filtered lists (`view_all_tasks`, `search_tasks`, `get_overdue_tasks`, `get_team_status`, `get_workload_summary`, `get_completion_rate`, `view_meetings`, `view_escalations` — filtered via `lead_scope_ids` inside the handlers), target-validated actions (`assign_task`/`reassign_task`/task updates/`get_employee_details`/`resolve_escalation` — any out-of-team target is refused, never silently retargeted), and meetings (`schedule_meeting` attendees must be ⊆ team; reschedule/delete only meetings they created; Meet invites work from the lead's own Google). Role is read from the DB at the `execute_tool` gate — never from model args. Excluded by design: hiring/firing, passwords, company analytics, goals admin, contracts admin, outward-action approvals, other teams' anything. En-route fix: `get_team_status`/`get_workload_summary` now filter `system_role != "manager"` (was `== "employee"`, which would have hidden leads from listings). Verified with an 11-case authorization matrix (scoped lists, denied cross-team writes, blocked manager tools, regular employees unchanged, manager unscoped, demote re-blocks). Frontend lead dashboard = later polish; the AI is the lead's power surface for now.

### Manager tools (78)
**Slack/Email/Calendar:** `post_to_slack`, `list_slack_channels`, `read_slack_channel`, `check_my_emails`, `draft_email_reply`, `send_email`, `check_my_calendar`, `create_calendar_event`, `check_google_connection`
**Tasks:** `view_all_tasks`, `search_tasks`, `get_overdue_tasks`, `assign_task`, `reassign_task`, `update_task_status`, `update_task_priority`, `update_task_due_date`, `update_task_description`, `delete_task`, `add_task_comment`, `view_task_comments`, `add_task_dependency`, `view_task_dependencies`, `add_tag_to_task`
**Projects:** `create_project`, `view_projects`, `update_project_status`, `delete_project`, `get_tasks_by_project`
**People:** `get_team_status`, `get_employee_details`, `search_employees`, `find_employee_by_name`, `add_employee`, `update_employee`, `delete_employee`, `assign_to_team`, `rebalance_team`
**Meetings:** `view_meetings`, `schedule_meeting`, `reschedule_meeting`, `delete_meeting`, `add_meeting_summary`, `add_meeting_transcript`, `create_meeting_action_item`, `view_meeting_action_items`, `convert_action_item_to_task`
> **Google Meet sync (2026-07-01):** `schedule_meeting` now ALSO creates a Google Calendar event **with a Google Meet link** on the organizer's connected account and **emails invites** to every attendee with an `Employee.email` (`sendUpdates=all`). The model passes `start_iso` (exact ISO-8601 local datetime it derives from "tomorrow 2pm" + CURRENT TIME); naive times are interpreted in the organizer's primary-calendar timezone. The Meet link is stored in `Meeting.location` (unless a physical location was given) and the event id in `Meeting.google_event_id`, so `reschedule_meeting` (with `new_start_iso`) **moves** the Google event and `delete_meeting` **cancels** it — attendees get email updates both ways. All Google steps are best-effort: if Google isn't connected/fails, the Nexus meeting is still created and the reply says the Meet step was skipped. Attendees without an email are listed in the reply as not-invited. Helpers: `create_meet_event`/`update_meet_event_time`/`delete_meet_event` in `api/google_services.py` (dict-returning, sanitized errors, `conferenceDataVersion=1`; they release the DB connection **before** the Google HTTP calls and require callers to have committed first). **Privacy:** since `location` can now hold a joinable Meet link, `GET /meetings/` masks `location` for employees who aren't attendees (managers and attendees see it) — `format_meeting(m, viewer=…)` in `api/routers/meetings.py`.
**Peer/Delegation/Escalation:** `view_all_peer_requests`, `create_delegation`, `view_delegations`, `complete_delegation`, `revoke_delegation`, `view_escalations`, `resolve_escalation`
**Analytics:** `get_workload_summary`, `get_overdue_summary`, `get_completion_rate`
**Approvals/Notifications:** `view_pending_approvals`, `approve_action`, `reject_action`, `send_notification`
**Goals:** `create_goal`, `view_goals`, `update_goal_progress`, `link_task_to_goal`
**Admin/Prefs/Drafts:** `set_employee_password`, `save_preference`, `view_preferences`, `draft_idea`, `view_drafts`, `delete_draft`, `promote_draft_to_task`, `generate_daily_briefing`
**Coordination (NEW):** `define_contract`, `view_contracts`, `confirm_dependency_map`
**Knowledge:** `search_knowledge`

### Employee tools (36)
**Tasks:** `get_my_tasks`, `mark_task_complete`, `breakdown_task`, `complete_subtask`, `add_single_subtask`, `add_task_comment`, `view_task_comments`, `view_task_dependencies`
**Meetings:** `get_my_meetings`
**Peer help:** `find_available_colleague`, `find_employee_by_name`, `dispatch_peer_request`, `negotiate_peer_help`, `view_my_peer_requests`
**Goals/Time:** `view_my_goals`, `update_goal_progress`, `start_time_entry`, `stop_time_entry`, `view_my_time_entries`
**Notifications/Prefs/Escalation:** `view_my_notifications`, `mark_notification_read`, `get_my_preferences`, `set_my_preference`, `create_escalation`, `get_my_daily_briefing`
**Google:** `check_my_emails`, `draft_email_reply`, `send_email`, `check_my_calendar`, `check_availability`, `create_calendar_event`, `get_focus_time_suggestions`, `check_google_connection`
**Coordination (NEW):** `define_contract`, `view_contracts`
**Knowledge:** `search_knowledge`

> **Key behavioral rules baked into the system prompts:** always call a tool for current state (never answer counts/status from memory); the **propose-and-wait** rhythm for outward/irreversible actions (state → propose → confirm → then call); `find_employee_by_name` before any peer dispatch; `check_google_connection` before Gmail/Calendar tools.

---

## 8. The AI/Model Routing Layer (ai_router)

`backend/api/ai_router.py` routes one-shot AI calls (NOT the orchestrator's tool loop, which calls Claude directly) to the cheapest capable provider, with graceful fallback.

| Task type | Provider/Model |
|---|---|
| `orchestrator`, `negotiation` | Claude `claude-sonnet-4-5` |
| `simple_command`, `general_draft` | Claude `claude-haiku-4-5` |
| `email_summary`, `email_draft`, `calendar_analysis` | Gemini `gemini-2.5-pro` |
| `daily_briefing`, `chat_summary`, `action_item_detection`, `preference_extraction`, `meeting_summary` | Ollama `qwen2.5:7b` (free, local) |

**Fallbacks:** Ollama → Haiku, Gemini → Haiku, Claude → none. So if Ollama is down, free tasks fall back to paid Haiku (a reason the local models must stay pulled).

---

## 9. API Reference (every route)

Base prefix `/api/v1`. Auth column: **none** (public), **JWT** (`get_current_user`), **mgr** (`require_manager`), **self/mgr** (own resource or manager), **sig** (webhook signature), **token-qs** (token via query string for WebSocket).

### Auth (`/auth`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/setup` | none + SETUP_SECRET | One-time first-manager creation |
| POST | `/auth/login` | none | Login → access + refresh tokens |
| POST | `/auth/refresh` | none (refresh token) | New access token |
| POST | `/auth/logout` | JWT | Invalidate refresh token |
| GET | `/auth/me` | JWT | Current user |
| POST | `/auth/employees/create` | mgr | Create employee |
| POST | `/auth/set-password` | mgr | Reset an employee's password |
| POST | `/auth/change-password` | JWT | Change own password |

### AI / Manager (`/manager`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/manager/command` | JWT | **The AI entry point.** Agent id derived from token. |
| POST | `/manager/trigger-audit` | mgr | Broadcast a manual system audit |
| GET | `/manager/command-history/{agent_id}` | JWT self/mgr | Restore a thread (employee may only read their own) |
| POST | `/manager/speak` | JWT | Edge-TTS audio of text |

### Tasks / Employees / Meetings / Notifications / Analytics / Peer
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/tasks/` | JWT | All tasks (company-scoped) |
| GET | `/employees/` | JWT | All employees (company-scoped) |
| GET/POST/PATCH/DELETE | `/meetings/` `/meetings/{id}` | JWT (read) / mgr (write) | Meetings CRUD |
| GET | `/notifications/{employee_id}` | JWT self/mgr | Recent notifications |
| POST | `/notifications/read/{notification_id}` | JWT | Mark one read (must be yours) |
| POST | `/notifications/read-all/{employee_id}` | JWT self/mgr | Mark all read |
| GET | `/analytics/summary` `/analytics/team/{name}` `/analytics/employee/{id}` | JWT | Analytics |
| GET/POST/PATCH | `/goals/` · `/goals/{id}/progress` | JWT | Goals/OKRs — list (company-scoped), create, update progress |
| GET/POST | `/approvals/` · `/approvals/{id}/approve` · `/approvals/{id}/reject` | mgr | Agent-action approvals — list pending, approve/reject |
| GET | `/integrations/status` | JWT | Which channels are configured (Slack/WhatsApp/Telegram/Google booleans — never the secrets) |
| GET/POST/PATCH/DELETE | `/mcp/` · `/mcp/{id}/toggle` · `/mcp/{id}` | JWT | Connected MCP enterprise apps/data sources (any user; company-scoped; tokens Fernet-encrypted, **never returned** — only `has_token`) |
| GET | `/peer-requests/` | JWT | List peer requests |
| POST | `/peer-requests/{req_id}/respond` | JWT recipient/mgr | Accept/Decline/Complete |

### Files / Chat / Google / Channels / Admin / Internal / WS
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/files/upload` | JWT | Upload + AI-analyze + auto-index to RAG |
| GET | `/files/recent` `/files/{id}` ; DELETE `/files/{id}` ; POST `/files/{id}/execute` | JWT | File intelligence |
| POST | `/chat/ensure-project-channel` | JWT | Get/create a project's channel |
| GET | `/chat/my-channels/{employee_id}` | JWT self/mgr | Channels (+ unread counts) |
| GET | `/chat/{channel_id}/messages` | JWT member/mgr | History (marks read) |
| POST | `/chat/{channel_id}/send` | JWT member/mgr | Send (sender from token) |
| POST | `/chat/{channel_id}/summarize` | JWT member/mgr | Local-LLM TL;DR (posts as AI) |
| GET | `/google/connect/{employee_id}` | none | OAuth redirect (browser) |
| GET | `/google/callback` | none | OAuth callback |
| GET/POST | `/google/status` `/disconnect` `/emails` `/calendar` `/availability` `/focus-time` `/{employee_id}` | JWT self/mgr | Google data |
| POST | `/channels/link` `/verify` `/slack/start-link` ; DELETE `/channels/{id}` ; GET `/channels/my` | JWT | Omnichannel linking |
| POST | `/channels/whatsapp/inbound` `/telegram/inbound` `/telegram/set-webhook` | sig | Inbound webhooks |
| GET | `/admin/metrics` `/health` `/agents` `/recent-errors` | mgr | Observability |
| WS | `/admin/stream` | token-qs (mgr) | Live circuit-board events |
| POST | `/admin/run-briefings-now` `/run-proactive-scan-now` `/run-digests-now` `/run-dependency-mapping-now` | mgr | Manual triggers (demo) |
| POST | `/internal/sync` | none (internal) | Slack bot pings this to broadcast SYNC_REQUIRED |
| WS | `/ws/{employee_id}` | token-qs (subject must match) | Per-user real-time channel |
| GET | `/health`, `/`, `/docs`, `/openapi.json`, `/redoc` | none | Health / API docs |

---

## 10. Data Model (43 tables)

Defined in `backend/database/models.py`. Almost every business table carries `company_id` (tenant key; the pilot runs one company per instance). Grouped:

- **Org:** `companies`, `teams`, `employees`, `project_members` (junction), `meeting_attendees` (junction), `task_tags` (junction).
- **Work:** `projects`, `tasks`, `subtasks`, `task_comments`, `task_dependencies`, `tags`.
- **Meetings:** `meetings` (incl. `google_event_id` — the linked Google Calendar/Meet event, added 2026-07-01; the Meet link itself lives in `location`), `meeting_action_items`.
- **Chat:** `channels` (type: public/private/dm/project/ai), `channel_members` (`last_read_at`), `messages` (type: text/file/ai/system/action_item; `ai_agent_id`; `reply_to_id` self-ref), `message_reactions`, `message_reads`, `message_files`.
- **File intelligence:** `uploaded_files` (`extracted_text`), `file_extractions`.
- **Omnichannel:** `channel_connections`, `channel_message_logs`.
- **Collaboration:** `peer_requests`, `delegations`, `escalations`.
- **Goals/Time:** `goals`, `goal_tasks`, `time_entries`.
- **Notifications:** `notifications` (`type`, `entity_type`, `entity_id`, `is_read`).
- **Auth/OAuth:** `oauth_tokens` (encrypted `access_token`), `app_connections`.
- **AI memory/audit:** `agent_memory` (per company+agent), `audit_logs`, `approval_requests`.
- **Preferences/Drafts:** `employee_preferences`, `manager_profiles`, `manager_drafts`.
- **Autonomy:** `daily_briefings`, `workload_snapshots`.
- **RAG:** `knowledge_embeddings` (`source_type`, `source_id`, `chunk_index`, `content`, `embedding` JSON, `embed_model`, `meta`).
- **Coordination (NEW):** `contracts` (`producer_task_id`, `consumer_task_id`, `name`, `description`, `status` proposed/active/at_risk/broken/fulfilled, `baseline_at`). `proposed` = auto-mapped by `dependency_inference`, awaiting manager confirm (not drift-watched until `active`).

> Full column lists are in `models.py`. **Schema is Alembic-managed as of 2026-07-02** (baseline revision `1f73cc0a0608` = the full current schema; the employees↔teams FK cycle is broken by adding `fk_employees_team_id` after both tables). `create_tables.py` now runs `alembic upgrade head` (fresh DB → full schema; existing DB → pending migrations only; legacy pre-Alembic DB → auto-stamped at baseline first). **To change the schema:** edit `models.py` → `alembic revision --autogenerate -m "..."` → review the generated file (autogenerate is a draft) → run `create_tables.py` everywhere. Never hand-ALTER the database; the old `COLUMN_UPGRADES` list is gone. Migrations in `alembic/versions/` are committed to git (the old ignore rule was removed).

---

## 11. Authentication & Security

- **Login** (`auth.py`): name + password (bcrypt). Returns an **access token** (JWT HS256, 8h) and a **refresh token** (30d, hashed in the DB). `get_current_user` decodes the bearer, loads the active `Employee`. `require_manager` adds a role check.
- **Frontend** (`NexusContext.jsx`): an axios **request interceptor** attaches the bearer to every axios call; a **response interceptor** refreshes once on 401. Raw `fetch()` calls (chat, command-history, analytics) attach the token explicitly. The WebSocket appends `?token=`.
- **Agent identity is derived from the token** on `/manager/command` (the body's `manager_id` is ignored) → no impersonation.
- **Tenant/identity guards:** read routers are company-scoped; per-user endpoints (notifications, google, command-history, chat) enforce self-or-manager; chat enforces channel membership; the WebSocket requires the token subject to match `employee_id`.
- **OAuth + MCP tokens are Fernet-encrypted at rest** via the **multi-key** `api/token_crypto.py` (which `google_auth.py` now reuses): ENCRYPT with the stable `NEXUS_TOKEN_KEY`, DECRYPT with any known key → rotating `JWT_SECRET` never bricks tokens; legacy plaintext / JWT-key rows still decrypt and re-encrypt on next save. Existing rows were migrated onto `NEXUS_TOKEN_KEY` on 2026-06-28; `_load_mcp_servers` now SKIPS a connection whose token won't decrypt (rather than attaching an unauthenticated server that would 401 every AI call).
- **Hardening done:** default-`JWT_SECRET` startup warning; `admin_router` shares `security.py`'s JWT secret; Twilio signature **fails closed** when unconfigured (`TWILIO_ALLOW_UNVERIFIED=1` dev override); `OAUTHLIB_INSECURE_TRANSPORT` only set for http/local redirect URIs.
- **Outward-action approval gate (2026-07-02) — the non-LLM barrier between untrusted content and real-world side effects.** `send_email` and `create_calendar_event`-with-attendees no longer execute when the model calls them: the handler creates a pending `ApprovalRequest` (payload = exact recipient/subject/body or title/attendees) and tells the model it's queued. The send/invite executes ONLY in `api/routers/approvals.py` when a human clicks **Approve & Send** on the Approvals page (execution failure → status `failed`, honestly surfaced; requester gets a Notification either way). The AI's `approve_action` tool **refuses** to approve these types (a prompt-injected "approve my pending requests" can't defeat the gate; AI rejection stays allowed). Kill-switch: `NEXUS_REQUIRE_APPROVAL=0` (demos only). Tool descriptions + both system prompts updated: the model drafts with the user, calls the tool, reports "awaiting approval" — never claims "sent". Attendee-less calendar events (own calendar) stay direct.
- **AI audit trail (2026-07-02):** every model-invoked tool execution writes an `AuditLog` row (`action="ai_tool:<name>"`, actor parsed from `agent_id`, truncated input + result) via `_audit_tool_execution` at the tool-loop call site — own short session, never raises. "What did the AI do last Tuesday" is now a query. (The context-snapshot's fixed pre-briefing reads are deliberately not audited — pure noise.)
- **Untrusted-content framing (2026-07-02):** `read_recent_emails` and `draft_email_reply` wrap email bodies/threads in explicit `<untrusted_emails>`/`<untrusted_thread>` blocks with never-follow-instructions framing (both in the Gemini prompt and in the summary returned to the orchestrator), and the summarizer is told to flag AI-directed instructions inside emails as possible injection.
- **Still open:** `SETUP_SECRET` is on its default (one-time first-manager only); full multi-tenant `company_id` scoping inside the orchestrator tools is deferred (the pilot is single-tenant-per-instance).

---

## 12. RAG — Semantic Memory

File: `backend/rag.py`. Table: `knowledge_embeddings`.

- **Embeddings:** local Ollama `nomic-embed-text` (768-dim, free). `EMBED_MODEL`/`EMBED_DIM` are env-overridable.
- **Storage:** plain Postgres JSON column; **no pgvector** (it isn't available on the target Windows/PG18 box and compiling it was deemed too fragile). Similarity is a numpy brute-force cosine — sub-100ms at pilot scale. The single place to swap for a real vector DB is `_top_k`.
- **Tenant-safe:** every `search` filters by `company_id` **and** `embed_model` (dimension safety); off-dimension vectors are rejected at write and skipped at read.
- **Functions:** `embed_text`, `chunk_text` (1200 chars / 200 overlap), `index_content` (idempotent per source — delete-then-insert), `index_async` (daemon-thread fire-and-forget, used by upload + chat hooks), `search(company_id, query, k, source_types, min_score)`, `backfill(company_id)`.
- **What's indexed:** uploaded files' extracted text, task content (backfill), and chat messages ≥25 chars (auto-ingested on send) + AI digests. CLI: `python rag.py backfill | search "<q>" | stats`.
- **Consumed by:** the `search_knowledge` tool in the orchestrator.
- **Operational note:** if `nomic-embed-text` is missing from Ollama, search/ingest silently fail — `main.py` warns at startup; re-pull with `ollama pull nomic-embed-text`.

---

## 13. The Coordination Layer (digest, drift, contracts)

The differentiated vision. File: `backend/project_digest.py` (+ the contract tools/model).

- **Daily Project Digest** (`build_project_digest` → `_narrate` → `post_ai_message`): for each project, gathers real activity already in the DB — tasks completed today, overdue, due-soon, **task dependencies in play**, open peer requests, and **contracts** — then `_narrate` turns the facts into a natural-language briefing via Ollama `qwen2.5:7b` (deterministic fallback if Ollama is down), and posts it into the project's chat channel as an AI message (`ai_agent_id="Nexus Daily"`), broadcast live + indexed to RAG.
- **Dependency-drift alerts** (`run_drift_alerts`): for every **cross-person** `TaskDependency`, it notifies the **downstream owner** when the upstream task is overdue ("at risk") or just completed ("you're unblocked"). De-duped (a new alert only fires when the state/message changes). Pushed live via `notifier.send_notification`.
- **Contract drift** (same function): a **contract** is the interface promise between a producer task and a consumer task, with `baseline_at`. When `producer.updated_at > baseline_at` (the producer changed *after* the interface was agreed) and the consumer isn't done, the contract flips to `at_risk` and the consumer's owner is alerted *"the interface changed — re-check before integrating."* Surfaced in the digest with an ⚠️ AT RISK flag.
- **Resolution engine (NEW 2026-06-24, v1) — closes the loop:** on the first flip to `at_risk`, `_propose_and_surface_fix` calls `resolution_engine.propose_resolution(contract_id)` — Claude (sonnet-4-5) proposes the single **cheapest correct fix** (`{summary, fix, rationale, effort, who}`) — and surfaces it as a pending `ApprovalRequest(action_type="resolve_contract_drift")` (de-duped one-per-contract). It renders as a readable proposal card on the **Approvals page** for manager approve/reject. **v1 = propose + human applies (no auto-execute — a later, riskier stage).** Verified end-to-end (drift → at_risk → real Claude fix as an approval); `test_coordination` 3/3.
- **Tools:** `define_contract(producer_task_id, consumer_task_id, name, description)` and `view_contracts(project_id?, task_id?)` (both manager + employee).
- **Triggers:** `run_all_digests(force=True)` runs both digests **and** drift alerts. Fired by the scheduler (Mon-Fri 17:00 UTC) and on demand via `POST /api/v1/admin/run-digests-now` (manager) — surfaced as the **"Post Digest"** button in the chat header.
- **Division of labor:** chat channel = the *shared, durable, RAG-indexed* record; notifications = the *directed, per-person* tap on the shoulder. Future MCP simply swaps the input (DB facts → GitHub/Figma/Docs diffs) behind this same, proven pipeline.

---

## 14. Real-Time / WebSocket Protocol

File: `backend/api/ws_manager.py` (`notifier` = `ConnectionManager`).

- **Endpoints:** `/api/v1/ws/{employee_id}?token=…` (per-user; token subject must match) and `/api/v1/admin/stream?token=…` (manager circuit board).
- **On connect**, the server auto-joins all of the employee's chat channels' rooms.
- **Outbound message formats** (plain text with a prefix the frontend parses in `NexusContext.jsx`):
  - `SYNC_REQUIRED` — refetch dashboard data (sent after any DB write).
  - `NOTIF:{json}` — real-time notification push (bell).
  - `CHAT:{channel_id}|{json}` — new chat message (renders in chat / bumps unread).
  - `THOUGHT:{agent_id}|{text}` — Glass Brain reasoning / system audit (routed to the matching user).
  - `MEETING:{meeting_id}|{json}` — meeting events (stub on the frontend).
- **Rooms:** per-channel and per-meeting member sets; `send_chat_message` broadcasts to a channel's room (optionally excluding the sender). Dead connections are pruned on failed send.
- **Concurrency (hardened 2026-06-28):** guarded by a plain **`threading.Lock`** (NOT `asyncio.Lock`) that protects only the in-memory registries and is **never held across an `await`** — targets are snapshotted under the lock, sends happen outside it. The previous `asyncio.Lock` was bound to the main loop while `_broadcast_sync`/`_broadcast_chat_async`/notif/negotiation push from background threads via `asyncio.run()` (a *different* loop) → awaiting it cross-loop silently dropped `SYNC_REQUIRED`/chat/notif (RuntimeError swallowed) or hung forever (leaking a thread). Also: `disconnect(employee_id, websocket)` is now **identity-aware** (a stale socket's teardown can't evict the user's reconnected socket); and manager-only telemetry (glass-brain for `Manager_*`, negotiation/rebalance/deadline reports) now routes via **`broadcast_to_managers`** (a tracked `_managers` set) instead of `broadcast` — it no longer leaks to every connected employee.
- **Sync from background threads:** orchestrator/digest use a daemon-thread `asyncio.run(...)` helper (`_broadcast_sync` / `_broadcast_chat_async`) to push from non-async contexts.

---

## 15. Background Jobs & Schedulers

Started in `main.py`'s `lifespan`:
- **`proactive_agent_loop`** (in main) — hourly: broadcasts an audit line to the manager if there are open tasks.
- **`glass_brain_loop`** (in main) — drains `glass_brain_queue` → routes reasoning to the right user's WS.
- **`negotiation_engine.run_forever()`** — every 30 min: AI workload rebalancing (also fired on demand by `rebalance_team`). **Runs in its OWN daemon thread (2026-06-28)**, not `asyncio.create_task` on the main loop — its synchronous ~45s multi-agent Claude calls would otherwise freeze the whole event loop (every HTTP + WS request) for minutes each cycle. (Same pass: Gmail/Calendar/Gemini calls got explicit timeouts so a hung upstream can't exhaust the thread pool; Google token refresh self-heals on `invalid_grant` by clearing the row so it reports disconnected instead of erroring forever; the RAG startup probe moved off the boot path.)
- **`autonomous_briefings.start_scheduler()`** — APScheduler, Mon-Fri morning: per-person briefings (in-app + WhatsApp/Telegram).
- **`proactive_engine.start_proactive_scheduler()`** — every 15 min: deterministic overdue/deadline/overload alerts.
- **`project_digest.start_digest_scheduler()`** — Mon-Fri 17:00 UTC: digests + drift alerts.
- **`dependency_inference.start_mapping_scheduler()`** — every 30 min: auto-maps any project with ≥2 tasks and no contracts into **proposed** interface contracts + posts the map to its channel; manager confirms to activate drift-watching.
- **`slack_bot.start_in_background()`** — Slack Socket-Mode listener thread (no-op if tokens missing).
- **RAG health check** — warns if `nomic-embed-text` isn't responding.

Manual demo triggers (manager-only): `/admin/run-briefings-now`, `/admin/run-proactive-scan-now`, `/admin/run-digests-now`, `/admin/run-dependency-mapping-now`, `/manager/trigger-audit`.

---

## 16. External Integrations

- **Claude (Anthropic):** orchestration tool loop + complex single-shots. Models `claude-sonnet-4-5` / `claude-haiku-4-5`. Prompt caching enabled.
- **Gemini (Google):** `gemini-2.5-pro` for Gmail/Calendar reasoning (`google_services.py`).
- **Ollama (local, free):** `qwen2.5:7b` (chat summaries, digest narration, briefings, preference extraction) and `nomic-embed-text` (RAG). Must be running with both models pulled.
- **Google Workspace:** OAuth2 (`google_auth.py`), encrypted tokens; Gmail read/draft/send, Calendar read/availability/create/focus-time, **and Google Meet meetings (2026-07-01)** — `schedule_meeting` auto-creates a Meet-linked Calendar event and emails attendee invites; reschedule/cancel in Nexus move/cancel the Google event (see §7). `get_credentials` now propagates the stored `token_expiry` into the Credentials object, so expired access tokens refresh proactively and a dead refresh token (e.g. Google's **7-day testing-mode expiry**) self-heals: the stored row is deleted and the account honestly shows "not connected" until the user reconnects. `/connect` + `/callback` are public (browser OAuth); data endpoints require auth. The AI Gmail/Calendar tools (`GOOGLE_PERSONAL_TOOLS` in `claude_orchestrator.py`) always act on the **caller's own** account — `employee_id` is forced to the caller's real id (the manager is resolved via `system_role=='manager'`, since `Manager_1` doesn't encode it). Fixed 2026-06-24: the manager's "check my emails" no longer wrongly reports "not connected".
- **Slack:** Socket-Mode bot (`slack_bot.py`) — DMs route to the user's agent; tools `post_to_slack`/`list_slack_channels`/`read_slack_channel`. **Linking:** Connections → Slack → *Connect* generates a 6-digit code (`/channels/slack/start-link`, 10-min expiry, stored in `channel_connections`); the user DMs it to the bot, which calls `complete_slack_link`. Fixed 2026-06-25: the DM handler now strips Slack markup (a pasted `*732305*` arrives **bold**) and pulls a standalone 6-digit run before matching — previously `isdigit()` failed on the asterisks so the code was never checked and the user got the generic "I don't recognize you" reply. It also now tells the user when a code is expired/unknown instead of silently asking for their name. Stale legacy `slack_links.json` entries were cleared (DB link is authoritative; JSON is fallback-only). **Privacy boundary — DM vs channel (fixed 2026-06-25):** the personal agent runs **only in DMs**. `handle_mention` (channel `@nexus`) previously called `route_to_agent(mentioner, …)` → this ran the mentioner's `Employee_{id}` agent, loading their **private DM memory** and personal tools (emails/tasks/calendar) and posting the reply **publicly in the channel** — a personal-data leak. It now never runs the personal agent in a channel. (The `/nexus` slash command is safe — Slack `respond()` is ephemeral, visible only to the invoker.) **Channel/team assistant — DONE 2026-06-25:** `@nexus` in a channel runs the **Team/Coordinator orchestrator role** (`run_orchestrator("Team_{channel}", …)`), NOT the personal agent. `handle_mention` builds `extra_context` from the channel's already-public info — an auto-detected **roster** (channel members → linked employees → their open tasks, via `_slack_channel_project_context`) + the recent transcript — and passes it in. So the bot knows who's in the channel, what they're working on, the project/dependency/contract state, and can post digests/audits and escalate blockers — while the server-side `caller_is_team` gate makes it structurally unable to touch private data or take manager/write actions. Replies in-thread when mentioned in one, addresses people by name. On any failure it **falls back** to the lightweight conversational reply (`channel_assistant.build_channel_reply`) so the channel never gets a hard error. Verified live: surfaced a real graphics→announcement dependency risk + cheapest fix, and refused to show an individual's emails/calendar. **Scopes:** public channels work today (`channels:history`/`channels:read`/`users:read`/`conversations.members` granted); **private channels need `groups:history` added** (OAuth & Permissions → reinstall). The `Employee_{id}` (personal, DM-only) vs `Team_{channel}` (coordinator, channel) split is the privacy boundary. **Next:** wire the same Team role into Nexus's own group chat; optional channel→project explicit override.
- **Twilio (WhatsApp/SMS):** `twilio_client.py` — send + fail-closed signature verification; inbound webhook at `/channels/whatsapp/inbound`.
- **Telegram:** **stub only** (`telegram_client.py`) — paths degrade gracefully; not wired for the pilot.
- **LiveKit / edge-tts:** meeting room SDK (declared) + Edge TTS for `/manager/speak` voice output.
- **MCP connectors (NEW 2026-06-24) — DONE end-to-end:** any user can connect enterprise apps over MCP from the Connections page (GitHub/Notion/Linear/Jira/Postgres/custom) — company-scoped, token Fernet-encrypted (`MCPConnection` · `api/routers/mcp.py` · `api/token_crypto.py`). `run_orchestrator` feeds enabled connections into the Claude calls via Anthropic's remote connector: `_load_mcp_servers()` → `claude_client.beta.messages.create(betas=["mcp-client-2025-11-20"], mcp_servers=…, tools=…+mcp_toolset)`, `pause_turn` handled, `serialize_message_content` preserves MCP blocks (`model_dump`). **Conditional** — no connection = the orchestrator is byte-identical to before. MCP attached upgrades the model to sonnet (Haiku under-uses MCP tools). **Verified live** reading the founder's real GitHub repos. Needs a **publicly reachable** MCP URL (GitHub's hosted server works; local Postgres does not). **2026-06-28:** MCP is now **role-gated** — NOT attached to the Team (public-channel) tier; only the manager + employee agents (authenticated, private DM/web) get MCP. `_load_mcp_servers` also **skips** any connection whose token won't decrypt (rather than attaching an unauthenticated server that would 401 *every* AI call). ⚠️ Still open (deferred): any authenticated user can add/edit company connections, MCP tools bypass `execute_tool` for the manager/employee tiers, and `_load_mcp_servers` still filters by hardcoded `DEFAULT_COMPANY_ID=1` (a silent no-op for company≠1) — all fine for the single-tenant pilot; tighten (per-user `owner_id` + approval, thread `company_id`) for multi-tenant.

---

## 17. Frontend

- **State & logic** live in `context/NexusContext.jsx` (a single React context): auth + token refresh, the WebSocket (auto-reconnect with backoff + 60s polling fallback), dashboard data fetching, the AI command sender (`sendCommandToNexus`), voice (Web Speech in + TTS out), and all WS message parsing (`SYNC_REQUIRED`/`NOTIF`/`CHAT`/`THOUGHT`/`MEETING`).
- **Shell** (`App.jsx`): role-based sidebar (manager vs employee sections), top bar with notification bell + system-audit button, task detail modal, and a tab-based page router. Tabs persist in `sessionStorage`.
- **Auth storage:** `sessionStorage` (`nexus_user`, `nexus_access_token`, `nexus_refresh_token`).
- **Pages:** see §5. The fully-wired, demo-important ones are **Commands** (AI + voice + Glass Brain + file upload), **ChatPage** (channels + unread + digest button), **Dashboard**, **Analytics**, **TeamMatrix**, **AdminPage** (circuit board), **ConnectionsPage** (the unified integration hub) — plus Goals, Meetings, Settings, Approvals, and GoogleWorkspace. **As of 2026-06-24 every sidebar page is wired to real data; no "Coming soon" placeholders remain.** (The old Integrations page was merged into Connections.)
- **Backend URL:** `http://${window.location.hostname}:8000`.

---

## 18. Event Bus, Admin & Observability

- **`event_bus.py`:** an in-process pub/sub (`event_bus`) plus cost estimation (`estimate_cost`, cache-aware: reads 0.1×, writes 1.25×) and convenience emitters (`emit_agent_thinking`, `emit_tool_completed`, `emit_cost`, `emit_negotiation_*`, `emit_error`, …). The orchestrator and engines emit; the admin circuit board subscribes.
- **`admin_router.py`:** manager-only `/admin/metrics`, `/admin/health`, `/admin/agents`, `/admin/recent-errors`, and the live `/admin/stream` WebSocket that powers the animated circuit board (cost, agent activity, negotiations, errors). WS auth is via `?token=` (decoded with the shared `JWT_SECRET`).
- **Glass Brain:** live AI reasoning streamed to the user's chat/commands view (`THOUGHT:` WS messages).

---

## 19. Testing

`backend/tests/` (pytest). Integration tests against the real local Postgres + Ollama (seed first).
- `conftest.py` — fixtures: `client` (FastAPI TestClient, lifespan not started), `db`, `people` (a manager + two employees + their tokens), `mgr_headers`/`emp_headers`.
- `test_auth.py` — protected endpoints reject no-token, work with a token, enforce identity (notifications, command-history), admin endpoints manager-only, and the WebSocket token gate.
- `test_rag.py` — embedding dimension, semantic (not keyword) retrieval, tenant isolation, idempotent re-index. **Skips** if `nomic-embed-text` is unavailable.
- `test_coordination.py` — digest builds, dependency-drift fires + dedups, and the full contract define→change→drift→at_risk flow.

Run: `venv\Scripts\python.exe -m pytest tests/ -q`. (`pytest` is in `requirements.txt`.)

---

## 20. Known Gaps & Deferred Work

- **Multi-tenancy (Tier-4, deferred):** the orchestrator tools mostly use a hardcoded `DEFAULT_COMPANY_ID=1`; ~dozens of `execute_tool` queries aren't `company_id`-scoped. Safe for the **single-tenant-per-instance pilot**; required before true multi-tenant SaaS. Login also matches employee name globally (collision risk at multi-tenant).
- **SETUP_SECRET / JWT_SECRET** — now **fail-closed** (set both, per instance; see the Environment section). Fixed 2026-06-23.
- **Pilot-readiness audit (2026-06-23).** 8 blockers found. **Fixed:** B3 secrets fail-closed; B5 WebSocket rejects refresh tokens; B8 Claude-client timeout; **B1 AI-tool authorization** — `execute_tool` now ignores model-supplied ids: for employee callers it forces self-identity fields to the authenticated `agent_id` and hard-blocks the 63 manager-only tools (closes the "act as employee 3 / read their Gmail" impersonation hole); **B2** single-tenant company guard at bootstrap. Also fixed: **B6** (AI `add_employee` now sets a temp password), **B7** (`GET /auth/status` + first-run Setup screen in `Login.jsx`), **B8-finish** (`/manager/command` async + rate-limited + threadpool), **B4** (Google `/connect` now auth-required + returns `{auth_url}`; OAuth state is random/single-use/expiring and bound server-side to the employee — closes account-linking/CSRF). **All 8 pilot blockers are now closed & verified (compiles, imports, frontend builds, 14/14 pytest).** Also fixed the top **should-fix**: **task-ownership checks** — `mark_task_complete` / `breakdown_task` / `add_single_subtask` / `complete_subtask` now reject an employee acting on a task they don't own. Remaining should-fix: login name-collision (deferred — login by unique email / reject ambiguous name match), run uvicorn `--workers 1`, soft-delete+AuditLog on cascades, password strength, refresh rotation, wire the Approvals page, auto create_all on boot. Full list in memory `roadmap-status`. **To go live: restart backend (new secrets+code), redeploy frontend, everyone re-logs-in.**
- **Robustness / hardening pass (2026-07-01).** Second sweep after a full system audit — no data leaks, no broken pipes, bounded resource use. All verified: 14/14 pytest, every edited module imports, frontend builds (880 modules), backend boots healthy (db ok, Slack connected, WS routing). Fixed:
  - **Real-time survives token expiry (HIGH).** `NexusContext.jsx` WebSocket used to reconnect forever with the *same* expired access token (server closes 1008 / handshake fails 1006), so live updates silently died after the ~8h token lifetime. The socket now refreshes the token before reconnecting on an auth-close, via a shared **de-duplicated** `refreshAccessToken()` (a burst of 401s / a WS-close racing an HTTP 401 now triggers ONE `/auth/refresh`, not N competing rotations). Reconnect timer is tracked (no stacked/duplicate sockets); `markNotificationRead` only decrements the badge when the notification was actually unread.
  - **$0 background jobs stay $0.** `ai_router.call(..., allow_fallback=False)`: preference extraction (an Ollama-only job) no longer silently escalates to paid Haiku when Ollama is down — it skips. Concurrent extraction for the same agent is now guarded (per-agent in-flight set) so overlapping threads can't clobber each other's `key_preferences` merge.
  - **Bounded background work.** RAG `index_async` runs on a bounded `ThreadPoolExecutor` (`RAG_INDEX_WORKERS`, default 2) instead of an unbounded daemon-thread-per-message; `auto_map_unmapped_projects` maps at most `DEP_MAP_MAX_PER_RUN` (default 5) projects per pass so a backlog can't fire an unbounded burst of Sonnet calls (deferral is logged, never silent).
  - **AI-to-AI recursion guard closed in the background path.** The negotiation engine now sets the same thread-local `_negotiation_local.active` guard the tool path uses, so a candidate agent's orchestrator can't spawn a nested negotiation → unbounded fan-out.
  - **Interactive knowledge search can't hang.** `search_knowledge` fast-fails on a 2s backend probe (`rag.backend_available()`) and caps the query embed at 8s (`query_timeout`), instead of blocking the user's turn for the full 60s Ollama timeout.
  - **External-call timeouts + no PII leaks.** Twilio outbound sends run through a `TwilioHttpClient(timeout=…)` (no blind retry — sends are non-idempotent); inbound webhook signature is verified against **multiple candidate public URLs** (proxy/ngrok-forwarded + configured base), so a base-URL mismatch no longer rejects every real webhook (safe: each candidate still needs a valid HMAC). Google/Gemini raw exception strings (which carry request URLs, response bodies, emails, token fragments) are now sanitized by `_safe_err()` — full error logged server-side, only a generic message + HTTP status returned to the model/user. Spreadsheet preview reads only the first 20 rows (`nrows`) instead of loading a whole multi-GB file; `my-channels` replaced its per-channel N+1 (2 queries × N) with two batched queries (Postgres `DISTINCT ON` + one grouped unread count).
  - **Deferred (intentionally):** narrowing the negotiation cycle's DB-session scope — already mitigated (own thread, `pool_pre_ping`, `finally: db.close()`); a full refactor risks the ORM object lifecycle for little gain.
- **Google Meet feature review (2026-07-01).** A 3-lens adversarial review (14 agents) of the new Meet integration confirmed 9 unique issues; **8 fixed same-day:** idempotent `COLUMN_UPGRADES` in `create_tables.py` (missing-migration HIGH); timezone-safe `token_expiry` (google-auth's naive-UTC expiry was being shifted by the server's UTC offset through the timestamptz column, silently defeating proactive refresh); token maintenance in `get_credentials` moved to a **private session** (never commits/rolls back the caller's transaction); self-heal narrowed to **permanent** failures only (`invalid_grant` — a network blip no longer deletes a good token); Meet helpers release the DB connection before Google HTTP + roll back on error (no pinned pool connections, no poisoned shared session); `reschedule_meeting` commits the Nexus change before any Google call; the Google event id is persisted with a fresh-session fallback + logged (no orphaned events); `GET /meetings/` masks the Meet link from non-attendee employees. **Update 2026-07-02 — the outward-action approval gate SHIPPED (see §11):** `send_email` and attendee-bearing `create_calendar_event` are now hard-gated behind human approval; the AI cannot self-approve them. Still direct (accepted): `schedule_meeting`'s own invites (attendees restricted to company `Employee.email` rows — no arbitrary-address exfiltration) and reschedule/cancel updates for those meetings.
- **Deploy rails (2026-07-02).** The app is now cloud-deployable: `backend/Dockerfile` (python:3.12-slim + libmagic, healthcheck, runs `create_tables.py` migrations then a single uvicorn worker on `$PORT`; command inlined via `sh -c` so Windows CRLF can never break the container), `backend/.dockerignore`, root `docker-compose.yml` (Postgres 16 + backend, for self-hosting), and `backend/.env.example` documenting every env var. **RAG works without Ollama:** `EMBED_PROVIDER=auto|ollama|gemini` in `rag.py` — auto prefers free local Ollama, falls back to hosted **Gemini embeddings** (`gemini-embedding-001`, `output_dimensionality=768` = same dim; rows are tagged per model and search filters on it, so a provider switch just needs `python rag.py backfill`). Verified live: forced-Gemini index+search round-trip scored 0.72; local auto still resolves to Ollama. **Frontend is deploy-portable:** all 7 hardcoded `hostname:8000` constants replaced by `src/config.js` (`VITE_BACKEND_URL` build-time env, ws(s) URL derived; falls back to `hostname:8000` for local dev). CORS via `ALLOWED_ORIGINS` env (already existed). Deploy shape: backend container on Railway/Render + managed Postgres, frontend on Vercel with `VITE_BACKEND_URL`, `EMBED_PROVIDER=gemini`.
- **CI + migrations rails (2026-07-02).** GitHub Actions CI added (`.github/workflows/ci.yml`): a fresh Postgres 16 service → `create_tables.py` (Alembic) → `seed_demo.py` → `pytest` (11 pass, 3 RAG tests auto-skip without Ollama; no external AI calls — dummy keys), plus a frontend production build. The exact pipeline was dry-run locally against a scratch DB before committing the workflow. Schema management moved to **Alembic** (see §10) — baseline `1f73cc0a0608`, live DB stamped, `create_tables.py` rewritten, migrations now committed to git. Also fixed en route: the root `.gitignore` was **UTF-16-encoded and silently broken** (git couldn't parse it — `*.db`, `venv/`, `backend/uploads/` weren't actually ignored; secrets were only saved by a second ASCII `backend/.gitignore`) — rewritten as UTF-8; and `numpy` + `apscheduler` were **missing from requirements.txt** (worked locally only as transitive/preinstalled packages — a cold deploy would crash RAG and silently disable every background scheduler).
- **Telegram** is a stub.
- **Chat RAG granularity:** messages are indexed per-line, not per-thread (lower retrieval value); de-index on edit/delete isn't wired.
- **Next coordination steps (autonomous integration loop):** (1) ✅ **Done** — `dependency_inference.py` now maps dependencies automatically: a 30-min background pass (or `/admin/run-dependency-mapping-now`) infers each project's dependency graph + contracts and writes them as **proposed** contracts, posts the map to the project channel, and notifies the manager, who confirms with one sentence (`confirm_dependency_map`) to flip proposed→active and start drift-watching. (2) Upgrade `negotiation_engine` from workload-only/ACCEPT-substring to a structured resolution engine: on drift, each agent estimates its side's fix cost + correctness → propose the cheapest *correct* fix to a human. (3) MCP (GitHub/Figma/Docs) feeds real artifacts so detection is schema-level, not description-level. Guardrails: *correct before cheap*, *advise before dictate*.
- **Every sidebar page is now wired to real data** — the last two (GoogleWorkspace + Integrations) were done 2026-06-24, backed by the new `/integrations/status` endpoint. `placeholder_pages.jsx` is a legacy, unrouted stub file.

---

## 21. How to Keep This Manual Updated

**This file must be updated in the same change as any code change.** Checklist when you touch the code:
- **New/changed API route** → update §9 and (if relevant) §5.
- **New/changed orchestrator tool** → update §7 (and §13 if it's coordination).
- **New/changed DB table or column** → update §10.
- **New module/file** → update §5.
- **New integration, model, or routing change** → update §4, §8, §16.
- **New background job / scheduler** → update §15.
- **Auth/security change** → update §11.
- **Frontend page/component change** → update §5 and §17.
- Bump **Last updated** at the top.

Authoritative inventories (routes, tables, tool names, routing tables) can be regenerated by importing `main.app.routes`, `database.models.Base.metadata.tables`, and `api.claude_orchestrator.MANAGER_TOOLS/EMPLOYEE_TOOLS` — prefer that over editing lists by hand, to avoid drift.
