# New factor candidates — 2026-10-11

Status: **research proposal. All candidates NOT_VALIDATED. No claim of alpha.** Research only; no trading actions (AGENTS.md rule 7).
Scope: US equities and China A-shares. Not duplicates of the seven locked components (Amihud; Residual Volatility; Residual Momentum; Surprise Discrepancy; Capital Allocation Intent; Effective Factor Dimension; Exposure Graph Multiplex).

## How to read this

* **Citations**: each was checked by web search (journal, volume, pages, DOI/publisher page) on 2026-10-11. Where only part of a citation was confirmed, it says so. I did **not** read the full papers; claims about their findings come from abstracts/search summaries.
* **Evidence quality caveat (applies to all)**: Hou, Xue & Zhang, "Replicating Anomalies", RFS 33(5), 2019-2133 (2020) report that, after controlling for microcaps, most of 452 replicated anomalies fail conventional significance tests. McLean & Pontiff, JF 71(1), 5-32 (2016) report 97 published predictors lose ~26% out of sample and ~58% post-publication. So every candidate below starts with a **post-publication decay prior**, and every hurdle is set above "the paper's number".
* **China A-share caveat**: I did not verify A-share-specific evidence for these candidates, except that Liu, Stambaugh & Yuan, "Size and value in China", JFE 134(1), 48-69 (2019) show China needs local construction choices (excluding the smallest 30% of firms, earnings-yield value). Treat all A-share applicability as an untested hypothesis. A-share frictions to handle in validation: T+1, ±10%/±20% price limits, suspensions, retail-heavy turnover, short-sale constraints.
* **PIT convention**: US fundamentals `known_at` = SEC acceptance datetime; China = exchange announcement date (not period end). Prices `known_at` = end of trading day. Restatements are new facts with new `known_at`.
* **Ranking** = evidence quality (E) × feasibility (F) × novelty vs locked set (N), each 1-5, product shown.

## Ranked summary

| # | Candidate | E | F | N | Score | Label |
|---|-----------|---|---|---|-------|-------|
| 1 | Gross profitability (GP/A) | 5 | 4 | 5 | 100 | implemented (contract) |
| 2 | Sloan balance-sheet accruals | 4 | 4 | 5 | 80 | implemented (contract) |
| 3 | MAX lottery demand | 4 | 5 | 4 | 80 | implemented (contract) |
| 4 | Betting-against-beta (low beta) | 4 | 4 | 4 | 64 | candidate |
| 5 | 52-week-high proximity | 4 | 5 | 3 | 60 | candidate; overlap with momentum to be tested |
| 6 | Net share issuance (composite) | 4 | 4 | 3 | 48 | candidate; partial overlap with Capital Allocation Intent |
| 7 | Industry momentum | 3 | 4 | 3 | 36 | candidate; overlap with Residual Momentum to be tested |
| 8 | Overnight-vs-intraday return split | 3 | 3 | 4 | 36 | **weak** (cost/microstructure) |
| 9 | Distress risk (CHS) | 2 | 3 | 3 | 18 | **weak** (anomalously low returns, complex, decayed) |

Ties 2/3 broken by implementation simplicity. Top 3 are implemented as `NOT_VALIDATED` contracts (`contracts/factors/candidates_20261011.json`, `factor_candidates/`).

---

## 1. Gross profitability (GP/A)
* **Mechanism**: profitable firms are productive assets bought cheaply relative to valuation; value and profitability are complementary (Novy-Marx). Plausibly also quality/investor underreaction.
* **Formula**: `GP/A = GrossProfit / TotalAssets`, with GrossProfit = Revenue − COGS (gross **profit** over total **assets**; this is not gross margin, which divides by revenue). Latest statement with `known_at ≤ as_of` (timezone-aware timestamp). Novy-Marx full text not read in this repo; the revenue−COGS construction is a reference choice.
* **Inputs**: revenue, COGS, total assets. US: SEC XBRL (`known_at` = acceptance time). CN: exchange/CNINFO filings (`known_at` = announcement date).
* **Crowding/publication**: published 2013; widely used in quality sleeves; decay expected. Crowded in US large caps.
* **Failure modes**: banks/insurers lack COGS; CN COGS definitions vary; asset-light vs asset-heavy industry bias (neutralise by industry); intangible capitalisation differences; correlation with value/quality factors.
* **Literature**: Novy-Marx (2013), "The other side of value: the gross profitability premium", JFE 108(1), 1-28, doi:10.1016/j.jfineco.2013.01.003 (verified).
* **Feasibility**: US high (free XBRL, but PIT-restatement handling needed); CN medium (vendor field mapping).
* **Hurdle**: out-of-sample (post-2013 US; separate A-share sample) industry-neutral long-short with Newey-West t > 3 after controlling for size, value (EP/BM), momentum and existing locked factors; positive in ≥ 2 of 3 sub-periods; survives microcap exclusion (bottom 30% CN / NYSE 20th pct US); cost-adjusted net Sharpe > 0.3.

## 2. Sloan balance-sheet accruals
* **Mechanism**: accrual component of earnings is less persistent than cash flow; investors fixate on earnings (Sloan).
* **Formula** (verified against Sloan 1996 full text, pp. ~293-294 of the JSTOR scan, page numbers approximate; see `evidence/pr48_sources.md`): `((ΔCA − ΔCash) − (ΔCL − ΔSTD − ΔTP) − Dep) / avg(TA_t, TA_{t−1})`, where Dep is depreciation **and amortization** expense (Compustat item 14), subtracted, and the denominator is the average of beginning- and end-of-year total assets. Hypothesis: higher → lower returns. Mapping of Compustat items 34/71 to XBRL/IFRS tags is UNVERIFIED.
* **Inputs**: seven balance-sheet/IS items, two consecutive fiscal years, same source + `known_at` rules as #1.
* **Crowding/publication**: published 1996; very well known; evidence of decay after publication in many studies (not re-verified here beyond the McLean-Pontiff aggregate).
* **Failure modes**: M&A and divestitures distort balance-sheet accruals (cash-flow-statement version preferable if data allows); strict missing rule drops many names; fiscal-year-end seasonality; CN earnings-management regimes differ; announced restatements.
* **Literature**: Sloan (1996), "Do stock prices fully reflect information in accruals and cash flows about future earnings?", The Accounting Review 71(3), 289-315 (verified).
* **Feasibility**: medium-high US; CN medium (tax-payable/STD line items inconsistent).
* **Hurdle**: as #1, plus: effect concentrated around subsequent earnings announcements (mechanism check), robust to using cash-flow-statement accruals, and not subsumed by GP/A (Novy-Marx argues GP/A explains many earnings-related anomalies — test explicitly for incremental alpha).

## 3. MAX lottery demand
* **Mechanism**: investors with poor diversification/preference for lottery payoffs overpay for stocks with extreme positive returns (Bali-Cakici-Whitelaw).
* **Formula (primary, original)**: `MAX = max daily return within the previous calendar month` (BCW: portfolios sorted on MAX over the previous month; checked in the Aug-2008 NYU working paper, not the published JFE text). Implemented as variant `prior_calendar_month`. **Alternative variant (not the paper's definition)**: max over the trailing fixed 21 trading days, `fixed_21_trading_days`. Hypothesis: higher → lower returns. `min_obs=15` is a project choice, not from the paper.
* **Inputs**: adjusted daily returns (`known_at` = day close), explicit trading calendar.
* **Crowding/publication**: published 2011; price-only so cheap and crowded in shorts; short leg is costly/capacity-limited (hard to borrow, esp. A-shares where shorting is constrained).
* **Failure modes**: short-leg dominated; microcap dominated (use size screens); overlaps idiosyncratic volatility and short-term reversal (locked Residual Volatility); A-share price limits censor MAX; corporate-action adjustments.
* **Literature**: Bali, Cakici & Whitelaw (2011), "Maxing out: stocks as lotteries and the cross-section of expected returns", JFE 99(2), 427-446, doi:10.1016/j.jfineco.2010.08.014 (verified).
* **Feasibility**: high (price-only) but needs PIT adjusted prices.
* **Hurdle**: out-of-sample after controlling for Residual Volatility, short-term reversal, size, liquidity (Amihud); long-only (underweight) implementability test since shorts are not assumed; value-weighted and ex-microcap t > 3; in A-shares assess with limit-up days censored vs included.

## 4. Betting against beta (low-beta)
* **Mechanism**: leverage/margin-constrained investors bid up high-beta assets (Frazzini-Pedersen).
* **Formula**: `β_i = ρ_{i,m} · σ_i/σ_m` with 1y daily vol and 5y 3-day-overlap correlation shrunk `β = 0.6 β_ts + 0.4` (Frazzini-Pedersen construction — verify exact windows against the paper before implementing); rank by β ascending.
* **Inputs**: adjusted daily returns, market index returns (`known_at` day close).
* **Crowding/publication**: published 2014, very well known (low-vol ETFs); risk of drawdown when funding conditions tighten (paper's own prediction 3).
* **Failure modes**: sector concentration in defensives; rate-sensitivity; overlap with Residual Volatility (locked) is **to be tested** (no correlation data computed; do not assume it is high or low); leverage-dependent BAB implementation, which is out of scope here.
* **Literature**: Frazzini & Pedersen (2014), JFE 111(1), 1-25, doi:10.1016/j.jfineco.2013.10.005 (verified).
* **Feasibility**: high.
* **Hurdle**: incremental alpha after Residual Volatility; unlevered, sector-neutral version positive net of costs; stable in both bull and tightening-funding regimes.

## 5. 52-week-high proximity
* **Mechanism**: anchoring to the 52-week high causes underreaction (George-Hwang).
* **Formula**: `P_t / max(P over trailing 252 trading days)`.
* **Inputs**: unadjusted-consistent daily closes.
* **Crowding**: published 2004; overlap with momentum / Residual Momentum is **to be tested** (not measured here).
* **Failure modes**: momentum crashes; January; A-share price-limit and suspension gaps; adjustments for splits.
* **Literature**: George & Hwang (2004), JF 59(5), 2145-2176, doi:10.1111/j.1540-6261.2004.00695.x (verified).
* **Feasibility**: high.
* **Hurdle**: incremental alpha over Residual Momentum, post-2004 sample only, net of costs.

## 6. Net share issuance (composite)
* **Mechanism**: firms issue into overvaluation / repurchase when undervalued (Pontiff-Woodgate; Daniel-Titman composite issuance).
* **Formula**: `ln(SharesOut_t / SharesOut_{t−k})` split-adjusted (k = 1y; or Daniel-Titman composite issuance with 5y window — verify definition before implementing).
* **Inputs**: shares outstanding with `known_at` filing/announcement; split-adjustment factors.
* **Crowding**: published 2006/2008. **Overlap warning**: Capital Allocation Intent contains buyback/issuance signals — must be tested for incremental information, otherwise reject as duplicate.
* **Failure modes**: split/consolidation data errors; A-share rights issues, lock-up expiries, private placements dominate (non-comparable to US SEOs); stock compensation noise.
* **Literature**: Pontiff & Woodgate (2008), "Share issuance and cross-sectional returns", JF 63(2), 921-945 (page range from search summary; publisher DOI 10.1111/j.1540-6261.2008.01335.x); Daniel & Titman (2006), "Market reactions to tangible and intangible information", JF 61(4), 1605-1643, doi:10.1111/j.1540-6261.2006.00884.x (both verified; summaries note the effect was absent before 1970).
* **Feasibility**: medium (share-count PIT quality).
* **Hurdle**: nonzero incremental alpha vs Capital Allocation Intent; A-share variant must separate event types.

## 7. Industry momentum
* **Mechanism**: industry-level return continuation (Moskowitz-Grinblatt).
* **Formula**: value-weighted industry return over months t−12…t−2 (skip last month); stock gets its industry's score.
* **Inputs**: PIT industry classification (`known_at` of reclassification), returns.
* **Crowding/overlap**: overlap with Residual Momentum to be tested; novelty moderate; few independent bets (≈ 10-30 industries) → low statistical power.
* **Failure modes**: GICS/CSRC/SW reclassification look-ahead; sector rotations; few effective observations.
* **Literature**: Moskowitz & Grinblatt (1999), JF 54(4), 1249-1290 (verified).
* **Feasibility**: medium (PIT industry history).
* **Hurdle**: incremental alpha over Residual Momentum with multiple-testing-adjusted t > 3.

## 8. Overnight vs intraday return split — **WEAK**
* **Mechanism**: persistent overnight/intraday return patterns reflecting clientele/tug of war between investor types (Lou-Polk-Skouras).
* **Formula**: trailing average of `open_t/close_{t−1} − 1` minus average of `close_t/open_t − 1`.
* **Inputs**: official open and close prices (`known_at` day close).
* **Crowding**: published 2019; very high turnover so net-of-cost value is doubtful.
* **Failure modes**: open-price definition (auction vs first trade); A-share call auction; costs; capacity.
* **Literature**: Lou, Polk & Skouras (2019), JFE 134(1), 192-213, doi:10.1016/j.jfineco.2019.03.011 (verified bibliographic details only; findings not independently reviewed).
* **Feasibility**: medium (clean open prices).
* **Hurdle**: net-of-cost positive at monthly rebalance with ≥ 2 independent open-price sources agreeing.

## 9. Distress risk (Campbell-Hilscher-Szilagyi) — **WEAK**
* **Mechanism**: distress-risk-based failure probability; distressed stocks had *anomalously low* returns historically (Campbell et al.), i.e. a mispricing, not a risk premium.
* **Formula**: logit failure model on NI/MTA, TL/MTA, excess return, σ, cash/MTA, size, MB, price (coefficients must be re-estimated PIT, not copied).
* **Inputs**: fundamentals, market equity, failure/delisting labels (survivorship-sensitive).
* **Failure modes**: model re-estimation leakage; delisting data quality; CN special-treatment (ST) regime is a different default process; overlap with Residual Volatility, size, profitability (#1) to be tested (not measured).
* **Literature**: Campbell, Hilscher & Szilagyi (2008), JF 63(6), 2899-2939 (verified bibliographic details).
* **Feasibility**: low-medium.
* **Hurdle**: expanding-window PIT model; incremental to GP/A + Residual Volatility; failure-prediction AUC out of sample before any return claim.

---

## Not verified / honest gaps
* Findings were taken from abstracts/search summaries, not full-text review.
* No A-share-specific evidence for any candidate was verified.
* Exact BAB beta-estimation windows and Daniel-Titman composite issuance definition must be checked against the papers before implementation.
* No data was run; no performance numbers are claimed. Data pipelines for PIT fundamentals and adjusted prices are not in the repo.


## Filing-basis and PIT contract for the reference implementations (added after review)
Every fundamental record carries: `security`, `period_end` (date), `known_at` (**timezone-aware datetime**; date-only and naive values are rejected), `source`, `period_type` (annual / quarterly / ttm), `currency`, `unit_scale`, `statement_ref` (accession / form / original-statement reference) and `revision` (0 = original). Daily return rows carry `adjustment_basis` and `revision`.

Version selection (order-independent): visible records (`known_at ≤ as_of`) for one fact are ordered by `(known_at, revision)`; revision must not decrease as `known_at` increases; records with equal revision must have identical content; otherwise `AmbiguousVersionError` is raised. Duplicate trading days raise `TradingCalendarError`. Synthetic tests prove code behaviour only, not factor validity.
