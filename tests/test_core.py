import json, os, random, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docextract import store
from docextract.evaluate import align_rows
from docextract.extractors import HybridExtractor, LLMExtractor, RulesExtractor, parse_llm_json
from docextract.generator import generate_dataset
from docextract.parsing import parse_pdf
from docextract.pipeline import process, score
from docextract.schemas import Extraction, FieldResult, normalize, parse_date, parse_money, values_match
from docextract.validators import run_checks


class Schemas(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_date("5 March 2026"), "2026-03-05")
        self.assertEqual(parse_date("March 5, 2026"), "2026-03-05")
        self.assertEqual(parse_date("2026-03-05"), "2026-03-05")
        self.assertIsNone(parse_date("soon"))

    def test_money(self):
        self.assertEqual(parse_money("USD 1,234.50"), 1234.5)
        self.assertEqual(parse_money("(USD 1,234.00)"), -1234.0)
        self.assertEqual(parse_money("$7,000"), 7000.0)
        self.assertTrue(values_match(1000, "1,000.00", "money"))
        self.assertFalse(values_match(1000, 1001, "money"))
        self.assertEqual(normalize("  Foo   Bar ", "str"), "foo bar")


class Validators(unittest.TestCase):
    def test_capital_call(self):
        ok = dict(management_fee=10, investment_amount=80, fund_expenses=10, call_amount=100, call_date="2026-01-01",
                  due_date="2026-01-12", commitment=1000, cumulative_called=300, unfunded_commitment=700, currency="USD")
        self.assertTrue(all(c.passed for c in run_checks("capital_call", ok)))
        bad = dict(ok, call_amount=101)
        failed = [c.name for c in run_checks("capital_call", bad) if not c.passed]
        self.assertIn("items_sum", failed)

    def test_nav_rollforward(self):
        v = dict(nav_beginning=100, contributions=10, distributions=5, net_gain=-2, nav_ending=103)
        self.assertTrue(next(c for c in run_checks("lp_report", v) if c.name == "nav_rollforward").passed)
        v["nav_ending"] = 104
        self.assertFalse(next(c for c in run_checks("lp_report", v) if c.name == "nav_rollforward").passed)

    def test_scoring_boosts_and_penalises(self):
        ex = Extraction("capital_call", {k: FieldResult(val, 0.7) for k, val in
              dict(management_fee=10, investment_amount=80, fund_expenses=10, call_amount=100).items()})
        review = score(ex, run_checks("capital_call", ex.values()))
        self.assertEqual(review, [])                      # passing identity -> all boosted above tau
        ex.fields["call_amount"].value = 99
        review = score(ex, run_checks("capital_call", ex.values()))
        self.assertIn("call_amount", review)              # failing identity -> review


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.d = generate_dataset(cls.tmp, n_per_cell=1, seed=11)
        cls.man = [json.loads(l) for l in open(Path(cls.d) / "manifest.jsonl")]

    def test_clean_docs_auto_approved_and_correct(self):
        from docextract.evaluate import evaluate
        res = evaluate(self.d, RulesExtractor(), limit=3, progress=False)   # the 3 clean docs
        self.assertEqual(res["field_accuracy"], 1.0)
        self.assertEqual(res["review_rate"], 0.0)

    def test_scanned_doc_uses_ocr(self):
        m = next(m for m in self.man if m["tier"] == "noisy")
        self.assertEqual(parse_pdf(Path(self.d) / m["file"]).source, "ocr")

    def test_store_roundtrip_and_correction(self):
        con = store.connect(":memory:")
        m = next(m for m in self.man if m["tier"] == "complex" and m["doc_type"] == "capital_call")
        r = process(Path(self.d) / m["file"])
        did = store.save(con, r)
        self.assertEqual(len(store.review_queue(con)), 1 if r.status == "review" else 0)
        store.approve(con, did, {"fund_expenses": m["truth"]["fund_expenses"]})
        out = store.export_approved(con)
        self.assertEqual(out[0]["data"]["fund_expenses"], m["truth"]["fund_expenses"])

    def test_row_alignment_survives_dropped_row(self):
        m = next(m for m in self.man if m["doc_type"] == "cap_table" and m["tier"] == "clean")
        ex = Extraction("cap_table")
        for i, row in enumerate(m["truth"]["holders"][1:]):          # first row "dropped"
            ex.fields[f"holders.{i}.holder"] = FieldResult(row["holder"], 0.9)
        mp = align_rows("cap_table", m["truth"], ex)
        self.assertNotIn(0, mp)
        self.assertEqual(mp[1], 0)


class LLMPath(unittest.TestCase):
    """The LLM path is exercised with a stub transport (no network needed)."""
    def _stub(self, content):
        return lambda url, payload, headers: {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}

    def test_parse_fenced_json(self):
        self.assertEqual(parse_llm_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_llm_extractor_and_hybrid(self):
        os.environ["LLM_API_KEY"] = "x"
        tmp = tempfile.mkdtemp()
        d = generate_dataset(tmp, n_per_cell=1, seed=5)
        m = next(json.loads(l) for l in open(Path(d) / "manifest.jsonl") if json.loads(l)["tier"] == "clean" and json.loads(l)["doc_type"] == "capital_call")
        t = m["truth"]
        reply = {k: {"value": v, "confidence": 0.9} for k, v in t.items()}
        reply["call_amount"] = {"value": t["call_amount"] + 1, "confidence": 0.9}   # LLM gets one field wrong
        parsed = parse_pdf(Path(d) / m["file"])
        llm = LLMExtractor(transport=self._stub(json.dumps(reply)))
        ex, usage = llm.extract(parsed, "capital_call")
        self.assertEqual(usage["input_tokens"], 1000)
        hy, _ = HybridExtractor(llm=llm).extract(parsed, "capital_call")
        self.assertLess(hy.fields["call_amount"].confidence, 0.5)                 # disagreement with rules halves confidence
        self.assertGreaterEqual(hy.fields["fund_name"].confidence, 0.95)           # agreement raises it


if __name__ == "__main__":
    unittest.main()
