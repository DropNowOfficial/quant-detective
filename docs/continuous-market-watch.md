# Continuous Market Watch

[System map / 中文入口](system-map.zh-CN.md)

This page describes the public-data headless `watch` command and its GitHub-hosted fallback. It is independent of browser LIVE and of the VPS `ibkr-watch` daemon. The browser still has session expiry and RTH-only U.S. bars; this separate path does not change those properties.

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

The current [workflow](../.github/workflows/market-watch.yml) is named `hosted-core-watch-fallback`. Each job performs **one scan**, using `watch --once --github-alerts`, with an eight-minute job timeout. It no longer starts three overlapping long-running shifts.

Its configured schedule is Monday–Friday in `America/New_York`: every five minutes from 04:02 through 19:57, plus 20:02. Manual dispatch is available; pushes to `main` trigger it only when the workflow or `market_data/github_alerts.py` changes. A push-triggered heartbeat does not prove that scheduled runs are firing.

This is a best-effort scheduled fallback, not a continuous process or an exchange-grade feed. A cron declaration alone is not execution evidence. Check the Actions run's trigger, timestamp, commit, conclusion and scanner heartbeat. GitHub-hosted fallback health does not prove VPS health; see the [self-hosted guide](self-hosted-ibkr-watch.md).

## Alerts

On a successfully published scan, the workflow creates or reuses a daily issue (even with no new material event):

`Market Watch | YYYY-MM-DD ET`

The daily issue is assigned to the repository owner. A mutable **Scanner heartbeat** comment records the latest scan, its trigger/run/commit, OK/error counts and material-alert count. Material-event comments are deduplicated by date + symbol + state. Issue numbers vary by date; they are not a fixed channel ID.

Alert count in a scan, newly published deduplicated comments, and notifications actually received by a person are different quantities. An issue comment proves publication, not email/push delivery. No new material comment does not mean no scan occurred or no market opportunity existed. The publisher requires the GitHub repository and runtime token environment; never commit credentials.

The VPS `ibkr-watch` service currently writes local state/events and does **not** call this publisher. Its files are not automatically the source of these GitHub comments. See [publisher](../market_data/github_alerts.py), [public watcher](../market_data/us_watch.py) and [VPS daemon](../market_data/ibkr_live.py).

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
