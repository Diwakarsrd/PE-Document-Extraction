"""Document schemas, value normalisation and comparison helpers.

Every extracted value lives under a flat dotted key, e.g. ``call_amount`` or
``holdings.3.fair_value``. Ground truth and extraction use the same key space,
which keeps validation, scoring and evaluation simple.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

CURRENCIES = {"USD", "AED", "EUR"}

SCALARS: dict[str, dict[str, str]] = {
    "capital_call": {
        "fund_name": "str", "investor_name": "str", "call_number": "int",
        "call_date": "date", "due_date": "date", "currency": "str",
        "commitment": "money", "call_amount": "money", "management_fee": "money",
        "investment_amount": "money", "fund_expenses": "money",
        "cumulative_called": "money", "unfunded_commitment": "money",
    },
    "lp_report": {
        "fund_name": "str", "period": "str", "currency": "str",
        "nav_beginning": "money", "contributions": "money", "distributions": "money",
        "net_gain": "money", "nav_ending": "money", "tvpi": "float", "dpi": "float",
        "net_irr_pct": "float", "total_cost": "money", "total_fair_value": "money",
    },
    "cap_table": {"company_name": "str", "as_of_date": "date", "total_shares": "int"},
}

# doc_type -> (table name, {column: kind})
TABLES: dict[str, tuple[str, dict[str, str]]] = {
    "lp_report": ("holdings", {"company": "str", "cost": "money", "fair_value": "money"}),
    "cap_table": ("holders", {"holder": "str", "share_class": "str", "shares": "int", "pct": "float"}),
}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


@dataclass
class FieldResult:
    value: Any = None
    confidence: float = 0.0
    evidence: str = ""
    flags: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    doc_type: str
    fields: dict[str, FieldResult] = field(default_factory=dict)

    def values(self) -> dict[str, Any]:
        return {k: v.value for k, v in self.fields.items() if v.value is not None}

    def table_rows(self, doc_type: Optional[str] = None) -> int:
        spec = TABLES.get(doc_type or self.doc_type)
        if not spec:
            return 0
        idx = {int(k.split(".")[1]) for k in self.fields if k.startswith(spec[0] + ".")}
        return max(idx) + 1 if idx else 0


def kind_of(doc_type: str, key: str) -> str:
    if "." in key:
        table, _, col = key.split(".")
        return TABLES[doc_type][1][col]
    return SCALARS[doc_type][key]


def expected_keys(doc_type: str) -> list[str]:
    return list(SCALARS[doc_type])


def parse_date(s: Any) -> Optional[str]:
    """Return ISO date for '2026-03-05', '5 March 2026' or 'March 5, 2026'."""
    if isinstance(s, (date, datetime)):
        return s.strftime("%Y-%m-%d")
    s = str(s).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})", s)
    if m and m.group(2).lower() in MONTHS:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def parse_money(s: Any) -> Optional[float]:
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    neg = t.startswith("(") or t.startswith("-") or "(" in t[:3]
    digits = re.sub(r"[^0-9.]", "", t)
    if not digits or digits == ".":
        return None
    try:
        v = float(digits)
    except ValueError:
        return None
    return -v if neg else v


def normalize(value: Any, kind: str) -> Any:
    if value is None:
        return None
    try:
        if kind == "money":
            v = parse_money(value)
            return None if v is None else round(v, 2)
        if kind == "int":
            v = parse_money(value)
            return None if v is None else int(round(v))
        if kind == "float":
            return round(float(str(value).replace(",", "").replace("%", "").replace("x", "")), 4)
        if kind == "date":
            return parse_date(value)
        return " ".join(str(value).split()).casefold()
    except (ValueError, TypeError):
        return None


def values_match(expected: Any, got: Any, kind: str) -> bool:
    e, g = normalize(expected, kind), normalize(got, kind)
    if e is None or g is None:
        return False
    if kind == "money":
        return abs(e - g) < 0.01
    if kind == "float":
        return abs(e - g) < 0.006
    return e == g


def flatten_truth(doc_type: str, truth: dict[str, Any]) -> dict[str, Any]:
    flat = {k: truth[k] for k in SCALARS[doc_type] if k in truth}
    spec = TABLES.get(doc_type)
    if spec:
        name, cols = spec
        for i, row in enumerate(truth.get(name, [])):
            for c in cols:
                flat[f"{name}.{i}.{c}"] = row[c]
    return flat
