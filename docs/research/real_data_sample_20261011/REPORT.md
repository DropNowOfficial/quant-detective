# Quant Detective – Workstream B: real-data small sample (REPORT)

Generated 2026-10-11 (America/New_York). This version (v2) was revised by the 2026-10-11 audit (see `docs/research/evidence/acceptance_time_audit.md`, `audit_code_version.md`); the pre-audit values are preserved as 'previous' where they changed. Raw data is NOT in Git (AGENTS.md rule 6): reproduce with `reproduce.md` / `run_all.sh`. No credentials; no live/production. **This sample proves only that inputs can be traced and recomputed. It makes NO claim of investment validity, no backtest, no returns.**

## 1. SEC sample (Part 1)

Companies chosen: **Apple (AAPL, CIK 0000320193), Johnson & Johnson (JNJ, CIK 0000200406), Adobe (ADBE, CIK 0000796343)**; periods = last two fiscal years that have a 10-K (FY2024, FY2025 per each company's own fiscal calendar). All 10-K (no 10-K/A in any of the three submissions `recent` lists). Currency USD, values stored in full USD in XBRL (R-files display $ millions). All XBRL values in `data_points.csv` were machine-compared to the face-statement R-file values rendered from the same accession: 112/112 values actually used by the GP/A and Sloan computations match their R-file value (counts below are derived by `derive_counts.py` into `counts.json`). The other 24 rows with an R-file mismatch are context rows (a third, older year column) that no computation uses.

### Filings (accession, form, filing date, acceptance time: authoritative = SEC filing-index 'Accepted' (US Eastern), converted to UTC)

| Ticker | Accession | Form | Filing date | acceptance (UTC, from index page Accepted ET) | acceptance (ET) | Period end | Document |
|---|---|---|---|---|---|---|---|
| AAPL | 0000320193-24-000123 | 10-K | 2024-11-01 | 2024-11-01T10:01:36.000Z | 2024-11-01T06:01:36-04:00 | 2024-09-28 | https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm |
| AAPL | 0000320193-25-000079 | 10-K | 2025-10-31 | 2025-10-31T10:01:26.000Z | 2025-10-31T06:01:26-04:00 | 2025-09-27 | https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm |
| ADBE | 0000796343-25-000004 | 10-K | 2025-01-13 | 2025-01-13T21:02:17.000Z | 2025-01-13T16:02:17-05:00 | 2024-11-29 | https://www.sec.gov/Archives/edgar/data/796343/000079634325000004/adbe-20241129.htm |
| ADBE | 0000796343-26-000003 | 10-K | 2026-01-15 | 2026-01-15T22:01:55.000Z | 2026-01-15T17:01:55-05:00 | 2025-11-28 | https://www.sec.gov/Archives/edgar/data/796343/000079634326000003/adbe-20251128.htm |
| JNJ | 0000200406-25-000038 | 10-K | 2025-02-13 | 2025-02-13T21:07:39.000Z | 2025-02-13T16:07:39-05:00 | 2024-12-29 | https://www.sec.gov/Archives/edgar/data/200406/000020040625000038/jnj-20241229.htm |
| JNJ | 0000200406-26-000016 | 10-K | 2026-02-11 | 2026-02-11T21:06:11.000Z | 2026-02-11T16:06:11-05:00 | 2025-12-28 | https://www.sec.gov/Archives/edgar/data/200406/000020040626000016/jnj-20251228.htm |

**Acceptance-time correction (audit).** v1 of this report took the submissions-JSON field `acceptanceDateTime` as UTC and printed an ET conversion; for the two Apple 10-Ks that was 4 hours too late. The filing index page 'Accepted' (EDT/EST wall clock) and the accession header `<ACCEPTANCE-DATETIME>` agree with each other and are used here. Raw submissions field for AAPL: `2024-11-01T14:01:36.000Z` and `2025-10-31T14:01:26.000Z`; index 'Accepted': `2024-11-01 06:01:36` and `2025-10-31 06:01:26` (Eastern, EDT) = 10:01:36Z / 10:01:26Z. JNJ and ADBE raw fields equal the index-derived UTC exactly. Full table of raw values and root-cause analysis: `docs/research/evidence/acceptance_time_audit.md`. `data_points.csv` keeps all three values per row. Use acceptance (not filing date) as `known_at`.

### Tags used (exact)

- **Gross profit**: `us-gaap:GrossProfit` as reported (income statement R-page) for AAPL, ADBE; JNJ: `us-gaap:GrossProfit` exists in companyfacts but is NOT a face line in JNJ's income statement R-page I parsed (see issue U3) – value taken from the XBRL fact, not from a displayed line.
- **Total assets**: `us-gaap:Assets` (current FY end and prior FY end, both from the same 10-K's comparative column).
- **Sloan balance-sheet components**: ΔCA = `AssetsCurrent`; ΔCash = change in (`CashAndCashEquivalentsAtCarryingValue` + short-term investments), short-term investments tag: AAPL `MarketableSecuritiesCurrent` (BS 'Marketable securities', current), JNJ `MarketableSecuritiesCurrent` (BS 'Marketable securities'), ADBE `ShortTermInvestments` (BS 'Short-term investments'); the cash-only v1 values are kept below as 'previous (cash only)'; ΔCL = `LiabilitiesCurrent`; ΔSTD (debt in current liabilities): AAPL = `CommercialPaper` + `LongTermDebtCurrent`; JNJ = `ShortTermBorrowings` (BS line 'Loans and notes payable'); ADBE = `DebtCurrent` (BS line 'Debt'); ΔTP = `AccruedIncomeTaxesCurrent` (BS 'Accrued taxes on income' JNJ R3; 'Income taxes payable' ADBE R3; AAPL: 10-K note 'Consolidated Financial Statement Details – Other Current Liabilities (Details)' R50.htm in 0000320193-24-000123 and R49.htm in 0000320193-25-000079, not on the face balance sheet); Dep = `us-gaap:DepreciationDepletionAndAmortization` ONLY (one tag, the cash-flow-statement total, for all three companies; no other amortization/accretion tag added or removed). Labels: AAPL 'Depreciation and amortization'; JNJ 'Depreciation and amortization of property and intangibles'; ADBE 'Depreciation, amortization and accretion' — **ADBE's line includes accretion of unknown size, so scope is not identical across companies** (v1 used the same tag but did not state this component list).
- **CF version**: `NetIncomeLoss` and `NetCashProvidedByUsedInOperatingActivities`.
- Period type: FY duration facts 360–372 days; instants for balance sheet.

Full per-datapoint record (accession, form, filing date, acceptance UTC+ET, period end, tag, unit, currency, period type, start/end, value, cross-filing value comparison, 10-K/A check, R-file cross-check): `data_points.csv`.

**Exact counts (script-derived: `derive_counts.py` -> `counts.json`, checked by `tests/test_sample_counts_20261011.py`):** `data_points.csv` has 160 rows (6 filings; AAPL 56, JNJ 52, ADBE 52). 112 used inputs are the rows actually read by the computations (`recompute.py` records them in `used_inputs.json`; AAPL 40, JNJ 36, ADBE 36). The remaining 48 rows are context/extra (prior-year flow values of the same filings that no computation reads). Earlier versions of this report stated other row and value counts; those were stale and are withdrawn.

**Cross-filing value comparison (field renamed; it replaces `later_restatement`).** For each row the script compares the same tag, unit, start and end in every other filing in companyfacts. `later_filed_value_difference` lists only filings with a strictly later public filing date (`filingDate`, a date, not a time) whose value differs; `earlier_or_same_day_value_difference` lists differing values in older or same-day filings, which cannot be ordered by date. **Neither field implies a restatement, and an empty field does NOT imply the absence of one**: companyfacts coverage is not guaranteed complete and the comparison is by filing date, not by acceptance time. Result: 0 rows have a later-filed difference (0 of the 112 used rows); 1 context row (JNJ FY2023 GrossProfit in the 2025 filing differs from the 2023 filing's value) has an earlier-filed difference. The v1 field `later_restatement` wrongly flagged that older 2023 filing as a 'later restatement' of the 2025 filing; and the v1 sentence that inferred an absence of restatements from it is withdrawn.

### Definitions recomputed by `recompute.py` (standalone, stdlib only)

- GP/A = GrossProfit / Assets (end of fiscal year). (Denominator is year-end total assets, not average – stated choice.)
- Sloan (1996, TAR 71(3) p.289-315) accruals = (ΔCA − ΔCash) − (ΔCL − ΔSTD − ΔTP) − Dep, scaled by average total assets (A_t + A_t-1)/2. Verified against the paper full text I downloaded (`raw/papers/sloan1996_cuhk.pdf`, line: 'Accruals = the change in non-cash current assets, less the change in current liabilities (exclusive of short-term debt and taxes payable), less depreciation expense, all divided by average total assets').
- CF alternative (labelled, NOT Sloan's original): (NetIncome − CFO) / average total assets. Differs from the BS version because of non-operating/non-current accruals, acquisitions, FX etc.

### Inputs and results (USD; ΔX = X(FY end) − X(prior FY end))

| Ticker | FY end | Accession | GrossProfit | Assets_t | Assets_t-1 | Avg assets | ΔCA | ΔCash only | ΔShort-term inv. | ΔCash+STI (used) | ΔCL | ΔSTD | ΔTP | Dep | NI | CFO |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AAPL | 2024-09-28 | 0000320193-24-000123 | 180,683,000,000 | 364,980,000,000 | 352,583,000,000 | 358,781,500,000 | 9,421,000,000 | -22,000,000 | 3,638,000,000 | 3,616,000,000 | 31,084,000,000 | 5,072,000,000 | 17,782,000,000 | 11,445,000,000 | 93,736,000,000 | 118,254,000,000 |
| AAPL | 2025-09-27 | 0000320193-25-000079 | 195,201,000,000 | 359,241,000,000 | 364,980,000,000 | 362,110,500,000 | -5,030,000,000 | 5,991,000,000 | -16,465,000,000 | -10,474,000,000 | -10,761,000,000 | -550,000,000 | -13,585,000,000 | 11,698,000,000 | 112,010,000,000 | 111,482,000,000 |
| JNJ | 2024-12-29 | 0000200406-25-000038 | 61,350,000,000 | 180,104,000,000 | 167,558,000,000 | 173,831,000,000 | 2,398,000,000 | 2,246,000,000 | -651,000,000 | 1,595,000,000 | 4,039,000,000 | 2,532,000,000 | 779,000,000 | 7,339,000,000 | 14,066,000,000 | 24,266,000,000 |
| JNJ | 2025-12-28 | 0000200406-26-000016 | 63,937,000,000 | 199,210,000,000 | 180,104,000,000 | 189,657,000,000 | -269,000,000 | -4,396,000,000 | -24,000,000 | -4,420,000,000 | 3,805,000,000 | 2,512,000,000 | -2,384,000,000 | 7,503,000,000 | 26,804,000,000 | 24,530,000,000 |
| ADBE | 2024-11-29 | 0000796343-25-000004 | 19,147,000,000 | 30,230,000,000 | 29,779,000,000 | 30,004,500,000 | 148,000,000 | 472,000,000 | -428,000,000 | 44,000,000 | 2,270,000,000 | 1,499,000,000 | 34,000,000 | 857,000,000 | 5,560,000,000 | 8,056,000,000 |
| ADBE | 2025-11-28 | 0000796343-26-000003 | 21,218,000,000 | 29,496,000,000 | 30,230,000,000 | 29,863,000,000 | -1,069,000,000 | -2,182,000,000 | 891,000,000 | -1,291,000,000 | -321,000,000 | -1,499,000,000 | 34,000,000 | 818,000,000 | 7,130,000,000 | 10,031,000,000 |

| Ticker | FY end | GP/A | Sloan BS accruals, cash+ST inv. (USD) | **Sloan scaled (cash+STI, current)** | previous (cash only) scaled | CF-version accruals (NI−CFO) | CF-version scaled |
|---|---|---|---|---|---|---|---|
| AAPL | 2024-09-28 | 0.495049 | -13,870,000,000 | **-0.038659** | -0.028519 | -24,518,000,000 | -0.068337 |
| AAPL | 2025-09-27 | 0.543371 | -9,628,000,000 | **-0.026589** | -0.072058 | 528,000,000 | 0.001458 |
| JNJ | 2024-12-29 | 0.340637 | -7,264,000,000 | **-0.041788** | -0.045533 | -10,200,000,000 | -0.058678 |
| JNJ | 2025-12-28 | 0.320953 | -7,029,000,000 | **-0.037062** | -0.037188 | 2,274,000,000 | 0.011990 |
| ADBE | 2024-11-29 | 0.633377 | -1,490,000,000 | **-0.049659** | -0.063924 | -2,496,000,000 | -0.083188 |
| ADBE | 2025-11-28 | 0.719352 | -1,740,000,000 | **-0.058266** | -0.028430 | -2,901,000,000 | -0.097144 |

**Sloan stays NOT usable for scoring stocks**: Compustat-item → XBRL-tag mapping (items 1, 14, 34, 71) is unverified; D&A scope differs by company (ADBE includes accretion); Sloan's own text says dCash = "change in cash/cash equivalents (Compustat item 1)" — that item 1 also contains short-term investments is Compustat documentation that is not verified in this repo; the user-requested cash+short-term-investments scope is implemented and cash-only is shown as previous.

Formulas shown with numbers are in `recompute_output.txt` (e.g. AAPL FY2024 GP/A = 180,683,000,000 / 364,980,000,000 = 0.495049). These are descriptive recomputations of 6 firm-years only; they are not evidence of any factor premium.

### Caveats / unresolved in Part 1

- **U1 AAPL tax payable (resolved as to source)**: `AccruedIncomeTaxesCurrent` is not on Apple's face balance sheet; it is a line in the 10-K note *Consolidated Financial Statement Details – Other Current Liabilities (Details)*: R50.htm of 0000320193-24-000123 (FY24 $26,601M; FY23 $8,819M) and R49.htm of 0000320193-25-000079 (FY25 $13,016M; FY24 $26,601M), located by this workstream from each filing's FilingSummary.xml and matched exactly to the XBRL facts (`rfile_ref` column). The user had independently verified the value exists in the notes. The tag→Sloan item 71 mapping is still unverified; ΔTP for AAPL (+17.8B, −13.6B) is large.
- **U2 Cash definition (fixed)**: ΔCash now = cash and equivalents + short-term investments (AAPL/JNJ marketable securities current; ADBE short-term investments). v1 cash-only values are shown as 'previous (cash only)'. Long-term marketable securities (AAPL non-current) are NOT included.
- **U3 JNJ GrossProfit**: companyfacts has GrossProfit for JNJ (FY2024 61,350M = ties to 10-K R-file extraction as 'matches' flag True), but I did not confirm that it is a face-statement line vs derived; it equals R-file value per the match check, so it is in the filing's XBRL.
- **U4 STD for JNJ/ADBE** uses a single tag; if a current portion of long-term debt is embedded in other lines, ΔSTD is understated. JNJ's 10-K BS shows only 'Loans and notes payable' as debt in current liabilities (R3).
- **U5 Dep**: single CF-statement total-D&A tag for all; ADBE's line includes accretion (not separable from the data I hold); AAPL's note-level `Depreciation` (8.0B FY25) = PP&E depreciation only is not used. Sloan's equation says 'depreciation and amortization expense (item 14)' while his prose says 'depreciation expense'.
- **Rejected companies (gaps not filled)**: MSFT – no standard D&A tag in 10-K companyfacts (only components such as `Depreciation`, `AmortizationOfIntangibleAssets`); CSCO – `DepreciationDepletionAndAmortization` = 700M is a note-level number, while the cash-flow statement uses custom `csco_DepreciationAmortizationAndOther` (2,811M, includes 'other'); KO – no tag for total current debt ('Loans and notes payable') for FY2024-25; PG – no GrossProfit tag; PEP – no `AccruedIncomeTaxesCurrent`/taxes-payable tag recent. CSCO/KO/PG/PEP raw files saved in `raw/` for audit (KO/PG/PEP companyfacts only).
- Data is as retrieved 2026-10-11 from data.sec.gov (companyfacts is a restated-to-latest-frame view with accn per fact; I selected facts by accession of the filing itself, so values are as-originally-filed in that 10-K).
- Fiscal year labels are mine (`fy` in companyfacts is the filing's fiscal year; e.g. FY2025 facts may be labelled fy=2025 for the prior-year comparatives).

## 2. Price sample for MAX (Part 2)

Retrieval: 2026-10-11 ~07:55 ET (11:54:59Z–11:56:08Z). Window 2026-07-01 → 2026-10-09 (71 trading days; last available session 2026-10-09). 'Prior full calendar month' relative to 2026-10-11 = **September 2026** (21 trading days; Labor Day 2026-09-07).

**Sources tried**
- Stooq CSV (`stooq.com/q/d/l/`): **BLOCKED** – returns a JavaScript browser-verification page, no CSV; not bypassed.
- Alpha Vantage: skipped (needs API key).
- **Yahoo Finance chart API v8** (`query1.finance.yahoo.com/v8/finance/chart/{T}?interval=1d&events=div|split`): WORKED, no key. Unofficial/undocumented endpoint; fields: raw `close` (split-adjusted as quoted by Yahoo, not dividend-adjusted – Yahoo's convention: historical quote closes are split-adjusted) and `adjclose` (split- AND dividend-adjusted). Adjustment convention is therefore Yahoo's, not independently documented by a contract; no splits occurred in the window (`events.splits` absent); dividends in window: AAPL $0.27 (ex-date 2026-08-10 ET approx, ts 1786368600), JNJ $1.34 (ts 1787664600 ≈ 2026-08-25), ADBE none. Because dividends fall in August, September raw and adjusted returns coincide.
- **Nasdaq.com historical JSON** (`api.nasdaq.com/api/quote/{T}/historical`): WORKED, no key, used only as a raw-close cross-check (it has no adjusted close).

Raw files: `_raw/prices/yahoo_{AAPL,JNJ,ADBE}.json`, `_raw/prices/nasdaq_*.json`, `_raw/prices/stooq_*.csv` (block pages, kept as evidence). SHA256 in `SHA256SUMS.txt`. Per-ticker daily table (date, raw close, adjusted close, return from adjusted close): `daily_{TICKER}.csv` is raw market data and is **no longer tracked in Git** (AGENTS.md rule 6); `price_manifest.json` records SHA256, row count, date range and a raw-close hash, and `run_all.sh` regenerates the CSVs into the untracked `_out/`. They remain in earlier commits' history (e.g. `git show e259a4e:docs/research/real_data_sample_20261011/daily_AAPL.csv`); history cleanup is proposed in `docs/research/evidence/audit14_fixes_20261011.md`, not executed. Script (v2): `max_prices.py` now calls the repo's fixed `factor_candidates.max_lottery(..., calendar_tz='America/New_York')` and asserts equality with an independent standalone computation; `max_results.json` also records the same instant in UTC / New York / Tokyo / Auckland spellings (identical results) and the code version. Values are unchanged from v1. Reproduction: `reproduce.md`.

Checks in script: dates unique (assert), sorted ascending (assert), no null closes (missing_dates = [] for all three), 71 rows each, raw close vs Nasdaq.com close: **0 mismatches > $0.005 on all 71 days for all 3 tickers** (cross-check covers raw close only; both sources ultimately derive from exchange/consolidated tape so it is a consistency check, not fully independent verification; adjusted close is **not cross-checked** by a second source).

| Ticker | MAX primary: max daily return in Sept 2026 (adj close) | date | Alt: max over last 21 trading-day returns ending 2026-09-30 | Alt: 21 returns ending 2026-10-09 (window 2026-09-11..10-09) |
|---|---|---|---|---|
| AAPL | 0.035612 | 2026-09-10 | 0.035612 (2026-09-10) | 0.017454 (2026-09-11) |
| JNJ | 0.020087 | 2026-09-01 | 0.020087 (2026-09-01) | 0.019339 (2026-10-09) |
| ADBE | 0.053007 | 2026-09-14 | 0.053007 (2026-09-14) | 0.053007 (2026-09-14) |

(The 21-day window ending 09-30 equals September here only because September had exactly 21 trading days – the variants coincide by calendar accident, not by definition.) Daily return r_t = adj_t / adj_{t-1} − 1; September's first return uses the 2026-08-31 close. Values are single-month descriptive numbers; not a signal test.

## 3. Tool-permission inventory (executor) – proven by calls this session

| Capability | Status | Evidence (call → result excerpt) |
|---|---|---|
| (a) Retrieve X original post + timestamp | **PROVEN** (namespace `x`; note `user-X` namespace listing lacks post-read tools, only chat/bookmark/webhook/activity tools) | `x.get_users_by_username{username:SEC_News}` → id 1418247562662973451, created_at 2021-07-22T16:33:34Z. `x.get_users_posts{id:1418247562662973451,max_results:5,post.fields:created_at,edit_history_tweet_ids,edit_controls}` → post 1418288482141839368 `created_at 2021-07-22T19:14:59.000Z`, text 'We moved our Twitter handle to @SECGov…', url https://x.com/sec_news/status/1418288482141839368, edit_history_tweet_ids [1418288482141839368], edit_controls editable_until 2021-07-22T19:44:59Z |
| (b) Read a paper's full text | **PROVEN (via a public mirror)** | `curl` of `https://www.cuhk.edu.hk/acy2/workshop/June2009Wasley/1996TAR).pdf` → Sloan 1996 full PDF (733,503 bytes, sha256 229b1448…d2f6, 1,422 text lines via pdftotext); extracted body line: 'Accruals = the change in non-cash current assets, less the change in current liabilities (exclusive of short-term debt and taxes payable), less depreciation expense, all divided by average total assets.' Also arXiv 1706.03762 PDF fetched and text extracted (tool test). Publisher/JSTOR/ScienceDirect: not accessed (ScienceDirect URL guess returned 404; JSTOR is paywalled – only HTTP 200 landing, no full text claimed). Sloan's copyright status of the CUHK mirror not verified. |
| (c) Historical data access | **PROVEN** | SEC `data.sec.gov` submissions + companyfacts + `www.sec.gov/Archives` R-files (all 200 with descriptive User-Agent); Yahoo chart API and Nasdaq.com historical JSON (71 daily rows each). Blocked: Stooq (JS challenge). X full-archive search also returned Jan-2022 posts (10 results). |
| (d) Persistent running tasks | **MISSING (not proven)** | `GetDynamicTools{pattern:'(?i)routine|schedule|cron'}` → 0 matches; no routine/UpdateRoutine tool in my toolset; box has no `crontab` (`command not found`). The box is persistent for files/shell but I have no proven scheduler. I did not create anything. |

### X traceability / limits observed (no sentiment/factor use)

- **Historical**: `search_posts_all` (full-archive) returned posts from 2022-01-21..28 for `from:SECGov` with start/end_time; user timeline accepts start_time ≥ 2010-11-06. So history is retrievable via API.
- **Edits**: `edit_history_tweet_ids` and `edit_controls` (editable_until, edits_remaining) are returned; an edited post would show multiple ids. I did not observe an actual edited post, so edit-version recovery of prior text is NOT proven (API returns current text).
- **Deletions**: a nonexistent id returns `Not Found Error` ('Could not find post with id'). A deleted post is indistinguishable from never-existed through this call; deleted posts disappear from the API ⇒ survivorship bias unless one archives at capture time. No compliance-delete feed was tested.
- **Coverage**: API snapshot of currently existing public posts; `impression_count` returned 0 for old posts (metrics not reliable); retweets show as truncated 'RT @…' text; `@SEC_News` has only 1 post total. Coverage of a topic is limited by query/rate limits; no firehose proof.
- **Authorization/rate limits observed**: footer on each call: 30 reads/minute and 1,000 reads/day for this user (3 reads of the day were used before final count: 996–998 of 1000 left). Access is via the user's connected X authorization; terms of X API/Developer agreement limit redistribution and require deletion-compliance for stored content – I did not verify the license terms in this session (UNVERIFIED).
- Not used: no post text was turned into sentiment/news factor.

## 4. Files

- `REPORT.md` (this), `data_points.csv`, `recompute.py`, `recompute_output.txt`, `results.json`, `extract.py` (builds data_points.csv from raw), `explore.py` (tag scan)
- `max_prices.py`, `max_output.txt`, `max_results.json`, `price_manifest.json` (manifest of the untracked `daily_*.csv`)
- `SHA256SUMS.txt` (93 raw files), `raw/` : `submissions_<CIK>.json`, `companyfacts_<CIK>.json` (AAPL, JNJ, ADBE; plus rejected CSCO, KO, PG, PEP), `raw/filings/*_R*.htm` (face statements per accession), `_raw/prices/*`, `raw/papers/*`
- Retrieval times: SEC pulls ~07:51–07:55 ET 2026-10-11; prices 07:55 ET.

## 5. Unresolved / not done
- SEC R-files were fetched as rendered HTML (not the primary iXBRL doc); filing links point to the primary documents but I did not hash the primary documents themselves.
- Only 3 firms × 2 years; no universe, no PIT 'known_at' validation of any wider set. companyfacts is a derived SEC view; for production use the filing-level iXBRL/Financial Statement Data Sets.
- Yahoo adjustment convention is observed, not contractually documented; Stooq (documented) was blocked.
- JNJ DebtCurrent XBRL fact (8,500M) differs from BS 'Loans and notes payable' (8,495M) – I used the BS-line tag `ShortTermBorrowings`; DebtCurrent appears to be a note-level rounded figure.
- Compustat-item → XBRL-tag mapping unverified (Sloan not usable for scoring); ADBE D&A includes accretion; Yahoo adjusted close is re-downloaded with a tolerance fallback (see reproduce.md).
