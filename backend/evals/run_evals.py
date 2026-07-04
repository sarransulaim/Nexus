"""Nexus AI eval harness — measured accuracy for the coordination layer.

Runs the PRODUCTION inference paths against gold-labeled fixtures:
  1. Dependency inference (dependency_inference.infer_dependencies)
     → precision / recall / F1 on known dependency graphs
  2. Semantic drift assessment (resolution_engine.assess_contract_drift)
     → accuracy, false-positive rate (benign flagged), false-negative rate

Usage (from backend/):   python -m evals.run_evals [deps|drift]
Cost: ~10 Sonnet calls + ~16 Haiku calls per full run (roughly $0.50).
Results are written to evals/results/latest.json for the deck.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.fixtures import FIXTURES
from evals.drift_cases import DRIFT_CASES


def eval_dependencies() -> dict:
    from dependency_inference import infer_dependencies
    total_tp = total_fp = total_fn = 0
    rows = []
    for fx in FIXTURES:
        pred = infer_dependencies(fx["name"], fx["tasks"])
        pred_edges = {(d["producer_task_id"], d["consumer_task_id"]) for d in pred["dependencies"]}
        gold_edges = set(map(tuple, fx["gold"]))
        tp = len(pred_edges & gold_edges)
        fp = len(pred_edges - gold_edges)
        fn = len(gold_edges - pred_edges)
        total_tp += tp; total_fp += fp; total_fn += fn
        rows.append({"fixture": fx["name"], "tp": tp, "fp": fp, "fn": fn,
                     "pred": sorted(pred_edges), "gold": sorted(gold_edges)})
        print(f"  deps | {fx['name'][:38]:38s} tp={tp} fp={fp} fn={fn}"
              + (f"  extra={sorted(pred_edges - gold_edges)}" if fp else "")
              + (f"  missed={sorted(gold_edges - pred_edges)}" if fn else ""))
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 1.0
    recall    = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"per_fixture": rows, "tp": total_tp, "fp": total_fp, "fn": total_fn,
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def eval_drift() -> dict:
    from resolution_engine import assess_contract_drift
    tp = tn = fp = fn = 0
    rows = []
    for case in DRIFT_CASES:
        v = assess_contract_drift("eval contract", case["contract"], case["before"], case["after"])
        got = bool(v["interface_changed"])
        ok = got == case["expect"]
        if case["expect"] and got: tp += 1
        elif case["expect"] and not got: fn += 1
        elif not case["expect"] and got: fp += 1
        else: tn += 1
        rows.append({"case": case["name"], "expect": case["expect"], "got": got, "ok": ok,
                     "summary": v.get("change_summary", "")[:80]})
        print(f"  drift| {'OK ' if ok else 'MISS'} expect={str(case['expect']):5s} got={str(got):5s} | {case['name']}")
    n = len(DRIFT_CASES)
    benign = sum(1 for c in DRIFT_CASES if not c["expect"])
    breaking = n - benign
    return {"per_case": rows, "n": n,
            "accuracy": round((tp + tn) / n, 3),
            "false_positive_rate": round(fp / benign, 3) if benign else 0.0,   # benign flagged
            "false_negative_rate": round(fn / breaking, 3) if breaking else 0.0}  # drift missed


def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    out = {}
    if which in ("all", "deps"):
        print("== Dependency inference ==")
        out["dependency_inference"] = eval_dependencies()
        d = out["dependency_inference"]
        print(f"  => precision={d['precision']} recall={d['recall']} f1={d['f1']} "
              f"(tp={d['tp']} fp={d['fp']} fn={d['fn']})")
    if which in ("all", "drift"):
        print("== Semantic drift assessment ==")
        out["drift_assessment"] = eval_drift()
        d = out["drift_assessment"]
        print(f"  => accuracy={d['accuracy']} false-positive rate={d['false_positive_rate']} "
              f"false-negative rate={d['false_negative_rate']} (n={d['n']})")

    os.makedirs(os.path.join(os.path.dirname(__file__), "results"), exist_ok=True)
    path = os.path.join(os.path.dirname(__file__), "results", "latest.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
