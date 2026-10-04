"""Human review queue. Run: streamlit run docextract/review_app.py"""
import json
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

from docextract import store
from docextract.pipeline import TAU

st.set_page_config(page_title="Review queue", layout="wide")
db = st.sidebar.text_input("Database", "docextract.db")
tau = st.sidebar.slider("Confidence threshold", 0.5, 0.99, TAU)
con = store.connect(db)
queue = store.review_queue(con)
st.sidebar.metric("Documents awaiting review", len(queue))
if not queue:
    st.success("Queue is empty.")
    st.stop()

labels = [f"#{r['id']} {Path(r['path']).name} ({r['doc_type']})" for r in queue]
row = queue[labels.index(st.sidebar.selectbox("Document", labels))]
payload = json.loads(row["result_json"])
left, right = st.columns([1, 1])

with left:
    st.subheader("Source")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "80", "-png", row["path"], f"{td}/p"], check=False)
        for img in sorted(Path(td).glob("p-*.png")):
            st.image(str(img), use_container_width=True)
    failed = [c for c in payload["checks"] if not c["passed"]]
    if failed:
        st.error("Failed checks: " + "; ".join(f"{c['name']} ({c['detail']})" for c in failed))

with right:
    st.subheader("Fields needing review first")
    fields = payload["fields"]
    order = sorted(fields, key=lambda k: fields[k]["confidence"])
    corrected = {}
    for k in order:
        f = fields[k]
        low = f["confidence"] < tau
        label = f"{'LOW ' if low else ''}{k} (conf {f['confidence']:.2f})"
        new = st.text_input(label, value="" if f["value"] is None else str(f["value"]), key=f"{row['id']}-{k}",
                            help=f"evidence: {f.get('evidence', '')} | flags: {f.get('flags', [])}")
        if new != ("" if f["value"] is None else str(f["value"])):
            corrected[k] = new
    for k in payload.get("review_fields", []):
        if k not in fields:
            new = st.text_input(f"MISSING {k}", key=f"{row['id']}-{k}-missing")
            if new:
                corrected[k] = new
    if st.button("Approve", type="primary"):
        store.approve(con, row["id"], corrected)
        st.rerun()
