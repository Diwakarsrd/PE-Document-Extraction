"""Command line: generate | run | eval | review | export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="docextract")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="create a synthetic labelled dataset")
    g.add_argument("--out", default="data"); g.add_argument("--n", type=int, default=10, help="docs per (tier x doc type)"); g.add_argument("--seed", type=int, default=7)
    r = sub.add_parser("run", help="process one PDF or a folder and store results in SQLite")
    r.add_argument("path"); r.add_argument("--extractor", default="rules", choices=["rules", "llm", "hybrid"])
    r.add_argument("--db", default="docextract.db"); r.add_argument("--tau", type=float, default=0.85)
    e = sub.add_parser("eval", help="evaluate an extractor on a generated dataset")
    e.add_argument("--data", default="data"); e.add_argument("--extractor", default="rules", choices=["rules", "llm", "hybrid"])
    e.add_argument("--tau", type=float, default=0.85); e.add_argument("--limit", type=int); e.add_argument("--out", default="reports")
    x = sub.add_parser("export", help="dump approved documents as JSON"); x.add_argument("--db", default="docextract.db")
    sub.add_parser("review", help="print the command that starts the review UI")
    a = ap.parse_args(argv)

    if a.cmd == "generate":
        from .generator import generate_dataset
        print(f"wrote {generate_dataset(a.out, a.n, a.seed)}/manifest.jsonl")
    elif a.cmd == "run":
        from . import store
        from .extractors import get_extractor
        from .pipeline import process
        ex, con = get_extractor(a.extractor), store.connect(a.db)
        files = [Path(a.path)] if Path(a.path).is_file() else sorted(Path(a.path).glob("*.pdf"))
        for f in files:
            res = process(f, ex, a.tau)
            store.save(con, res)
            print(f"{f.name}: {res.doc_type} -> {res.status} ({len(res.review_fields)} fields to review, {res.latency_s:.2f}s)")
    elif a.cmd == "eval":
        from .evaluate import evaluate, report_md
        from .extractors import get_extractor
        res = evaluate(a.data, get_extractor(a.extractor), a.tau, a.limit)
        out = Path(a.out); out.mkdir(exist_ok=True)
        (out / f"eval_{a.extractor}.json").write_text(json.dumps(res, indent=2, default=str))
        (out / f"eval_{a.extractor}.md").write_text(report_md(res, a.extractor))
        print(report_md(res, a.extractor))
    elif a.cmd == "export":
        from . import store
        print(json.dumps(store.export_approved(store.connect(a.db)), indent=2))
    else:
        print("streamlit run docextract/review_app.py")


if __name__ == "__main__":
    main()
