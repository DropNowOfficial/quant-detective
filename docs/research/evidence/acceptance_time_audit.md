# Acceptance-time audit (user finding #2): Apple 10-K times 4 h later than the SEC index

Retrieved live 2026-10-11 (~08:2x ET) with `User-Agent: QuantDetective research contact-research@example.com`, ≤4 req/s.
Script: `acceptance_time_audit.py` (output `acceptance_time_audit_output.txt`, per-filing data `acceptance_time_audit_results.json`).

## The three sources compared for every one of the 6 filings

1. **Raw field** – `filings.recent.acceptanceDateTime` in `https://data.sec.gov/submissions/CIK<cik>.json` (value carries a `Z`).
2. **Filing index page** – `.../<accession>-index.htm`, "Accepted" (US Eastern wall clock; EDT in Nov/Oct, EST in Jan/Feb – stated below per row). The accession header `.../<accession>.hdr.sgml` `<ACCEPTANCE-DATETIME>` (no zone, Eastern) agreed with the index page for all 6.
3. **What the pipeline wrote** – v1 `data_points.csv` @ e14b256 (`acceptance_utc`, `acceptance_et`) vs v2.

| Filing | Raw submissions field | Index page "Accepted" (zone) | True UTC (index → UTC) | v1 `acceptance_utc` / `acceptance_et` | Raw − true UTC | v2 `acceptance_utc` / `acceptance_et` |
|---|---|---|---|---|---|---|
| AAPL 0000320193-24-000123 | `2024-11-01T14:01:36.000Z` | 2024-11-01 06:01:36 **EDT** | 2024-11-01T10:01:36Z | `…T14:01:36.000Z` / `2024-11-01T10:01:36-04:00` | **+4 h** | `…T10:01:36.000Z` / `2024-11-01T06:01:36-04:00` |
| AAPL 0000320193-25-000079 | `2025-10-31T14:01:26.000Z` | 2025-10-31 06:01:26 **EDT** | 2025-10-31T10:01:26Z | `…T14:01:26.000Z` / `2025-10-31T10:01:26-04:00` | **+4 h** | `…T10:01:26.000Z` / `2025-10-31T06:01:26-04:00` |
| JNJ 0000200406-25-000038 | `2025-02-13T21:07:39.000Z` | 2025-02-13 16:07:39 EST | 2025-02-13T21:07:39Z | same as true | 0 | unchanged |
| JNJ 0000200406-26-000016 | `2026-02-11T21:06:11.000Z` | 2026-02-11 16:06:11 EST | 2026-02-11T21:06:11Z | same as true | 0 | unchanged |
| ADBE 0000796343-25-000004 | `2025-01-13T21:02:17.000Z` | 2025-01-13 16:02:17 EST | 2025-01-13T21:02:17Z | same as true | 0 | unchanged |
| ADBE 0000796343-26-000003 | `2026-01-15T22:01:55.000Z` | 2026-01-15 17:01:55 EST | 2026-01-15T22:01:55Z | same as true | 0 | unchanged |

(The user's observation is reproduced: v1's *ET* column for Apple, 10:01:36-04:00, is 4 hours later than the index page's 06:01:36.)

## Finding and root cause

* **Neither of the two hypotheses is right as stated.** The conversion in `extract.py` v1 did not double-apply an offset: v1 parsed the raw `…Z` string as UTC and converted it to New York, so the UTC column simply equals the raw field and the ET column is raw − 4 h.
* The wrong number is **in the raw field itself, for Apple only**. For Apple the raw field, read as UTC, is later than the true UTC instant (index/hdr Eastern wall clock converted to UTC) by **exactly the Eastern UTC offset**: +4 h for EDT filings, +5 h for EST filings. Equivalently the raw field = Eastern wall clock + 2 × offset, i.e. the Eastern→UTC offset appears to have been applied twice upstream. For JNJ and ADBE the raw field equals the true UTC instant exactly.
* Breadth: across **26 Apple filings** sampled from 2024-01..2026-09 (10-K, 10-Q, 8-K, Form 4, 144, 13G/A, …) the difference was always +4 h (EDT: 18 filings) or +5 h (EST: 8 filings), never 0; across **11 JNJ and 11 ADBE filings** it was always 0 (`acceptance_time_audit_output.txt`, per-filing detail in the JSON). So it is a systematic property of the Apple CIK's feed as retrieved, not a one-off.
* Which source is right? The index page and the `.hdr.sgml` header are two independent renderings of the same acceptance record and agree to the second. The Apple raw values would also imply implausible clock times (earnings 8-Ks at 20:30 ET, Form 4s at 22:30 ET) while the index values are pre-market 06:01 ET for 10-Ks and 16:30 ET for the earnings 8-K; this plausibility argument is supporting only. **Why the SEC feed is off for Apple I could not determine and do not assert.**
* Impact: `known_at` for the two Apple 10-Ks was **4 hours too late** in v1 (conservative for look-ahead: a later known_at never leaks the future, but it understates availability). Financial values were not affected. Dates (filing date) were correct.

## Fix

* `extract.py` v2 takes the index page "Accepted" (asserted equal to the hdr value) as authority, converts Eastern→UTC with the IANA zone (DST-correct), and keeps the raw field verbatim in `acceptance_raw_submissions_field` with an `acceptance_check` flag (`raw_field_matches_index` / `RAW_FIELD_MISMATCH_index_used`). 56 of 160 rows (all Apple) carry the mismatch flag.
* Regenerated: `data_points.csv`, `REPORT.md` (filings table + correction note), `results.json`, `recompute_output.txt`.
* Tests: `tests/test_real_data_sample_20261011.py` (failing before the fix: the new columns did not exist; passing after) check `acceptance_utc == index_ET→UTC` and `acceptance_et` wall clock == index for all 160 rows.
* `verify_inputs.py` re-fetches and compares index page, hdr and submissions field on every reproduction run.
* Not done: a generic runtime guard that cross-checks every future submissions field against its index page; for a production ingester use the index/hdr time (or the raw field only after a per-CIK consistency check).
