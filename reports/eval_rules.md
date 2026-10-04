# Evaluation report: `rules` extractor

90 documents, 2588 labelled fields, auto-approve threshold tau=0.85.

## Headline

| Metric | Value |
|---|---|
| Field accuracy | 79.6% |
| Document exact match | 46.7% |
| Classification accuracy | 100.0% |
| Docs needing human review | 52.2% |
| Auto-approved doc precision | 97.7% |
| Fields auto-approved (conf >= tau) | 76.8% |
| Auto-approved field precision | 99.6% |
| Silent errors (wrong but conf >= tau) | 7 |
| Latency P50 / P95 | 0.03s / 10.31s |
| Cost per document | $0.00000 |

## By tier

| Tier | Field acc. | Doc exact | Review rate | P50 | P95 |
|---|---|---|---|---|---|
| clean | 100.0% | 100.0% | 0.0% | 0.02s | 0.02s |
| complex | 91.6% | 33.3% | 66.7% | 0.02s | 0.05s |
| noisy | 44.3% | 6.7% | 90.0% | 7.45s | 10.49s |

## By document type

| Type | Field acc. |
|---|---|
| capital_call | 84.1% |
| lp_report | 77.1% |
| cap_table | 80.9% |

## Threshold sweep (what auto-approving costs in accuracy)

| tau | Fields auto-approved | Precision of those fields |
|---|---|---|
| 0.5 | 77.6% | 99.5% |
| 0.6 | 77.5% | 99.6% |
| 0.7 | 77.4% | 99.6% |
| 0.8 | 77.2% | 99.6% |
| 0.85 | 76.8% | 99.6% |
| 0.9 | 76.7% | 99.6% |
| 0.95 | 35.9% | 100.0% |

## Worst fields

| Doc type | Field | Errors | Example (expected -> got) |
|---|---|---|---|
| lp_report | holdings.company | 53/258 | Kestrel Aerospace -> None (noisy) |
| cap_table | holders.holder | 50/236 | Bluewater Private Equity -> None (noisy) |
| lp_report | holdings.cost | 49/258 | 601000 -> None (noisy) |
| lp_report | holdings.fair_value | 49/258 | 1096000 -> None (noisy) |
| cap_table | holders.shares | 49/236 | 233000 -> None (noisy) |
| cap_table | holders.share_class | 47/236 | Series B Preferred -> None (noisy) |
| cap_table | holders.pct | 47/236 | 12.17 -> None (noisy) |
| capital_call | cumulative_called | 17/30 | 20049000 -> None (complex) |
| capital_call | fund_expenses | 16/30 | 71000 -> None (complex) |
| capital_call | unfunded_commitment | 16/30 | 11951000 -> None (complex) |

## Silent errors (auto-approved but wrong)

| File | Field | Expected | Got | Conf |
|---|---|---|---|---|
| docs/noisy_capital_call_01.pdf | management_fee | 165000 | 165000.9 | 0.92 |
| docs/noisy_capital_call_01.pdf | fund_expenses | 39000 | 39006.0 | 0.92 |
| docs/noisy_capital_call_09.pdf | fund_name | Atlas Horizon Fund III | Atlas Horizon Fund Ill | 0.92 |
| docs/noisy_lp_report_00.pdf | fund_name | Bluewater Private Equity III | Bluewater Private Equity #1 | 0.92 |
| docs/noisy_lp_report_09.pdf | distributions | 8000000 | 6000000.0 | 0.92 |
| docs/noisy_cap_table_01.pdf | company_name | Nova Telecom Ltd | Nova Telecom Lid | 0.92 |
| docs/noisy_cap_table_06.pdf | holders.holder | Oasis Frontier Capital | Gasis Frontier Capital | 0.92 |
