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

## Runtime guard and diagnostic health

The public watcher supports XNYS trading dates, New York time 04:00 inclusive to
20:00 exclusive. The pinned exchange calendar covers weekends, holidays, DST,
and early RTH closes; supported POST observations still end at 20:00. This does
not add an overnight equity feed.

The scanner checks actual time before every provider request and publication
write. A delayed schedule or manual run outside the window returns `SKIPPED`
without fetching or publishing. A scan crossing the boundary retains its
already-acquired evidence as `SESSION_ENDED`, clears remaining alerts, and stops
new requests. Genuine acquisition failures remain incomplete/unavailable and
exit nonzero. Existing quote provenance, freshness, and confirmation gates stay
in force. Confirmed publication responses and request times survive a cutoff
or later error; smoke checks distinguish safe partial publication from no
publication. A skip or boundary stop never proves fresh data or VPS health.

Active coverage is `COMPLETE`, `INCOMPLETE` (some requested symbols usable), or
`UNAVAILABLE` (none usable). `INCOMPLETE` completes successfully with explicit
usable/requested counts, rejected symbols and fixed rejection codes. Sparse POST
bars retain the same freshness gate; no stale quote becomes usable to improve
workflow status. `UNAVAILABLE`, malformed evidence (`FAILED`) and execution or
publication exceptions still exit nonzero. A failed publication records a fixed
`execution_error` without saving remote exception text.

Separate nontrade diagnostics use the existing Slack ledger and webhook.
Unchanged state and coverage/reason fingerprints remain quiet across restarts
and calendar dates. Severity escalation and full recovery are immediate;
changed missing symbols or reasons and severity downgrades are limited to one
notice per 30 minutes. Expected session stops never announce failure/recovery.
The first encounter with a legacy notice lacking a coverage fingerprint can
produce one migration notice after cooldown.

The Slack command exports `delivery_healthy` and fails if its verified queue has
pending, retry-wait, unknown or needs-attention records, or forwarding pauses.
Unknown delivery remains blocked from automatic resend. The workflow separately
checks ledger readiness, health persistence, forwarding outcome and actual
delivery evidence; its `notification-delivery.json` artifact distinguishes
transport health from market coverage. Healthy scan data alone cannot establish
successful Slack delivery. A duplicate-source record does not imply a failed
delivery of its canonical event.
