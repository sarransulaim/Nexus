"""Gold-labeled drift cases for assess_contract_drift.

expect=False → benign edit (must NOT alert); expect=True → real interface
drift (must alert). Includes adversarial cases: both-sides pollution, timing
promises, and implementation details that look scary but change nothing.
"""

CONTRACT_JSON = "producer hands the consumer a JSON payload from the /orders endpoint with fields id, total, status"
CONTRACT_DESIGN = "producer delivers the final Figma component library the consumer builds screens from"
CONTRACT_DATED = "producer delivers the labeled dataset (CSV with user_id,item_id,label) by June 10 so training can start"

DRIFT_CASES = [
    # ── benign (expect False) ─────────────────────────────────────
    {"name": "typo fix", "contract": CONTRACT_JSON, "expect": False,
     "before": "Build the /orders endpoint returing JSON with id, total, status.",
     "after":  "Build the /orders endpoint returning JSON with id, total, status."},
    {"name": "rewording, same meaning", "contract": CONTRACT_JSON, "expect": False,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Implement the orders endpoint; response is JSON containing id, total and status."},
    {"name": "collaboration note added", "contract": CONTRACT_DESIGN, "expect": False,
     "before": "Create the Figma component library: buttons, inputs, tokens.",
     "after":  "Create the Figma component library: buttons, inputs, tokens. (Pairing with Sam on the input states.)"},
    {"name": "formatting cleanup", "contract": CONTRACT_JSON, "expect": False,
     "before": "Build /orders. JSON out: id, total, status. handle errors. write tests",
     "after":  "Build /orders.\n- JSON response: id, total, status\n- Handle errors\n- Write tests"},
    {"name": "internal implementation detail", "contract": CONTRACT_JSON, "expect": False,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Build the /orders endpoint returning JSON with id, total, status. Internally switching from raw SQL to the ORM for the query layer."},
    {"name": "both-sides pollution (delta is benign)", "contract": CONTRACT_JSON, "expect": False,
     "before": "Build the /orders endpoint. NOTE: legacy v1 returned XML. New response is JSON with id, total, status.",
     "after":  "Build the /orders endpoint. NOTE: legacy v1 returned XML. New response is JSON with id, total, status. (Clarified wording.)"},
    {"name": "progress note", "contract": CONTRACT_DATED, "expect": False,
     "before": "Label the purchases dataset; output CSV user_id,item_id,label. Due June 10.",
     "after":  "Label the purchases dataset; output CSV user_id,item_id,label. Due June 10. About halfway done."},
    {"name": "priority tag", "contract": CONTRACT_DESIGN, "expect": False,
     "before": "Create the Figma component library: buttons, inputs, tokens.",
     "after":  "[P1] Create the Figma component library: buttons, inputs, tokens."},

    # ── real drift (expect True) ──────────────────────────────────
    {"name": "format change JSON→XML", "contract": CONTRACT_JSON, "expect": True,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Build the /orders endpoint returning XML with id, total, status."},
    {"name": "new required auth header", "contract": CONTRACT_JSON, "expect": True,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Build the /orders endpoint returning JSON with id, total, status. All calls now require the X-Org-Token header."},
    {"name": "endpoint renamed", "contract": CONTRACT_JSON, "expect": True,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Build the /v2/purchases endpoint returning JSON with id, total, status."},
    {"name": "field removed", "contract": CONTRACT_JSON, "expect": True,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Build the /orders endpoint returning JSON with id and status only — total moves to a separate /orders/{id}/pricing call."},
    {"name": "REST→GraphQL", "contract": CONTRACT_JSON, "expect": True,
     "before": "Build the /orders endpoint returning JSON with id, total, status.",
     "after":  "Expose orders through the new GraphQL schema instead of a REST endpoint."},
    {"name": "deliverable artifact swapped", "contract": CONTRACT_DESIGN, "expect": True,
     "before": "Create the Figma component library: buttons, inputs, tokens.",
     "after":  "Deliver static PNG mockups of the key screens instead of a component library."},
    {"name": "promised date slips", "contract": CONTRACT_DATED, "expect": True,
     "before": "Label the purchases dataset; output CSV user_id,item_id,label. Due June 10.",
     "after":  "Label the purchases dataset; output CSV user_id,item_id,label. Pushed to June 24 due to vendor delay."},
    {"name": "push→pull semantics", "contract": "producer's service sends a webhook to the consumer on every order event", "expect": True,
     "before": "Emit an order-events webhook to downstream consumers on create/update.",
     "after":  "Expose an order-events feed the consumers poll every 5 minutes (webhook removed)."},
]
