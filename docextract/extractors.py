"""Extractors: a deterministic regex baseline, an OpenAI-compatible LLM extractor, and a hybrid combiner.

All return an ``Extraction`` keyed by flat dotted keys plus a usage dict (tokens) used for cost tracking.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Callable, Optional

from .parsing import Line, ParsedDoc
from .schemas import (SCALARS, TABLES, Extraction, FieldResult, normalize, parse_date, parse_money, values_match)

RULE_CONF = 0.92  # base confidence of a regex hit on a known label
OCR_CONF_FLOOR = 0.80  # OCR line confidence at/above this is treated as fully reliable

# Label patterns the baseline knows. It deliberately knows layout A fully and layout B only partly,
# which is exactly where a rules-only system breaks on unseen client formats.
LABELS: dict[str, dict[str, list[str]]] = {
    "capital_call": {
        "fund_name": [r"Fund\s*:", r"Partnership\s+(?!Expenses)"], "investor_name": [r"Investor", r"Limited Partner"],
        "call_number": [r"Capital Call No\.?", r"Notice\s*#"], "call_date": [r"Call Date", r"Notice Date"],
        "due_date": [r"Due Date", r"Payment Due"], "commitment": [r"Total Commitment", r"Commitment"],
        "call_amount": [r"Total Capital Call Amount", r"Amount Called"], "management_fee": [r"Management Fee"],
        "investment_amount": [r"Portfolio Investments", r"Investments"], "fund_expenses": [r"Fund Expenses"],
        "cumulative_called": [r"Cumulative Capital Called"], "unfunded_commitment": [r"Unfunded Commitment"]},
    "lp_report": {
        "fund_name": [r"Fund\s*:", r"Partnership\s+"], "period": [r"Reporting Period"],
        "nav_beginning": [r"Beginning NAV"], "contributions": [r"Contributions", r"Capital Contributions"],
        "distributions": [r"Distributions"], "net_gain": [r"Net Gain\s*/\s*\(Loss\)"],
        "nav_ending": [r"Ending NAV"], "tvpi": [r"TVPI"], "dpi": [r"DPI"], "net_irr_pct": [r"Net IRR(?:\s*\(%\))?"]},
    "cap_table": {"company_name": [r"Company\s*:", r"Issuer"], "as_of_date": [r"As of", r"Register Date"],
                  "total_shares": [r"Total Shares Outstanding"]},
}
MONEY_START = re.compile(r"^\(?\s*(?:USD|AED|EUR|\$)?\s*-?\d")
NUM = r"\(?-?[\d,]+(?:\.\d+)?\)?"


WELL_FORMED = re.compile(r"^[\(\-]?(?:USD|AED|EUR|\$)?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?$|^[\(\-]?(?:USD|AED|EUR|\$)?\s*\d+(?:\.\d{2})?\)?$")
STRAY = re.compile(r"[|!{}\[\]_~]")


def _suspicious(raw: str, kind: str) -> bool:
    """OCR damage heuristics: malformed money tokens, stray glyphs in names."""
    raw = raw.strip()
    if kind == "money":
        m = re.match(r"^\(?\s*(?:USD|AED|EUR|\$)?\s*[\d,.]+\s*\)?", raw)
        return bool(m) and not WELL_FORMED.match(m.group(0).strip())
    if kind == "str":
        return bool(STRAY.search(raw))
    return False


def _scale(line: Line) -> float:
    return 1.0 if line.conf >= OCR_CONF_FLOOR or line.conf == 1.0 else max(0.0, line.conf / OCR_CONF_FLOOR)


def _value(rest: str, kind: str) -> Any:
    rest = rest.strip()
    if kind == "money":
        return parse_money(re.match(r"^\(?\s*(?:USD|AED|EUR|\$)?\s*[\d,]+(?:\.\d+)?\s*\)?", rest).group(0)) if MONEY_START.match(rest) else None
    if kind == "int":
        m = re.match(r"^#?\s*(\d[\d,]*)", rest)
        return int(m.group(1).replace(",", "")) if m else None
    if kind == "float":
        m = re.match(r"^(-?\d+(?:\.\d+)?)", rest)
        return float(m.group(1)) if m else None
    if kind == "date":
        return parse_date(rest)
    return rest or None


class RulesExtractor:
    name = "rules"

    def extract(self, parsed: ParsedDoc, doc_type: str) -> tuple[Extraction, dict]:
        ex = Extraction(doc_type)
        lines = parsed.lines
        for key, kind in SCALARS[doc_type].items():
            if key == "currency":
                m = re.search(r"\b(USD|AED|EUR)\b", parsed.text)
                if m:
                    ex.fields[key] = FieldResult(m.group(1), RULE_CONF, m.group(0))
                continue
            for pat in LABELS[doc_type].get(key, []):
                hit = None
                for ln in lines:
                    m = re.match(rf"^\s*(?:{pat})\s*:?\s*(?P<rest>.*)$", ln.text, re.I)
                    if m and (v := _value(m.group("rest"), kind)) is not None:
                        hit = (v, ln, m.group("rest"))
                        break
                if hit:
                    c, flags = RULE_CONF * _scale(hit[1]), []
                    if _suspicious(hit[2], kind):
                        c, flags = c * 0.4, ["suspicious_format"]
                    ex.fields[key] = FieldResult(hit[0], round(c, 3), hit[1].text, flags)
                    break
        self._tables(ex, lines, doc_type)
        return ex, {"input_tokens": 0, "output_tokens": 0}

    def _tables(self, ex: Extraction, lines: list[Line], doc_type: str) -> None:
        if doc_type == "lp_report":
            hdr = next((i for i, l in enumerate(lines) if re.match(r"(?i)^(portfolio company|company|investment)\b.*\b(cost|fair)", l.text)), None)
            if hdr is None:
                return
            n = 0
            h = lines[hdr].text.lower()
            swap = 0 <= h.find("fair") < h.find("cost")   # header says Fair Value comes before Cost
            for ln in lines[hdr + 1:]:
                tm = re.match(rf"(?i)^total\s+({NUM})\s+({NUM})", ln.text)
                if tm:
                    for k, g in (("total_cost", 2 if swap else 1), ("total_fair_value", 1 if swap else 2)):
                        ex.fields[k] = FieldResult(parse_money(tm.group(g)), round(RULE_CONF * _scale(ln), 3), ln.text)
                    break
                m = re.match(rf"^(?P<n>.*?[A-Za-z].*?)\s+(?P<a>{NUM})\s+(?P<b>{NUM})(?:\s+\S+)*$", ln.text)
                if m:
                    c = round(RULE_CONF * _scale(ln) * (0.4 if _suspicious(m.group("a"), "money") or _suspicious(m.group("b"), "money") or STRAY.search(m.group("n")) else 1), 3)
                    ex.fields[f"holdings.{n}.company"] = FieldResult(m.group("n").strip(), c, ln.text)
                    a, b = (m.group("b"), m.group("a")) if swap else (m.group("a"), m.group("b"))
                    ex.fields[f"holdings.{n}.cost"] = FieldResult(parse_money(a), c, ln.text)
                    ex.fields[f"holdings.{n}.fair_value"] = FieldResult(parse_money(b), c, ln.text)
                    n += 1
        elif doc_type == "cap_table":
            n = 0
            for ln in lines:
                m = re.match(r"^(?P<h>.+?)\s+(?P<c>Common|Series [A-Z] Preferred|Option Pool)\s+(?P<s>[\d,]+)\s+(?P<p>[\d.]+)\s*%?", ln.text)
                if m:
                    c = round(RULE_CONF * _scale(ln) * (0.4 if _suspicious(m.group("s"), "money") or STRAY.search(m.group("h")) else 1), 3)
                    ex.fields[f"holders.{n}.holder"] = FieldResult(m.group("h").strip(), c, ln.text)
                    ex.fields[f"holders.{n}.share_class"] = FieldResult(m.group("c"), c, ln.text)
                    ex.fields[f"holders.{n}.shares"] = FieldResult(parse_money(m.group("s")), c, ln.text)
                    ex.fields[f"holders.{n}.pct"] = FieldResult(float(m.group("p")), c, ln.text)
                    n += 1
            if "total_shares" not in ex.fields:
                for ln in lines:
                    m = re.match(r"(?i)^total\s+([\d,]+)\s+[\d.]+\s*%", ln.text)
                    if m:
                        ex.fields["total_shares"] = FieldResult(parse_money(m.group(1)), round(RULE_CONF * _scale(ln), 3), ln.text)


# ---------------- LLM ----------------
def _spec(doc_type: str) -> str:
    s = "\n".join(f'- "{k}" ({v})' for k, v in SCALARS[doc_type].items())
    if doc_type in TABLES:
        name, cols = TABLES[doc_type]
        s += f'\n- "{name}": list of rows with keys ' + ", ".join(f'"{c}" ({k})' for c, k in cols.items())
    return s


SYSTEM = ("You extract structured data from private-equity fund documents. Return ONLY a JSON object. "
          "Money = plain number (no symbols/commas; negatives as negative numbers); dates = YYYY-MM-DD; "
          "currency = ISO code; period = 'Q<1-4> <year>'; percentages as plain numbers. Use null when a value is "
          "absent. Never compute or guess values; copy what the document states. For each scalar give "
          '{"value":..., "confidence": 0-1}; for table rows give plain values plus "row_confidence".')


def default_transport(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


class LLMExtractor:
    """Any OpenAI-compatible chat endpoint (Groq by default). Configure via LLM_BASE_URL / LLM_API_KEY / LLM_MODEL."""
    name = "llm"

    def __init__(self, transport: Optional[Callable] = None, model: Optional[str] = None):
        self.base = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        self.key = os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
        self.transport = transport or default_transport

    def extract(self, parsed: ParsedDoc, doc_type: str) -> tuple[Extraction, dict]:
        user = f"Document type: {doc_type}\nFields to extract:\n{_spec(doc_type)}\n\nDOCUMENT TEXT:\n{parsed.text}"
        payload = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
        resp = self.transport(f"{self.base}/chat/completions", payload,
                              {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        data = parse_llm_json(resp["choices"][0]["message"]["content"])
        u = resp.get("usage", {})
        ocr = min((l.conf for l in parsed.lines), default=1.0) if parsed.source == "ocr" else 1.0
        return to_extraction(data, doc_type, parsed, ocr), {"input_tokens": u.get("prompt_tokens", 0),
                                                            "output_tokens": u.get("completion_tokens", 0)}


def parse_llm_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


def to_extraction(data: dict, doc_type: str, parsed: ParsedDoc, ocr_floor: float = 1.0) -> Extraction:
    ex = Extraction(doc_type)
    scale = 1.0 if parsed.source != "ocr" else min(1.0, max(0.0, ocr_floor) / OCR_CONF_FLOOR + 0.2)
    for k, kind in SCALARS[doc_type].items():
        item = data.get(k)
        val, conf = (item.get("value"), item.get("confidence", 0.7)) if isinstance(item, dict) else (item, 0.7)
        if val is None:
            continue
        nv = normalize(val, kind)
        ex.fields[k] = FieldResult(nv if kind in ("money", "int", "float", "date") else str(val).strip(),
                                   round(min(float(conf), 0.95) * min(scale, 1.0), 3), "llm")
    if doc_type in TABLES:
        name, cols = TABLES[doc_type]
        for i, row in enumerate(data.get(name) or []):
            if not isinstance(row, dict):
                continue
            rc = min(float(row.get("row_confidence", 0.7)), 0.95) * min(scale, 1.0)
            for c, kind in cols.items():
                if row.get(c) is not None:
                    nv = normalize(row[c], kind)
                    ex.fields[f"{name}.{i}.{c}"] = FieldResult(nv if kind != "str" else str(row[c]).strip(), round(rc, 3), "llm")
    return ex


class HybridExtractor:
    """LLM primary + rules secondary. Agreement raises confidence; disagreement halves it and flags the field."""
    name = "hybrid"

    def __init__(self, llm: Optional[LLMExtractor] = None, rules: Optional[RulesExtractor] = None):
        self.llm, self.rules = llm or LLMExtractor(), rules or RulesExtractor()

    def extract(self, parsed: ParsedDoc, doc_type: str) -> tuple[Extraction, dict]:
        a, usage = self.llm.extract(parsed, doc_type)
        b, _ = self.rules.extract(parsed, doc_type)
        from .schemas import kind_of
        for k, fa in a.fields.items():
            fb = b.fields.get(k)
            if fb is None or fb.value is None:
                continue
            if values_match(fa.value, fb.value, kind_of(doc_type, k)):
                fa.confidence = max(fa.confidence, 0.96)
            else:
                fa.confidence = round(fa.confidence * 0.5, 3)
                fa.flags.append(f"rules_disagree:{fb.value}")
        return a, usage


def get_extractor(name: str):
    return {"rules": RulesExtractor, "llm": LLMExtractor, "hybrid": HybridExtractor}[name]()
