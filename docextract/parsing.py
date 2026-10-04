"""PDF -> text lines. Digital PDFs use pdfplumber; scans fall back to Tesseract OCR with per-line confidence."""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

MIN_TEXT_CHARS = 80


@dataclass
class Line:
    text: str
    conf: float = 1.0   # 1.0 for digital text; mean Tesseract word confidence (0-1) for OCR
    page: int = 0


@dataclass
class ParsedDoc:
    lines: list[Line] = field(default_factory=list)
    source: str = "text"   # "text" | "ocr"
    n_pages: int = 0

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


def _digital(path: Path) -> ParsedDoc:
    doc = ParsedDoc(source="text")
    with pdfplumber.open(str(path)) as pdf:
        doc.n_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            for t in (page.extract_text() or "").splitlines():
                if t.strip():
                    doc.lines.append(Line(t.strip(), 1.0, i))
    return doc


def _ocr(path: Path, dpi: int = 200) -> ParsedDoc:
    import pytesseract
    from PIL import Image

    doc = ParsedDoc(source="ocr")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(path), f"{td}/p"], check=True)
        pages = sorted(Path(td).glob("p-*.png"))
        doc.n_pages = len(pages)
        for pi, p in enumerate(pages):
            d = pytesseract.image_to_data(Image.open(p), config="--psm 6", output_type=pytesseract.Output.DICT)
            groups: dict[tuple, list[tuple[str, float]]] = {}
            for txt, conf, b, pa, ln in zip(d["text"], d["conf"], d["block_num"], d["par_num"], d["line_num"]):
                if txt.strip() and float(conf) >= 0:
                    groups.setdefault((b, pa, ln), []).append((txt, float(conf) / 100))
            for key in sorted(groups):
                words = groups[key]
                doc.lines.append(Line(" ".join(w for w, _ in words), sum(c for _, c in words) / len(words), pi))
    return doc


def parse_pdf(path: str | Path) -> ParsedDoc:
    path = Path(path)
    doc = _digital(path)
    if sum(len(l.text) for l in doc.lines) >= MIN_TEXT_CHARS:
        return doc
    return _ocr(path)


def classify(text: str) -> str:
    t = text.lower()
    if re.search(r"capital call|drawdown", t):
        return "capital_call"
    if re.search(r"capitali[sz]ation table|shareholder register|cap table", t):
        return "cap_table"
    if re.search(r"quarterly (report|statement)|net asset value|\bnav\b", t):
        return "lp_report"
    return "unknown"
