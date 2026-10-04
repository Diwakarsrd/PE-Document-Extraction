"""Synthetic PE fund document generator. Ground-truth JSON is produced first, then rendered,
so every document ships with exact labels.

Tiers: clean (digital PDF, layout A) | complex (digital PDF, layout B: different labels, date/number
formats, swapped table columns, bigger tables) | noisy (scanned: rasterised, rotated, blurred, noised).
"""
from __future__ import annotations

import json
import random
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageFilter
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FUNDS = ["Meridian Growth Partners", "Falcon Ridge Capital", "Atlas Horizon Fund", "Cedar Point Ventures",
         "Northgate Equity Partners", "Silverline Buyout Fund", "Oasis Frontier Capital", "Bluewater Private Equity"]
INVESTORS = ["Al Noor Family Office", "Harbor Pension Trust", "Crescent Endowment", "Zenith Insurance Group",
             "Pinnacle Wealth Holdings", "Sandstone Foundation", "Evergreen Retirement Fund", "Orion Sovereign Partners"]
COMPANIES = ["Delta Foods", "Nimbus Cloud", "Apex Logistics", "Verdant Energy", "Quartz Health", "Lumen Retail",
             "Falcon Robotics", "Harbor Marine", "Summit Fintech", "Cobalt Mining", "Aurora Pharma", "Ironwood Steel",
             "Nova Telecom", "Sable Hospitality", "Vertex Analytics", "Tidal Water", "Kestrel Aerospace", "Opal Education"]
HOLDERS = ["Founders Pool", "Meridian Growth Partners", "Falcon Ridge Capital", "Atlas Horizon Fund", "Employee Trust",
           "Cedar Point Ventures", "Northgate Equity Partners", "Strategic Investor Holdings", "Angel Syndicate",
           "Silverline Buyout Fund", "Oasis Frontier Capital", "Bluewater Private Equity"]
CLASSES = ["Common", "Series A Preferred", "Series B Preferred", "Option Pool"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]
TITLES = {
    ("capital_call", "A"): "CAPITAL CALL NOTICE", ("capital_call", "B"): "DRAWDOWN NOTICE",
    ("lp_report", "A"): "QUARTERLY REPORT", ("lp_report", "B"): "PARTNER QUARTERLY STATEMENT",
    ("cap_table", "A"): "CAPITALIZATION TABLE", ("cap_table", "B"): "SHAREHOLDER REGISTER",
}
LABELS = {  # (doc_type, variant) -> field -> label as printed
    ("capital_call", "A"): {"fund_name": "Fund", "investor_name": "Investor", "call_number": "Capital Call No.",
        "call_date": "Call Date", "due_date": "Due Date", "commitment": "Total Commitment",
        "call_amount": "Total Capital Call Amount", "management_fee": "Management Fee",
        "investment_amount": "Portfolio Investments", "fund_expenses": "Fund Expenses",
        "cumulative_called": "Cumulative Capital Called", "unfunded_commitment": "Unfunded Commitment"},
    ("capital_call", "B"): {"fund_name": "Partnership", "investor_name": "Limited Partner", "call_number": "Notice #",
        "call_date": "Notice Date", "due_date": "Payment Due", "commitment": "Commitment",
        "call_amount": "Amount Called", "management_fee": "Management Fee", "investment_amount": "Investments",
        "fund_expenses": "Partnership Expenses", "cumulative_called": "Total Contributed to Date",
        "unfunded_commitment": "Remaining Commitment"},
    ("lp_report", "A"): {"fund_name": "Fund", "period": "Reporting Period", "nav_beginning": "Beginning NAV",
        "contributions": "Contributions", "distributions": "Distributions", "net_gain": "Net Gain/(Loss)",
        "nav_ending": "Ending NAV", "tvpi": "TVPI", "dpi": "DPI", "net_irr_pct": "Net IRR (%)"},
    ("lp_report", "B"): {"fund_name": "Partnership", "period": "Quarter ended", "nav_beginning": "Opening Net Asset Value",
        "contributions": "Capital Contributions", "distributions": "Distributions", "net_gain": "Net Investment Gain",
        "nav_ending": "Closing Net Asset Value", "tvpi": "Total Value to Paid-In (TVPI)",
        "dpi": "Distributions to Paid-In (DPI)", "net_irr_pct": "Net IRR (%)"},
    ("cap_table", "A"): {"company_name": "Company", "as_of_date": "As of", "total_shares": "Total Shares Outstanding"},
    ("cap_table", "B"): {"company_name": "Issuer", "as_of_date": "Register Date"},
}
TIERS = ["clean", "complex", "noisy"]
DOC_TYPES = ["capital_call", "lp_report", "cap_table"]


def _d(rng: random.Random) -> date:
    return date(2026, 1, 1) + timedelta(days=rng.randint(0, 270))


def make_truth(doc_type: str, rng: random.Random, tier: str) -> dict:
    cur = rng.choice(["USD", "USD", "AED", "EUR"])
    if doc_type == "capital_call":
        commitment = rng.randint(5, 50) * 1_000_000
        call_amount = int(commitment * rng.uniform(0.04, 0.2) // 1000 * 1000)
        fee = int(commitment * 0.02 / 4 // 100 * 100)
        exp = rng.randint(20, 90) * 1000
        cd = _d(rng)
        cum = int(rng.uniform(call_amount, commitment * 0.8) // 1000 * 1000)
        return {"fund_name": f"{rng.choice(FUNDS)} {rng.choice(['I', 'II', 'III', 'IV'])}",
                "investor_name": rng.choice(INVESTORS), "call_number": rng.randint(1, 12),
                "call_date": cd.isoformat(), "due_date": (cd + timedelta(days=rng.randint(10, 15))).isoformat(),
                "currency": cur, "commitment": commitment, "call_amount": call_amount, "management_fee": fee,
                "investment_amount": call_amount - fee - exp, "fund_expenses": exp,
                "cumulative_called": cum, "unfunded_commitment": commitment - cum}
    if doc_type == "lp_report":
        beg = rng.randint(50, 400) * 1_000_000
        con, dis = rng.randint(0, 30) * 1_000_000, rng.randint(0, 20) * 1_000_000
        gain = rng.randint(-15, 40) * 1_000_000 + rng.randint(0, 999) * 1000
        n = rng.randint(10, 16) if tier == "complex" else rng.randint(5, 9)
        names = rng.sample(COMPANIES, n)
        rows = []
        for nm in names:
            cost = rng.randint(2, 60) * 100_000 + rng.randint(0, 99) * 1000
            rows.append({"company": nm, "cost": cost, "fair_value": int(cost * rng.uniform(0.6, 2.4) // 1000 * 1000)})
        dpi = round(rng.uniform(0.0, 0.9), 2)
        q = rng.randint(1, 4)
        return {"fund_name": f"{rng.choice(FUNDS)} {rng.choice(['II', 'III', 'IV', 'V'])}",
                "period": f"Q{q} 2026", "currency": cur, "nav_beginning": beg, "contributions": con,
                "distributions": dis, "net_gain": gain, "nav_ending": beg + con - dis + gain,
                "tvpi": round(dpi + rng.uniform(0.3, 1.6), 2), "dpi": dpi, "net_irr_pct": round(rng.uniform(-4, 28), 1),
                "total_cost": sum(r["cost"] for r in rows), "total_fair_value": sum(r["fair_value"] for r in rows),
                "holdings": rows}
    n = rng.randint(9, 12) if tier == "complex" else rng.randint(5, 8)
    holders = rng.sample(HOLDERS, n)
    shares = [rng.randint(50, 900) * 1000 for _ in holders]
    total = sum(shares)
    rows = [{"holder": h, "share_class": rng.choice(CLASSES), "shares": s, "pct": round(s / total * 100, 2)}
            for h, s in zip(holders, shares)]
    return {"company_name": f"{rng.choice(COMPANIES)} {rng.choice(['Ltd', 'Inc', 'Holdings', 'Group'])}",
            "as_of_date": _d(rng).isoformat(), "total_shares": total, "holders": rows}


# ---------- formatting ----------
def fmt_money(v: float, cur: str, variant: str, with_code: bool = True) -> str:
    neg, a = v < 0, abs(int(round(v)))
    if variant == "A":
        s = f"{cur} {a:,.2f}" if with_code else f"{a:,.2f}"
    else:
        s = (f"${a:,}" if cur == "USD" else f"{cur} {a:,}") if with_code else f"{a:,}"
    return f"({s})" if neg else s


def fmt_date(iso: str, variant: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return iso if variant == "A" else f"{d} {MONTH_NAMES[m - 1]} {y}"


def _kv(variant: str, rows: list[tuple[str, str]]):
    if variant == "A":
        st = getSampleStyleSheet()["Normal"]
        return [Paragraph(f"{k}: {v}", st) for k, v in rows]
    t = Table(rows, colWidths=[3.2 * inch, 2.6 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 10), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return [t]


def _grid(data, variant: str):
    t = Table(data, repeatRows=1)
    style = [("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
             ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("ALIGN", (1, 0), (-1, -1), "RIGHT")]
    style += [("GRID", (0, 0), (-1, -1), 0.5, colors.grey)] if variant == "A" else \
             [("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]
    t.setStyle(TableStyle(style))
    return t


def render_pdf(doc_type: str, truth: dict, variant: str, path: Path, rng: random.Random) -> dict:
    """Render to a digital PDF. Returns layout metadata (e.g. swapped table columns)."""
    st = getSampleStyleSheet()
    L = LABELS[(doc_type, variant)]
    cur = truth.get("currency", "USD")
    meta: dict = {"variant": variant}
    story = [Paragraph(TITLES[(doc_type, variant)], st["Title"]), Spacer(1, 8)]

    def money(k):
        return fmt_money(truth[k], cur, variant)

    if doc_type == "capital_call":
        story += _kv(variant, [(L["fund_name"], truth["fund_name"]), (L["investor_name"], truth["investor_name"]),
                               (L["call_number"], str(truth["call_number"])),
                               (L["call_date"], fmt_date(truth["call_date"], variant)),
                               (L["due_date"], fmt_date(truth["due_date"], variant))])
        story += [Spacer(1, 6), Paragraph(f"All amounts in {cur}.", st["Normal"]), Spacer(1, 6)]
        story += _kv(variant, [(L[k], money(k)) for k in
                               ["commitment", "call_amount", "management_fee", "investment_amount", "fund_expenses",
                                "cumulative_called", "unfunded_commitment"]])
    elif doc_type == "lp_report":
        story += _kv(variant, [(L["fund_name"], truth["fund_name"]),
                               (L["period"], truth["period"] if variant == "A" else
                                f"{ {'Q1':'31 March','Q2':'30 June','Q3':'30 September','Q4':'31 December'}[truth['period'][:2]] } 2026")])
        story += [Spacer(1, 6), Paragraph(f"All amounts in {cur}.", st["Normal"]), Spacer(1, 6)]
        story += _kv(variant, [(L[k], money(k)) for k in ["nav_beginning", "contributions", "distributions", "net_gain", "nav_ending"]])
        story += [Spacer(1, 8), Paragraph("Portfolio Holdings", st["Heading3"])]
        swap = variant == "B" and rng.random() < 0.5
        extra = variant == "B"
        hdr = ["Portfolio Company", "Fair Value", "Cost"] if swap else ["Portfolio Company", "Cost", "Fair Value"]
        data = [hdr + (["% of NAV"] if extra else [])]
        for r in truth["holdings"]:
            nums = [fmt_money(r["fair_value"], cur, variant, False), fmt_money(r["cost"], cur, variant, False)] if swap else \
                   [fmt_money(r["cost"], cur, variant, False), fmt_money(r["fair_value"], cur, variant, False)]
            data.append([r["company"]] + nums + ([f"{r['fair_value'] / truth['nav_ending'] * 100:.1f}%"] if extra else []))
        tot = [fmt_money(truth["total_fair_value"], cur, variant, False), fmt_money(truth["total_cost"], cur, variant, False)] if swap else \
              [fmt_money(truth["total_cost"], cur, variant, False), fmt_money(truth["total_fair_value"], cur, variant, False)]
        data.append(["Total"] + tot + ([""] if extra else []))
        story += [_grid(data, variant), Spacer(1, 8)]
        story += _kv(variant, [(L["tvpi"], f"{truth['tvpi']:.2f}x"), (L["dpi"], f"{truth['dpi']:.2f}x"),
                               (L["net_irr_pct"], f"{truth['net_irr_pct']:.1f}%")])
        meta["swap_cols"] = swap
    else:
        story += _kv(variant, [(L["company_name"], truth["company_name"]),
                               (L["as_of_date"], fmt_date(truth["as_of_date"], variant))])
        if "total_shares" in L:
            story += _kv(variant, [(L["total_shares"], f"{truth['total_shares']:,}")])
        story.append(Spacer(1, 8))
        data = [["Holder" if variant == "A" else "Shareholder", "Class", "Shares", "%" if variant == "A" else "% Held"]]
        data += [[r["holder"], r["share_class"], f"{r['shares']:,}", f"{r['pct']:.2f}%"] for r in truth["holders"]]
        data.append(["Total", "", f"{truth['total_shares']:,}", "100.00%"])
        story.append(_grid(data, variant))
    if variant == "B":
        story += [Spacer(1, 10), Paragraph("Figures are unaudited. See the Limited Partnership Agreement, section 7.2.", st["Italic"])]
    SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.8 * inch).build(story)
    return meta


def degrade_to_scan(pdf: Path, out: Path, rng: random.Random) -> dict:
    """Rasterise, then rotate/blur/noise/downsample and save as an image-only PDF."""
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "150", "-png", str(pdf), f"{td}/p"], check=True)
        pages = []
        sev = rng.choice(["light", "medium", "heavy"])
        angle = {"light": 0.4, "medium": 1.0, "heavy": 1.8}[sev] * rng.choice([-1, 1])
        blur = {"light": 0.4, "medium": 0.8, "heavy": 1.2}[sev]
        sigma = {"light": 8, "medium": 16, "heavy": 26}[sev]
        for p in sorted(Path(td).glob("p-*.png")):
            im = Image.open(p).convert("L").rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC)
            im = im.filter(ImageFilter.GaussianBlur(blur))
            arr = np.asarray(im, dtype=np.float32) + np.random.default_rng(rng.randint(0, 10**9)).normal(0, sigma, im.size[::-1])
            pages.append(Image.fromarray(np.clip(arr, 0, 255).astype("uint8")))
        pages[0].save(out, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    return {"severity": sev, "angle": round(angle, 2)}


def generate_dataset(out_dir: str | Path, n_per_cell: int = 4, seed: int = 7) -> Path:
    out = Path(out_dir)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    manifest = []
    for tier in TIERS:
        for dt in DOC_TYPES:
            for i in range(n_per_cell):
                variant = "A" if tier == "clean" else "B" if tier == "complex" else rng.choice("AB")
                truth = make_truth(dt, rng, tier)
                name = f"{tier}_{dt}_{i:02d}.pdf"
                meta = render_pdf(dt, truth, variant, out / "docs" / name if tier != "noisy" else out / "docs" / f"_{name}", rng)
                if tier == "noisy":
                    meta.update(degrade_to_scan(out / "docs" / f"_{name}", out / "docs" / name, rng))
                    (out / "docs" / f"_{name}").unlink()
                manifest.append({"file": f"docs/{name}", "doc_type": dt, "tier": tier, "meta": meta, "truth": truth})
    with open(out / "manifest.jsonl", "w") as f:
        for m in manifest:
            f.write(json.dumps(m) + "\n")
    return out
