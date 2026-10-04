# Quant Detective

Falsifiable equity research: **LEDGER → VECTOR → SKEPTIC → ATLAS**. Evidence Contracts, Validation Kernel, Time Machine, and a reproducible MA5 strategy teaching lab. Research only; no order routing.

## Multi-market public K-line terminal

Eight independent public adapters: A-shares, US listed securities, OKX perpetual/delivery contracts, Binance USD-M/COIN-M, Binance spot (source A), and Gate USDT perpetuals (source B). Load each actual directory, search symbols/names and manually request native `1m`, `5m` or `15m` OHLCV. No keys, accounts, orders, automatic refresh or new signals.

```bash
uv sync --frozen
uv run qd-market serve
```

Open `http://127.0.0.1:8767/`. Every upstream GET has a 12-second total deadline; failures, incomplete coverage and unfinished candles remain explicit. A directory entry is not proof of present tradability or available history. See the **[Chinese startup and coverage guide](docs/public-markets.zh-CN.md)** and [observed source receipts](research_outputs/public-markets.json).

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
