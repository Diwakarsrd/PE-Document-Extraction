# Evaluation report: `rules` extractor

90 documents, 2588 labelled fields, auto-approve threshold tau=0.85.

## Headline

| Metric | Value |
|---|---|
| Field accuracy | 69.0% |
| Document exact match | 46.7% |
| Classification accuracy | 100.0% |
| Docs needing human review | 51.1% |
| Auto-approved doc precision | 95.5% |
| Fields auto-approved (conf >= tau) | 77.4% |
| Auto-approved field precision | 88.1% |
| Silent errors (wrong but conf >= tau) | 239 |
| Latency P50 / P95 | 0.03s / 10.28s |
| Cost per document | $0.00000 |

## By tier

| Tier | Field acc. | Doc exact | Review rate | P50 | P95 |
|---|---|---|---|---|---|
| clean | 100.0% | 100.0% | 0.0% | 0.02s | 0.03s |
| complex | 73.9% | 33.3% | 66.7% | 0.02s | 0.04s |
| noisy | 33.2% | 6.7% | 86.7% | 7.42s | 10.42s |

## By document type

| Type | Field acc. |
|---|---|
| capital_call | 84.4% |
| lp_report | 56.7% |
| cap_table | 77.0% |

## Threshold sweep (what auto-approving costs in accuracy)

| tau | Fields auto-approved | Precision of those fields |
|---|---|---|
| 0.5 | 78.5% | 87.2% |
| 0.6 | 78.3% | 87.4% |
| 0.7 | 78.2% | 87.5% |
| 0.8 | 77.9% | 87.7% |
| 0.85 | 77.4% | 88.1% |
| 0.9 | 77.3% | 88.0% |
| 0.95 | 35.9% | 79.8% |

## Worst fields

| Doc type | Field | Errors | Example (expected -> got) |
|---|---|---|---|
| lp_report | holdings.cost | 151/258 | 1703000 -> 3966000.0 (complex) |
| lp_report | holdings.fair_value | 151/258 | 3966000 -> 1703000.0 (complex) |
| lp_report | holdings.company | 65/258 | Kestrel Aerospace -> None (noisy) |
| cap_table | holders.holder | 59/236 | Bluewater Private Equity -> Strategic Investor Holdings (noisy) |
| cap_table | holders.shares | 59/236 | 233000 -> 96000.0 (noisy) |
| cap_table | holders.pct | 59/236 | 12.17 -> 5.01 (noisy) |
| cap_table | holders.share_class | 56/236 | Series B Preferred -> Common (noisy) |
| capital_call | cumulative_called | 17/30 | 20049000 -> None (complex) |
| capital_call | fund_expenses | 16/30 | 71000 -> None (complex) |
| capital_call | unfunded_commitment | 16/30 | 11951000 -> None (complex) |

## Silent errors (auto-approved but wrong)

| File | Field | Expected | Got | Conf |
|---|---|---|---|---|
| docs/complex_lp_report_00.pdf | total_cost | 29493000 | 44081000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | total_fair_value | 44081000 | 29493000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | holdings.cost | 1703000 | 3966000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | holdings.fair_value | 3966000 | 1703000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | holdings.cost | 4783000 | 5516000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | holdings.fair_value | 5516000 | 4783000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | holdings.cost | 324000 | 485000.0 | 0.95 |
| docs/complex_lp_report_00.pdf | holdings.fair_value | 485000 | 324000.0 | 0.95 |
