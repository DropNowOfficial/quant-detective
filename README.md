# Quant Detective LIVE

**Public market data → closed-bar features → ranked candidates → rule explanations.** The default product is a continuously refreshed screening terminal. Research and backtests are secondary; no account connection or order routing.

## Start the LIVE screener

```bash
git clone https://github.com/DropNowOfficial/quant-detective.git
cd quant-detective
python -m market_data serve
```

Open `http://127.0.0.1:8767/`. Python 3.11+ and `curl` are required; the LIVE runtime uses only the standard library. On Windows, run `start-live.cmd` from the repository. The reproducible developer environment remains `uv sync --frozen` and `uv run qd-market serve`.

- **LIVE / SCREENER / STOCK:** ongoing provider GET requests, editable thresholds, ranked candidates, whole-row selection, source-specific charts and check-by-check explanations.
- **Coverage honesty:** the public/Yahoo headless fallback watches a fixed core list and is **not** whole-market coverage. Market-wide discovery requires a provider scanner (currently the read-only IBKR U.S.-major scanner path), then HARNESS enrichment runs only on the ranked candidate subset.
- **Binance spot mirror, Gate USDT perpetual, OKX perpetual** public adapters; US/A-share and other original public directory adapters remain available. Live crypto comparisons use USDT-quoted instruments, not synthetic cross-currency returns. Derivative contracts never stand in for cash stocks.
- Target **12-second batch cadence**, 12 symbols per batch, four concurrent GETs. Each GET stops at 12 seconds. Slow requests/other active tabs can lengthen actual cadence; the UI reports cycle duration, coverage and each observation's age. Whole-directory rotation is NOT a claim that every instrument updates every 12 seconds.
- Current-price display includes explicitly marked unfinished candles. **MINUTE_MA5_V1** ranking uses completed bars only: MA5/MA20, ATR14, rolling VWAP60, RVOL20 and time-aligned same-source relative strength. These are minute-bar features, **not the original daily HARNESS** or a verified profit forecast.
- Stale, missing, malformed or unavailable observations cannot remain READY. HTTP 429/451, non-JSON blocks and timeout errors stay visible; there is no source substitution.
- **REPLAY:** last 30 observed scan batches in memory, marked historical. **RESEARCH:** existing frozen scenario/CAGR/drawdown research. Neither is a shadow account on the homepage.

See the **[Chinese LIVE guide](docs/live-screener.zh-CN.md)**. The original eight-market one-shot K-line terminal is at `/market`; one-shot CLI requests still emit no signals:

```bash
python -m market_data candles --market binance_spot --symbol ETHUSDT --interval 5m --limit 5
```

## Interactive research lab · 0.2.0

The lab explains the MA5/ATR/gap rule, reconstructs cash-bounded daily portfolios, contrasts data sources and long histories, and preserves negative evidence. It does not certify alpha or turn a backtest into permission to trade.

- 90 external snapshots, 151,721 source rows across IBKR and Yahoo; overlapping observations are not independent samples.
- Three datasets/windows and 192 disclosed rule/hold/cost scenarios; long-history window starts in 2017.
- Finite cash, next-open entry, explicit fees, same-capital trend control, QQQ/SOXX and current-universe EW benchmarks.
- Chronological ledgers, daily equity/drawdown, calendar-year results, paired block intervals, source discrepancies, symbol exclusion and capital sensitivity.
- Offline Chinese teaching UI with scenario comparison, historical reveal, a clearly synthetic indicator exercise, source evidence and readiness gates.
- All trusted investment metrics remain unvalidated. Historical price returns exclude dividends/cash interest and retain current-universe and corporate-action limitations.

Read **[the Chinese research and startup guide](docs/research-guide.zh-CN.md)**, **[method evidence](docs/method-evidence.zh-CN.md)** and **[pre-delivery falsification](docs/pre-delivery-review.zh-CN.md)**. Aggregate findings are in [research_outputs/summary.json](research_outputs/summary.json); source identities and hashes are in [snapshot-manifest.json](research_outputs/snapshot-manifest.json). No raw market datasets are committed.

```bash
uv sync --frozen --extra research
make verify
uv run --extra research qd-lab build \
  --ibkr /absolute/authorized-snapshots/ibkr \
  --yahoo /absolute/authorized-snapshots/yahoo \
  --expected-manifest research_outputs/snapshot-manifest.json \
  --review research_outputs/data-review.json
uv run qd-lab serve research_lab/runs/PRINTED_RUN_ID
```

Open the localhost URL printed by the server. Exact market replay requires the authorized pinned snapshots; a future provider download can revise history. `make verify` includes deterministic synthetic engine tests and the preserved v0.1.1 regression suite, not private market-data reproduction. See [verification receipt](research_outputs/verification.json) for what actually ran, including browser scope.

The prior selectable-stock workbench and frozen event-study code are preserved in [legacy/trade-monitor](legacy/trade-monitor/README.md). Its daily candidates are separate from the portfolio experiment.

### What the frozen experiment actually found

Price-return diagnostics, fixed primary rule / 3 sessions / 10bps round-trip cost, ending 2026-10-02. These are **not validated performance or expected returns**; dividends, cash interest, historical-universe reconstruction and some corporate-action economics remain unresolved.

| Source/window | Strategy CAGR | Maximum drawdown | QQQ price CAGR | Data state |
|---|---:|---:|---:|---|
| IBKR, 2022–2026 | 18.53% | −28.25% | 14.19% | BLOCKED_DATA |
| Yahoo, 2022–2026 | 18.22% | −30.93% | 14.19% | DIAGNOSTIC_ONLY |
| Yahoo, 2017–2026 | 11.27% | −30.93% | 20.75% | DIAGNOSTIC_ONLY |

Longer history weakens the apparent advantage; exposure differs from QQQ. At 50bps round-trip cost the Yahoo strategy CAGR becomes −2.97% (2022–2026) and −8.97% (2017–2026). Paired intervals versus the same-capital trend control include zero in both Yahoo windows. No stable alpha has been established, and the original daily proxy does not validate the full intraday HARNESS.

## Existing research OS

Evidence Contracts and schemas remain in `contracts/` and `schemas/`; deterministic financial validation is in `validation_kernel/`; the sealed synthetic PREDICT → LOCK → REVEAL → SCORE fixture is in `time_machine/`. The optional MA5 lab does not inject technical trading nodes into that default graph.

```bash
make verify
make tm-fixture
make clean-room-check
```

Governance: [AGENTS.md](AGENTS.md), [architecture](docs/architecture.md), [dependency reuse map](docs/adr/0001-dependency-reuse-map.md). See [GitHub verification runs](https://github.com/DropNowOfficial/quant-detective/actions/workflows/verify.yml).
