"""
resolution_engine.py — the missing last step of the autonomous-coordination loop.
================================================================================
The loop already exists: infer dependencies → confirm → watch for drift
(`project_digest.run_drift_alerts` flips a contract to `at_risk`). Until now drift
only ALERTED the human ("re-check it"). This module closes the loop: when a
contract drifts, ask Claude for the SINGLE cheapest CORRECT fix, which is then
surfaced for one-click human approval (advise-before-dictate).

Read-only: returns a proposal dict, writes NOTHING. The Claude call runs OUTSIDE
any DB session so the background drift loop never holds a connection on the wire.
"""
import os
import json
import logging

import anthropic
from database.core import SessionLocal
from database.models import Contract, Task, Employee

log = logging.getLogger("nexus.resolution")

_client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"), timeout=45.0, max_retries=1)
MODEL = "claude-sonnet-4-5"   # match dependency_inference (the sibling coordination module)


# ═══════════════════════════════════════════════════════════════
# SEMANTIC DRIFT ASSESSMENT (drift v2)
# v1 flagged a contract at-risk whenever the producer task was EDITED — any
# typo fix or priority tweak alerted the consumer. v2 diffs the actual change
# against the contract text and only alerts when the promised INTERFACE
# plausibly changed. Cheap (Haiku), schema-enforced via forced tool use,
# and FAILS TO ALERT on errors (never silently swallows a real drift).
# ═══════════════════════════════════════════════════════════════

def producer_content(task) -> str:
    """The producer task's content as a stable snapshot string (what we diff)."""
    nl = chr(10)
    return nl.join([f"title: {task.title or ''}",
                    f"description: {task.description or ''}",
                    f"due: {task.due_date or ''}"])


_DRIFT_ASSESS_TOOL = {
    "name": "record_drift_assessment",
    "description": "Record whether a task change affects the promised interface.",
    "input_schema": {
        "type": "object",
        "properties": {
            "interface_changed": {
                "type": "boolean",
                "description": "true ONLY if the change plausibly alters what the producer "
                               "hands the consumer (format, fields, endpoints, protocol, scope "
                               "of the deliverable, or its timing in a way that breaks the "
                               "promise). Typo fixes, rewording, priority/status notes, and "
                               "changes unrelated to the promised interface are false."},
            "change_summary": {
                "type": "string",
                "description": "one short sentence: WHAT changed about the interface (empty if nothing)"},
        },
        "required": ["interface_changed", "change_summary"],
    },
}


def assess_contract_drift(contract_name: str, contract_description: str,
                          old_snapshot: str, new_content: str) -> dict:
    """{"interface_changed": bool, "change_summary": str}. Errors → assume
    changed (v1 behavior) so a broken assessor can never hide a real drift."""
    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=250,
            tools=[_DRIFT_ASSESS_TOOL],
            tool_choice={"type": "tool", "name": "record_drift_assessment"},
            messages=[{"role": "user", "content": (
                "Two colleagues agreed on an interface contract between their tasks:\n"
                f"<contract name={contract_name!r}>{(contract_description or '')[:600]}</contract>\n\n"
                "The PRODUCER task then changed. Before the change:\n"
                f"<before>{(old_snapshot or '')[:1200]}</before>\n\n"
                "After the change:\n"
                f"<after>{(new_content or '')[:1200]}</after>\n\n"
                "Does this change plausibly affect what the producer promised to hand the "
                "consumer? Judge against the CONTRACT, not against style."
            )}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "record_drift_assessment":
                d = dict(block.input)
                return {"interface_changed": bool(d.get("interface_changed", True)),
                        "change_summary": str(d.get("change_summary", ""))[:300]}
    except Exception as e:
        log.warning(f"drift assessment failed ({e}) — assuming changed (fail-to-alert)")
    return {"interface_changed": True, "change_summary": ""}


def propose_resolution(contract_id: int) -> dict:
    """Given a drifted contract (producer changed after the interface baseline), ask
    Claude for the single cheapest correct fix.
    Returns {summary, fix, rationale, effort, who} or {} if it can't be generated."""
    # 1. Gather context in a short session, then release it before the network call.
    db = SessionLocal()
    try:
        c = db.query(Contract).filter(Contract.id == contract_id).first()
        if not c:
            return {}
        producer = db.query(Task).filter(Task.id == c.producer_task_id).first()
        consumer = db.query(Task).filter(Task.id == c.consumer_task_id).first()
        if not producer or not consumer:
            return {}
        po = db.query(Employee).filter(Employee.id == producer.owner_id).first() if producer.owner_id else None
        co = db.query(Employee).filter(Employee.id == consumer.owner_id).first() if consumer.owner_id else None
        ctx = {
            "name":    c.name,
            "desc":    c.description or "(no details captured)",
            "p_title": producer.title,
            "p_desc":  (producer.description or "")[:400],
            "p_owner": po.name if po else "Unassigned",
            "c_title": consumer.title,
            "c_desc":  (consumer.description or "")[:400],
            "c_owner": co.name if co else "Unassigned",
        }
    finally:
        db.close()

    # 2. Ask Claude for the cheapest correct fix (no DB session held here).
    prompt = (
        "Two pieces of work are connected by an interface contract, and the PRODUCER side "
        "changed after the interface was agreed — so integration is now at risk.\n\n"
        f'INTERFACE CONTRACT: "{ctx["name"]}"\n  {ctx["desc"]}\n\n'
        f'PRODUCER work (changed): "{ctx["p_title"]}" — owner {ctx["p_owner"]}\n  {ctx["p_desc"]}\n\n'
        f'CONSUMER work (depends on the interface): "{ctx["c_title"]}" — owner {ctx["c_owner"]}\n  {ctx["c_desc"]}\n\n'
        "Propose the SINGLE cheapest CORRECT fix that keeps these two integrated. Weigh whether "
        "it is cheaper and safer for the consumer to adapt to the change or for the producer to "
        "restore the agreed interface — pick whichever is correct with the least total work. Be "
        "concrete and specific to this work, not generic.\n\n"
        "Return ONLY valid JSON, no prose, of exactly this shape:\n"
        '{"summary": "<one line>", "fix": "<the concrete fix, 1-3 sentences>", '
        '"rationale": "<why this is the cheapest correct option>", '
        '"effort": "low|medium|high", "who": "producer|consumer|both"}'
    )
    try:
        resp = _client.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if hasattr(b, "text")), "")
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception as e:
        log.warning(f"propose_resolution failed for contract {contract_id}: {e}")
        return {}
