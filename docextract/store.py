"""SQLite persistence: processed documents, review queue and human corrections."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from typing import Any

from .pipeline import Result

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, path TEXT, doc_type TEXT, status TEXT, parse_source TEXT,
  latency_s REAL, cost_usd REAL, created_at REAL, result_json TEXT);
CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, doc_id INTEGER, field TEXT, old_value TEXT, new_value TEXT, ts REAL);
"""


def connect(path: str = "docextract.db") -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def save(con: sqlite3.Connection, r: Result) -> int:
    payload = {"fields": {k: asdict(v) for k, v in r.extraction.fields.items()},
               "checks": [asdict(c) for c in r.checks], "review_fields": r.review_fields}
    cur = con.execute("INSERT INTO documents(path,doc_type,status,parse_source,latency_s,cost_usd,created_at,result_json) VALUES(?,?,?,?,?,?,?,?)",
                      (r.path, r.doc_type, r.status, r.parse_source, r.latency_s, r.cost_usd, time.time(), json.dumps(payload, default=str)))
    con.commit()
    return cur.lastrowid


def review_queue(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM documents WHERE status='review' ORDER BY id").fetchall()


def approve(con: sqlite3.Connection, doc_id: int, corrected: dict[str, Any]) -> None:
    row = con.execute("SELECT result_json FROM documents WHERE id=?", (doc_id,)).fetchone()
    payload = json.loads(row["result_json"])
    for k, new in corrected.items():
        old = payload["fields"].get(k, {}).get("value")
        if str(old) != str(new):
            con.execute("INSERT INTO corrections(doc_id,field,old_value,new_value,ts) VALUES(?,?,?,?,?)",
                        (doc_id, k, str(old), str(new), time.time()))
            payload["fields"].setdefault(k, {"flags": []})
            payload["fields"][k].update(value=new, confidence=1.0)
            payload["fields"][k]["flags"] = list(payload["fields"][k].get("flags", [])) + ["human_corrected"]
    con.execute("UPDATE documents SET status='approved_human', result_json=? WHERE id=?", (json.dumps(payload, default=str), doc_id))
    con.commit()


def export_approved(con: sqlite3.Connection) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM documents WHERE status IN ('approved_auto','approved_human')"):
        p = json.loads(r["result_json"])
        out.append({"path": r["path"], "doc_type": r["doc_type"], "status": r["status"],
                    "data": {k: v["value"] for k, v in p["fields"].items()}})
    return out
