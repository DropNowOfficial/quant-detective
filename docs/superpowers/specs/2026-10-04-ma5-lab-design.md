# MA5 research and teaching lab

The user requests a serious, reproducible study of the existing MA5/ATR/gap/volume method, source verification, portfolio annualization, falsification, an interactive teaching system, and delivery to DropNowOfficial/quant-detective. The authorized outcome is a research system and a practical paper-trading readiness path; no trades or automatic broker actions.

## Scope and choices

Add an isolated research_lab module to the existing evidence/Time Machine repository. Preserve existing contracts and tests. Import the v0.1.1 screener source as a separate optional local module only if its integration does not hide dependencies. Do not inject technical rules into the default fundamental research graph.

Use the existing five-year saved IBKR daily responses (43 current watchlist stocks + QQQ/SOXX) and independently acquired secondary snapshots for reconciliation. Primary expanded interval: 2022-01-03 through 2026-10-02, with available prior bars as warm-up. Preserve the older 2023-10-02 event study as a historical result, not a portfolio. Secondary longer history is diagnostic and uses its own source identity; never silently splice providers.

Freeze inherited lagged MA5/ATR5 geometry as primary. Separate completed-day MA5 and volume>=0.8 variants from the primary. Trend-only is a control, not the same strategy. All features are calculated from the signal day's completed session or earlier, and entry occurs no earlier than next session open. Ranking is relative 20-session return to QQQ, then prior dollar liquidity, then symbol. Current fixed universe is not historical membership.

Portfolio experiment: no leverage, initially USD10,000, at most five positions, each new position at most 20% of prior-close NAV, available-cash cap including entry cost, fractional units for research, no pyramiding, hold three trading sessions and exit at close. Queue only signal-state transitions. Round-trip cost primary 10 bps, sensitivity 0/25/50 bps; holding-period sensitivity 1/3/5/10 sessions, no winner selection. Cash interest and dividends are excluded in the primary price-return experiment and named in metrics. A second cost/capacity diagnostic is not a live commission quote.

Unknown or contradictory OHLCV is quarantined. Never exclude a trade based on future close validity. Required missing holding marks or corporate-action ambiguity blocks trustworthy performance status. Diagnostic paths may freeze marks with explicit flags; they cannot be called validated CAGR. Source known_at is retrieval time, so historical reconstruction is never claimed to be archived point-in-time evidence.

## Artifacts and interface

CLI loads authorized local cache, validates content/identity/hashes, computes features, simulates portfolios and baselines, writes immutable experiment directory and standalone HTML. Public Git includes code, source manifests/hashes, aggregate derived outputs, tests, and docs, never raw OHLCV or private account data. Generated standalone HTML with historical teaching examples is a separate local artifact, not a tracked dataset.

Views: evidence status and results; scenario controls with equity/drawdown/cost sensitivity; selected event step-by-step reveal; indicator explanation and simple synthetic parameter laboratory; source matrix linking primary literature; limitations, falsification and paper-trading gates. Every chart labels diagnostic/price-return status and interval. A required 10% hurdle is an explicit user-set hurdle, never a forecast.

Metrics: actual NAV CAGR from elapsed calendar years; total return; daily annualized volatility and Sharpe (RF=0 assumption); max drawdown; trade count, win rate, profit factor, exposure, turnover; calendar-year realized returns (partial years labeled); paired date-block bootstrap confidence for baseline differential; stress by cost, hold, time period and symbol concentration. No fabricated DSR/PBO: absent effective trial history is stated.

## Acceptance and falsifiers

Hand-calculated cash/fee/next-open tests; invalid price and missing data tests; future-tail invariance; deterministic output/hashes; immutable conflict rejection; no raw data tracked; numerical reconciliation of results and UI; real Chromium desktop/mobile interactions and export. Existing make verify remains canonical and must pass; fresh remote CI determines REMOTE_REPRODUCIBLE independently.

Reject live readiness if provider differences, adjustment, universe survivorship, incomplete intraday confirmation, execution model or independent validation remain unresolved. No institutional-grade label based only on test count. Deliver negative evidence prominently.
