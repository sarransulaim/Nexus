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
