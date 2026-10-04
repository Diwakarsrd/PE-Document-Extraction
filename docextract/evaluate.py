"""Evaluation harness: run an extractor over a labelled dataset and report accuracy, routing quality, latency and cost."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from .pipeline import TAU, process
from difflib import SequenceMatcher

from .schemas import TABLES, flatten_truth, kind_of, normalize, values_match

SWEEP = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]


def _pct(a: list[float], q: float) -> float:
    return float(np.percentile(a, q)) if a else 0.0


def align_rows(doc_type: str, truth: dict, ex) -> dict[int, int]:
    """Map truth row index -> extracted row index by name similarity, so one dropped/merged row
    does not make every following row count as wrong."""
    spec = TABLES.get(doc_type)
    if not spec:
        return {}
    name, cols = spec
    key_col = next(iter(cols))
    got = {int(k.split(".")[1]): normalize(f.value, "str") for k, f in ex.fields.items() if k.endswith("." + key_col) and k.startswith(name + ".")}
    used: set[int] = set()
    mapping: dict[int, int] = {}
    for i, row in enumerate(truth.get(name, [])):
        want = normalize(row[key_col], "str")
        best = max(((SequenceMatcher(None, want, g or "").ratio(), j) for j, g in got.items() if j not in used), default=(0, None))
        if best[0] >= 0.8:
            mapping[i] = best[1]
            used.add(best[1])
    return mapping


def evaluate(data_dir: str | Path, extractor: Any, tau: float = TAU, limit: int | None = None, progress: bool = True) -> dict:
    data_dir = Path(data_dir)
    manifest = [json.loads(l) for l in open(data_dir / "manifest.jsonl")]
    if limit:
        manifest = manifest[:limit]
    fields: list[dict] = []
    docs: list[dict] = []
    for n, m in enumerate(manifest):
        r = process(data_dir / m["file"], extractor, tau)
        truth = flatten_truth(m["doc_type"], m["truth"])
        correct_all = r.doc_type == m["doc_type"]
        rowmap = align_rows(m["doc_type"], m["truth"], r.extraction)
        for key, exp in truth.items():
            lookup = key
            if "." in key:
                t, i, c = key.split(".")
                lookup = f"{t}.{rowmap[int(i)]}.{c}" if int(i) in rowmap else None
            f = r.extraction.fields.get(lookup) if lookup else None
            ok = bool(f and f.value is not None and values_match(exp, f.value, kind_of(m["doc_type"], key)))
            correct_all &= ok
            fields.append({"tier": m["tier"], "doc_type": m["doc_type"], "key": key.split(".")[0] + (("." + key.split(".")[2]) if "." in key else ""),
                           "ok": ok, "conf": f.confidence if f else 0.0, "exp": exp, "got": f.value if f else None, "file": m["file"]})
        docs.append({"tier": m["tier"], "doc_type": m["doc_type"], "pred_type": r.doc_type, "exact": correct_all, "status": r.status,
                     "latency": r.latency_s, "cost": r.cost_usd, "source": r.parse_source, "file": m["file"]})
        if progress:
            print(f"[{n + 1}/{len(manifest)}] {m['file']}: {r.status}, exact={correct_all}", flush=True)
    return summarize(fields, docs, tau)


def _acc(rows: list[dict]) -> float:
    return sum(r["ok"] for r in rows) / len(rows) if rows else 0.0


def summarize(fields: list[dict], docs: list[dict], tau: float) -> dict:
    def group(rows, key):
        g = defaultdict(list)
        for r in rows:
            g[r[key]].append(r)
        return g

    out: dict = {"tau": tau, "n_docs": len(docs), "n_fields": len(fields)}
    out["field_accuracy"] = _acc(fields)
    out["field_accuracy_by_tier"] = {k: _acc(v) for k, v in group(fields, "tier").items()}
    out["field_accuracy_by_doc_type"] = {k: _acc(v) for k, v in group(fields, "doc_type").items()}
    out["doc_exact_match"] = sum(d["exact"] for d in docs) / len(docs)
    out["doc_exact_by_tier"] = {k: sum(d["exact"] for d in v) / len(v) for k, v in group(docs, "tier").items()}
    out["classification_accuracy"] = sum(d["pred_type"] == d["doc_type"] for d in docs) / len(docs)
    auto = [d for d in docs if d["status"] == "approved_auto"]
    out["review_rate"] = 1 - len(auto) / len(docs)
    out["auto_approve_doc_precision"] = (sum(d["exact"] for d in auto) / len(auto)) if auto else None
    out["review_rate_by_tier"] = {k: sum(d["status"] == "review" for d in v) / len(v) for k, v in group(docs, "tier").items()}
    lat = [d["latency"] for d in docs]
    out["latency_s"] = {"p50": _pct(lat, 50), "p95": _pct(lat, 95), "mean": float(np.mean(lat))}
    out["latency_by_tier"] = {k: {"p50": _pct([d["latency"] for d in v], 50), "p95": _pct([d["latency"] for d in v], 95)}
                              for k, v in group(docs, "tier").items()}
    out["cost_usd_per_doc"] = float(np.mean([d["cost"] for d in docs]))
    sweep = []
    for t in SWEEP:
        hi = [f for f in fields if f["conf"] >= t]
        sweep.append({"tau": t, "auto_field_share": len(hi) / len(fields), "auto_field_precision": _acc(hi) if hi else None})
    out["threshold_sweep"] = sweep
    hi = [f for f in fields if f["conf"] >= tau]
    out["auto_field_share"] = len(hi) / len(fields)
    out["auto_field_precision"] = _acc(hi) if hi else None
    wrong = [f for f in fields if not f["ok"]]
    by_key = defaultdict(list)
    for f in wrong:
        by_key[(f["doc_type"], f["key"])].append(f)
    total_by_key = defaultdict(int)
    for f in fields:
        total_by_key[(f["doc_type"], f["key"])] += 1
    worst = sorted(by_key.items(), key=lambda kv: -len(kv[1]))[:10]
    out["worst_fields"] = [{"doc_type": k[0], "field": k[1], "errors": len(v), "of": total_by_key[k],
                            "example": {"expected": v[0]["exp"], "got": v[0]["got"], "file": v[0]["file"], "tier": v[0]["tier"]}}
                           for k, v in worst]
    silent = [f for f in wrong if f["conf"] >= tau]
    out["silent_errors"] = len(silent)
    out["silent_error_examples"] = [{k: f[k] for k in ("file", "key", "exp", "got", "conf")} for f in silent[:8]]
    return out


def report_md(res: dict, name: str) -> str:
    p = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
    L = [f"# Evaluation report: `{name}` extractor", "",
         f"{res['n_docs']} documents, {res['n_fields']} labelled fields, auto-approve threshold tau={res['tau']}.", "",
         "## Headline", "",
         "| Metric | Value |", "|---|---|",
         f"| Field accuracy | {p(res['field_accuracy'])} |", f"| Document exact match | {p(res['doc_exact_match'])} |",
         f"| Classification accuracy | {p(res['classification_accuracy'])} |",
         f"| Docs needing human review | {p(res['review_rate'])} |",
         f"| Auto-approved doc precision | {p(res['auto_approve_doc_precision'])} |",
         f"| Fields auto-approved (conf >= tau) | {p(res['auto_field_share'])} |",
         f"| Auto-approved field precision | {p(res['auto_field_precision'])} |",
         f"| Silent errors (wrong but conf >= tau) | {res['silent_errors']} |",
         f"| Latency P50 / P95 | {res['latency_s']['p50']:.2f}s / {res['latency_s']['p95']:.2f}s |",
         f"| Cost per document | ${res['cost_usd_per_doc']:.5f} |", "",
         "## By tier", "", "| Tier | Field acc. | Doc exact | Review rate | P50 | P95 |", "|---|---|---|---|---|---|"]
    for t in ("clean", "complex", "noisy"):
        if t in res["field_accuracy_by_tier"]:
            lt = res["latency_by_tier"][t]
            L.append(f"| {t} | {p(res['field_accuracy_by_tier'][t])} | {p(res['doc_exact_by_tier'][t])} | {p(res['review_rate_by_tier'][t])} | {lt['p50']:.2f}s | {lt['p95']:.2f}s |")
    L += ["", "## By document type", "", "| Type | Field acc. |", "|---|---|"]
    L += [f"| {k} | {p(v)} |" for k, v in res["field_accuracy_by_doc_type"].items()]
    L += ["", "## Threshold sweep (what auto-approving costs in accuracy)", "", "| tau | Fields auto-approved | Precision of those fields |", "|---|---|---|"]
    L += [f"| {s['tau']} | {p(s['auto_field_share'])} | {p(s['auto_field_precision'])} |" for s in res["threshold_sweep"]]
    L += ["", "## Worst fields", "", "| Doc type | Field | Errors | Example (expected -> got) |", "|---|---|---|---|"]
    L += [f"| {w['doc_type']} | {w['field']} | {w['errors']}/{w['of']} | {w['example']['expected']} -> {w['example']['got']} ({w['example']['tier']}) |" for w in res["worst_fields"]]
    if res["silent_error_examples"]:
        L += ["", "## Silent errors (auto-approved but wrong)", "", "| File | Field | Expected | Got | Conf |", "|---|---|---|---|---|"]
        L += [f"| {e['file']} | {e['key']} | {e['exp']} | {e['got']} | {e['conf']:.2f} |" for e in res["silent_error_examples"]]
    return "\n".join(L) + "\n"
