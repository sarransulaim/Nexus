"""
preference_learner.py — Behavioral Digital Twin
=================================================
After every N conversation turns, runs a background job that:
  1. Reads the agent's last 50 messages
  2. Uses local Ollama to extract behavioral patterns
  3. Saves them as structured preferences in DB
  4. Next session, those preferences are injected into the system prompt

Result: the AI sounds more like the user over time. It learns:
  - Communication style (concise vs detailed, formal vs casual)
  - Decision-making patterns (data-driven, gut, collaborative)
  - Recurring preferences (always Mondays, always confirms first, etc.)
  - Topics they care about

Storage:
  - Employee preferences  → EmployeePreference table
  - Manager preferences   → ManagerProfile table

Runs as a background task after each conversation. Costs $0 (uses Ollama).
"""

import json
import logging
import threading
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from database.core import SessionLocal
from database.models import AgentMemory, EmployeePreference, ManagerProfile
from api.ai_router import ai_router, TaskType

log = logging.getLogger("nexus.learner")

# Run extraction every N user turns (not every save — that's too often)
EXTRACTION_INTERVAL = 5


# ═══════════════════════════════════════════════════════════════
# EXTRACTION PROMPT
# ═══════════════════════════════════════════════════════════════

EXTRACTION_SYSTEM_PROMPT = """You are a behavioral analyst studying how a person communicates and makes decisions.

You will read a transcript of their conversations with their personal AI assistant.
Your job is to identify patterns that would help the AI serve them better next time.

Output ONLY a JSON object. No prose. No markdown. Just JSON.

Format:
{
  "communication_style": "concise" | "detailed" | "casual" | "formal" | "direct",
  "decision_pattern":    "data-driven" | "intuitive" | "consensus-seeking" | "fast" | "deliberate",
  "key_preferences": [
    "specific preference 1 (under 100 chars)",
    "specific preference 2 (under 100 chars)"
  ],
  "topics_of_interest": ["topic1", "topic2"],
  "communication_quirks": "1 short sentence describing how they write"
}

Examples of good "key_preferences":
  "always asks for confirmation before sending email"
  "prefers morning meetings before 11am"
  "breaks all tasks into 3-5 subtasks"
  "skips small talk, gets to the point"

Examples of bad "key_preferences" (too generic):
  "is professional"
  "uses the system"
  "likes efficiency"

If the conversation is too short to extract real patterns, return:
{"insufficient_data": true}
"""


# ═══════════════════════════════════════════════════════════════
# EXTRACTION LOGIC
# ═══════════════════════════════════════════════════════════════

def extract_preferences_sync(agent_id: str, company_id: int):
    """
    Synchronous extraction — called from a background thread.
    """
    db = SessionLocal()
    try:
        record = db.query(AgentMemory).filter(
            AgentMemory.agent_id   == agent_id,
            AgentMemory.company_id == company_id,
        ).first()

        if not record or not record.memory_json:
            return

        try:
            messages = json.loads(record.memory_json)
        except Exception:
            log.warning(f"Could not parse memory for {agent_id}")
            return

        # Need at least 4 messages to extract anything useful
        user_turns = sum(1 for m in messages if m.get("role") == "user")
        if user_turns < 4:
            print(f"⚠️  {agent_id}: too few user turns in memory ({user_turns}/4) — need conversation to be longer")
            return

        # Build transcript
        transcript_lines = []
        for m in messages[-30:]:
            role = m.get("role", "?").upper()
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                transcript_lines.append(f"{role}: {content[:400]}")
        transcript = "\n\n".join(transcript_lines)

        if not transcript.strip():
            print(f"⚠️  {agent_id}: empty transcript after filtering")
            return

        print(f"🧠 Calling Ollama for preference extraction... ({len(transcript)} chars)")

        # Call local Ollama for extraction (free)
        try:
            response = ai_router.call(
                task_type=TaskType.PREFERENCE_EXTRACTION,
                prompt=f"Conversation to analyze:\n\n{transcript}",
                system=EXTRACTION_SYSTEM_PROMPT,
                max_tokens=1024,
            )
            print(f"🧠 Ollama responded: {response[:200]}...")
        except Exception as e:
            print(f"⚠️  Extraction LLM call failed: {e}")
            return

        # Parse JSON from response
        prefs = _parse_extraction_response(response)
        if not prefs or prefs.get("insufficient_data"):
            print(f"⚠️  {agent_id}: parser returned empty or insufficient_data")
            return

        print(f"🧠 Parsed preferences: {list(prefs.keys())}")

        # Save to DB
        _save_preferences(db, agent_id, company_id, prefs)
        print(f"✅ Preferences saved to DB for {agent_id}")

    except Exception as e:
        print(f"❌ Preference extraction failed for {agent_id}: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


def _parse_extraction_response(text: str) -> dict:
    """
    Local models sometimes wrap JSON in markdown or add prose.
    Extract the JSON object.
    """
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    # Find first { and last }
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    json_str = text[start:end + 1]
    try:
        return json.loads(json_str)
    except Exception:
        log.warning(f"Could not parse extracted JSON: {json_str[:200]}")
        return {}


def _save_preferences(db: Session, agent_id: str, company_id: int, prefs: dict):
    """
    Save extracted preferences to the right table.

    Manager prefs → ManagerProfile (company-wide)
    Employee prefs → EmployeePreference (per-employee)
    """
    is_manager = agent_id.startswith("Manager_")

    def upsert(key: str, value: str):
        if is_manager:
            existing = db.query(ManagerProfile).filter(
                ManagerProfile.company_id    == company_id,
                ManagerProfile.preference_key == key,
            ).first()
            if existing:
                existing.preference_value = value
            else:
                db.add(ManagerProfile(
                    company_id=company_id,
                    preference_key=key,
                    preference_value=value,
                ))
        else:
            # Employee — extract employee_id from "Employee_5"
            try:
                emp_id = int(agent_id.split("_")[1])
            except Exception:
                return
            existing = db.query(EmployeePreference).filter(
                EmployeePreference.employee_id == emp_id,
                EmployeePreference.pref_key    == key,
            ).first()
            if existing:
                existing.pref_value = value
            else:
                db.add(EmployeePreference(
                    employee_id=emp_id,
                    pref_key=key,
                    pref_value=value,
                ))

    # Save each extracted dimension
    if prefs.get("communication_style"):
        upsert("communication_style", prefs["communication_style"])
    if prefs.get("decision_pattern"):
        upsert("decision_pattern", prefs["decision_pattern"])
    if prefs.get("communication_quirks"):
        upsert("communication_quirks", prefs["communication_quirks"])

    # Key preferences — store as JSON list
    if prefs.get("key_preferences"):
        existing_list = []
        if is_manager:
            existing_row = db.query(ManagerProfile).filter(
                ManagerProfile.company_id     == company_id,
                ManagerProfile.preference_key == "key_preferences",
            ).first()
            if existing_row and existing_row.preference_value:
                try:
                    existing_list = json.loads(existing_row.preference_value)
                except Exception:
                    existing_list = []
        else:
            try:
                emp_id = int(agent_id.split("_")[1])
                existing_row = db.query(EmployeePreference).filter(
                    EmployeePreference.employee_id == emp_id,
                    EmployeePreference.pref_key    == "key_preferences",
                ).first()
                if existing_row and existing_row.pref_value:
                    existing_list = json.loads(existing_row.pref_value)
            except Exception:
                existing_list = []

        # Merge, deduplicate, cap at 10 most recent
        merged = list(dict.fromkeys(existing_list + prefs["key_preferences"]))[-10:]
        upsert("key_preferences", json.dumps(merged))

    if prefs.get("topics_of_interest"):
        upsert("topics_of_interest", json.dumps(prefs["topics_of_interest"][:10]))

    db.commit()


# ═══════════════════════════════════════════════════════════════
# CONTEXT INJECTION
# Called at the START of each orchestrator run
# ═══════════════════════════════════════════════════════════════

def get_personality_context(agent_id: str, company_id: int) -> str:
    """
    Returns a short paragraph the AI prepends to its system prompt
    to make itself feel more like the user.

    Empty string if no preferences are learned yet (new user).
    """
    db = SessionLocal()
    try:
        is_manager = agent_id.startswith("Manager_")
        prefs = {}

        if is_manager:
            # order_by keeps the rendered text deterministic — this string is
            # appended to the cached system block, so unstable row order would
            # silently invalidate the prompt cache on every command
            rows = db.query(ManagerProfile).filter(
                ManagerProfile.company_id == company_id,
            ).order_by(ManagerProfile.preference_key).all()
            for r in rows:
                prefs[r.preference_key] = r.preference_value
        else:
            try:
                emp_id = int(agent_id.split("_")[1])
            except Exception:
                return ""
            rows = db.query(EmployeePreference).filter(
                EmployeePreference.employee_id == emp_id,
            ).order_by(EmployeePreference.pref_key).all()
            for r in rows:
                prefs[r.pref_key] = r.pref_value

        if not prefs:
            return ""

        # Build a natural language summary
        lines = []
        style = prefs.get("communication_style")
        quirks = prefs.get("communication_quirks")
        decision = prefs.get("decision_pattern")

        if style or quirks:
            line = "This person communicates in a "
            if style: line += f"{style} way"
            if quirks: line += f". {quirks}"
            lines.append(line + ".")

        if decision:
            lines.append(f"Their decision-making is {decision}.")

        # Key preferences
        if prefs.get("key_preferences"):
            try:
                key_prefs = json.loads(prefs["key_preferences"])
                if key_prefs:
                    lines.append("Things you've learned about them:")
                    for p in key_prefs[-5:]:   # most recent 5
                        lines.append(f"- {p}")
            except Exception:
                pass

        if prefs.get("topics_of_interest"):
            try:
                topics = json.loads(prefs["topics_of_interest"])
                if topics:
                    lines.append(f"They care about: {', '.join(topics[:5])}.")
            except Exception:
                pass

        if not lines:
            return ""

        return (
            "\n\n--- WHAT YOU'VE LEARNED ABOUT THIS PERSON ---\n"
            + "\n".join(lines)
            + "\nMatch their style and remember their preferences. Don't mention this section."
        )

    except Exception as e:
        log.error(f"get_personality_context error: {e}")
        return ""
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════
# BACKGROUND TRIGGER — called after each save_agent_memory
# ═══════════════════════════════════════════════════════════════

def maybe_extract_in_background(agent_id: str, company_id: int, current_turn_count: int):
    """
    Fire-and-forget extraction in a daemon thread.

    Runs every EXTRACTION_INTERVAL turns to avoid hammering Ollama.
    Falls back silently if Ollama isn't running.
    """
    if current_turn_count == 0 or current_turn_count % EXTRACTION_INTERVAL != 0:
        return

    def _run():
        try:
            print(f"🧠 Starting preference extraction for {agent_id}...")
            extract_preferences_sync(agent_id, company_id)
            print(f"✅ Preference extraction complete for {agent_id}")
        except Exception as e:
            print(f"⚠️  Background extraction error for {agent_id}: {e}")
            import traceback; traceback.print_exc()

    threading.Thread(target=_run, daemon=True).start()
    print(f"🧠 Triggered preference learning for {agent_id} (turn {current_turn_count})")