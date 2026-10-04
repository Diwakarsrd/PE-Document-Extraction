"""parse -> classify -> extract -> validate -> score -> route."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .extractors import RulesExtractor
from .parsing import classify, parse_pdf
from .schemas import Extraction, FieldResult
from .validators import Check, run_checks

TAU = 0.85            # per-field confidence needed for auto-approval
BOOST = 0.95          # confidence floor for fields covered by a passing arithmetic identity
PENALTY = 0.4         # multiplier for fields inside a failed check
PRICE_IN, PRICE_OUT = 0.59, 0.79   # USD per 1M tokens (override with env PRICE_IN / PRICE_OUT)


@dataclass
class Result:
    path: str
    doc_type: str
    extraction: Extraction
    checks: list[Check] = field(default_factory=list)
    status: str = "review"          # approved_auto | review
    parse_source: str = "text"
    latency_s: float = 0.0
    cost_usd: float = 0.0
    review_fields: list[str] = field(default_factory=list)


def score(ex: Extraction, checks: list[Check], tau: float = TAU) -> list[str]:
    """Adjust confidences using validators. Returns the field keys that need human review."""
    for c in checks:
        if c.passed and c.strong:
            for k in c.fields:
                if k in ex.fields:
                    ex.fields[k].confidence = max(ex.fields[k].confidence, BOOST)
    for c in checks:
        if not c.passed:
            for k in c.fields:
                if k in ex.fields:
                    ex.fields[k].confidence = round(ex.fields[k].confidence * PENALTY, 3)
                    ex.fields[k].flags.append(f"check_failed:{c.name}")
    return [k for k, f in ex.fields.items() if f.confidence < tau]


def cost_of(usage: dict) -> float:
    import os
    pi, po = float(os.environ.get("PRICE_IN", PRICE_IN)), float(os.environ.get("PRICE_OUT", PRICE_OUT))
    return (usage.get("input_tokens", 0) * pi + usage.get("output_tokens", 0) * po) / 1e6


def process(path: str | Path, extractor: Any = None, tau: float = TAU) -> Result:
    extractor = extractor or RulesExtractor()
    t0 = time.perf_counter()
    parsed = parse_pdf(path)
    doc_type = classify(parsed.text)
    if doc_type == "unknown":
        return Result(str(path), "unknown", Extraction("unknown"), [], "review", parsed.source,
                      time.perf_counter() - t0, 0.0, ["doc_type"])
    ex, usage = extractor.extract(parsed, doc_type)
    checks = run_checks(doc_type, ex.values())
    review = score(ex, checks, tau)
    # a missing expected field also needs a human
    from .schemas import SCALARS
    review += [k for k in SCALARS[doc_type] if k not in ex.fields]
    return Result(str(path), doc_type, ex, checks, "review" if review or any(not c.passed for c in checks) else "approved_auto",
                  parsed.source, time.perf_counter() - t0, cost_of(usage), sorted(set(review)))
