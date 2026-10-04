"""Business-rule and arithmetic cross-checks.

A passing arithmetic identity (e.g. NAV roll-forward) is strong evidence that
every value involved was read correctly, so passing "strong" checks raise
confidence; failing checks of any kind lower it and force human review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .schemas import CURRENCIES, TABLES

TOL = 0.011


@dataclass
class Check:
    name: str
    fields: list[str]
    passed: bool
    detail: str = ""
    strong: bool = False


def _has(vals: dict[str, Any], *keys: str) -> bool:
    return all(vals.get(k) is not None for k in keys)


def _rows(vals: dict[str, Any], table: str, col: str) -> list[tuple[str, float]]:
    out = []
    i = 0
    while f"{table}.{i}.{col}" in vals or f"{table}.{i}.company" in vals or f"{table}.{i}.holder" in vals:
        k = f"{table}.{i}.{col}"
        if vals.get(k) is not None:
            out.append((k, float(vals[k])))
        i += 1
        if i > 500:
            break
    return out


def run_checks(doc_type: str, v: dict[str, Any]) -> list[Check]:
    c: list[Check] = []
    if v.get("currency") is not None:
        c.append(Check("currency_valid", ["currency"], str(v["currency"]).upper() in CURRENCIES,
                       f"currency={v['currency']}"))
    if doc_type == "capital_call":
        if _has(v, "management_fee", "investment_amount", "fund_expenses", "call_amount"):
            s = v["management_fee"] + v["investment_amount"] + v["fund_expenses"]
            c.append(Check("items_sum", ["management_fee", "investment_amount", "fund_expenses", "call_amount"],
                           abs(s - v["call_amount"]) <= TOL, f"{s:,.2f} vs {v['call_amount']:,.2f}", True))
        if _has(v, "call_date", "due_date"):
            c.append(Check("dates_order", ["call_date", "due_date"], v["due_date"] > v["call_date"],
                           f"{v['call_date']} -> {v['due_date']}"))
        if _has(v, "commitment", "cumulative_called", "unfunded_commitment"):
            d = v["commitment"] - v["cumulative_called"]
            c.append(Check("unfunded", ["commitment", "cumulative_called", "unfunded_commitment"],
                           abs(d - v["unfunded_commitment"]) <= TOL, f"{d:,.2f} vs {v['unfunded_commitment']:,.2f}", True))
        if _has(v, "call_amount", "commitment"):
            c.append(Check("call_le_commitment", ["call_amount", "commitment"],
                           v["call_amount"] <= v["commitment"]))
    elif doc_type == "lp_report":
        if _has(v, "nav_beginning", "contributions", "distributions", "net_gain", "nav_ending"):
            e = v["nav_beginning"] + v["contributions"] - v["distributions"] + v["net_gain"]
            c.append(Check("nav_rollforward",
                           ["nav_beginning", "contributions", "distributions", "net_gain", "nav_ending"],
                           abs(e - v["nav_ending"]) <= TOL, f"{e:,.2f} vs {v['nav_ending']:,.2f}", True))
        for col, total in (("cost", "total_cost"), ("fair_value", "total_fair_value")):
            rows = _rows(v, "holdings", col)
            if rows and v.get(total) is not None:
                s = sum(x for _, x in rows)
                c.append(Check(f"holdings_{col}_sum", [k for k, _ in rows] + [total],
                               abs(s - v[total]) <= TOL, f"{s:,.2f} vs {v[total]:,.2f}", True))
        if _has(v, "tvpi", "dpi"):
            c.append(Check("tvpi_ge_dpi", ["tvpi", "dpi"], v["tvpi"] >= v["dpi"] - 1e-9))
        if v.get("period") is not None:
            c.append(Check("period_format", ["period"], bool(re.match(r"^Q[1-4] \d{4}$", str(v["period"]).strip())),
                           str(v["period"])))
    elif doc_type == "cap_table":
        rows = _rows(v, "holders", "shares")
        if rows and v.get("total_shares") is not None:
            s = sum(x for _, x in rows)
            c.append(Check("shares_sum", [k for k, _ in rows] + ["total_shares"],
                           abs(s - v["total_shares"]) <= 0.5, f"{s:,.0f} vs {v['total_shares']:,.0f}", True))
        pcts = _rows(v, "holders", "pct")
        if pcts:
            s = sum(x for _, x in pcts)
            c.append(Check("pct_sum", [k for k, _ in pcts], abs(s - 100) <= 0.1, f"{s:.2f}"))
        if v.get("total_shares"):
            for (sk, sh), (pk, pc) in zip(rows, pcts):
                if sk.split(".")[1] == pk.split(".")[1]:
                    exp = sh / v["total_shares"] * 100
                    c.append(Check(f"pct_row_{sk.split('.')[1]}", [sk, pk], abs(exp - pc) <= 0.02,
                                   f"{exp:.2f} vs {pc:.2f}", True))
    return c
