# Continuous Market Watch

This layer fixes the old LIVE screener's two structural blind spots:

1. the scan stopped when the browser session expired;
2. U.S. equities deliberately discarded all premarket/postmarket bars.

The headless watcher is independent of the browser UI.

## States

Market leadership and entry eligibility are separate.

- `LEADER_HOT_NO_CHASE`: a material move is happening, but price is too far above completed-day MA5.
- `LEADER_WATCH`: a material move is happening, but the entry gate is not complete.
- `ENTRY_ARMED`: completed-day trend and MA5/ATR geometry are valid; intraday confirmation is incomplete.
- `ENTRY_CONFIRMED`: completed-day trend, MA5/ATR geometry, two completed 5-minute closes above RTH VWAP + MA5, and same-time historical RVOL >= 0.8.
- `EXTENDED` / `WATCH`: no alert-grade transition.

A strong stock must not disappear merely because it is too extended to buy.

## Data domains

### Completed daily state

MA5, MA10, MA20, MA5 slope, and ATR5 use completed prior daily bars. The current session is excluded.

### Premarket / postmarket

The headless U.S. watcher requests Yahoo chart data with `includePrePost=true`. It records:

- premarket last price and move vs prior close;
- RTH opening gap;
- current move vs RTH open;
- RTH-only VWAP approximation;
- postmarket bars when available.

The browser LIVE provider remains unchanged and RTH-only. The headless watcher is a separate data path.

### Overnight regime

Single-stock public Yahoo data do not provide the full 20:00-04:00 ET overnight session. Do not label that gap as stock-level overnight coverage.

The watcher therefore adds:

- Nasdaq-100 futures (`NQ=F`) as an overnight growth/tech regime proxy;
- S&P 500 futures (`ES=F`) as a broad overnight regime proxy;
- QQQ as a same-session relative-strength benchmark when available.

True single-stock overnight coverage requires a provider that exposes those prints, for example a self-hosted broker/data gateway. This repository does not contain broker credentials.

### Same-time RVOL

For each stock, a cached one-month 5-minute RTH history builds prior-session cumulative volume at the same clock time. Current cumulative RTH volume is divided by the median of up to the last 20 available sessions.

No same-time volume history means no `ENTRY_CONFIRMED`.

### News context

Material states query Yahoo's public search/news endpoint. News is context, not a trading rule, and is cached for 15 minutes to reduce request load.

## GitHub execution

After this workflow exists on the repository default branch, `continuous-market-watch` starts three overlapping long-running GitHub-hosted shifts:

- 04:07 ET: 355 minutes
- 09:57 ET: 355 minutes
- 15:47 ET: 258 minutes

Inside a shift, the watcher polls every 60 seconds. The overlap reduces handoff gaps if a scheduled job starts slightly late. Daily and volume-profile inputs are cached for that ET date, and news is cached for 15 minutes.

GitHub schedule is only the handoff trigger. GitHub's documented minimum scheduled interval is five minutes, but the already-running process can poll more frequently inside the job. GitHub-hosted jobs still have a six-hour execution limit and scheduled runs can be delayed, so this is near-continuous hosted scanning rather than an exchange-grade streaming service. A self-hosted runner/service is the path for a true always-on process.

## Alerts

When a material state appears, the workflow creates or reuses:

`Market Watch | YYYY-MM-DD ET`

The daily issue is assigned to the repository owner. Comments are deduplicated by date + symbol + state.

This alert stream is observation-only. It does not create, draft, route, or submit orders.

## Local / self-hosted command

One scan:

```bash
uv run python -m market_data.cli watch --once --github-alerts
```

Continuous local process:

```bash
uv run python -m market_data.cli watch --poll-seconds 30 --duration-minutes 350 --github-alerts
```

The CLI lower bound is 15 seconds, but public-source rate limits and data update cadence still apply. Faster polling does not manufacture fresher upstream data.
