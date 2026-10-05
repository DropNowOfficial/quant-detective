# Pre-push market-watch audit — 2026-10-05

Status: **DRAFT / DO NOT MERGE UNTIL CI AND REVIEW PASS**

The product goal is market-wide discovery followed by HARNESS qualification.
It is not an NVDA exception engine and it is not a fixed-watchlist notifier.

## Architecture that should exist

1. **Market discovery** — broad provider-native market scans/events identify what deserves attention.
2. **Candidate enrichment** — price/volume, market/sector context, premarket/gap/news, data quality.
3. **HARNESS qualification** — completed-RTH trend, MA5/ATR geometry, gap regime, completed-5m VWAP structure, same-time RVOL, extension rules.
4. **Historical confidence** — separate 3-year no-lookahead state analytics; never substitute a daily proxy for exact intraday replay.
5. **Delivery** — a state transition is not useful until a user-facing notification path exists.

## What the previous runtime actually was

- Fixed 41-name core list.
- Yahoo public 5m/daily observations.
- No provider-native whole-market discovery.
- No true market breadth.
- No sector/industry-relative ranking.
- News was post-trigger context, not discovery.
- Historical confidence and Value–Price–Return Pendulum were not wired into live state.
- VPS had no user-facing push delivery.
- IBKR fast path existed in code but was not authenticated on the VPS.

Therefore the old runtime must not be described as a whole-market real-time scanner.

## P0 risks found in this audit

- **Coverage illusion:** fixed watchlist was implicitly treated as market coverage.
- **Stale event contamination:** a strong premarket/gap event could keep a stock in LEADER state after an RTH reversal.
- **HARNESS drift:** gap regimes and VWAP extension blockers were missing from live confirmation.
- **Discovery visibility gap:** newly discovered IBKR candidates could wait up to one structural refresh before appearing in HARNESS rows.
- **Dynamic contract leak:** old discovery design could keep accumulating stale conids in snapshot requests.
- **Deploy/market latency confusion:** one-second Git polling did not make market data fresher.

## Changes on this audit branch

- Fixed core list is explicitly CORE_FALLBACK, not market-wide.
- Added read-only IBKR U.S.-major provider scanner discovery. Scan codes are resolved from current /iserver/scanner/params.
- Provider scanner output is explicitly top-N/non-exhaustive.
- SPY/QQQ/IWM/SOXX + NQ/ES/RTY context replaces QQQ-only framing.
- Premarket/open-gap history is event context; it cannot by itself preserve an RTH leader after reversal.
- Gap regime and VWAP-extension blockers are back in ENTRY_CONFIRMED.
- Immediate discovery_candidates are visible before structural enrichment.
- Dynamic candidate contracts are pruned.
- Superseded self-hosted deploy workflow and installer were removed.
- Git deployment polling is a low-frequency fallback again.

## Still missing — do not imply otherwise

### Market discovery / data
- True market breadth: advance/decline, new highs/lows, percent above moving averages.
- Sector/industry membership and percentile relative strength.
- News-first market discovery.
- Halt/resume, block-trade, unusual options and sub-5-minute spike event feeds.
- A permanently authenticated professional realtime provider on the VPS.
- Certified security-class filtering for scanner results (ETF/warrant/special classes).
- Corporate-action/split normalization audit.
- Exchange-calendar early-close handling.

### HARNESS
- Explicit liquidity gate policy.
- Reclaim/retest/higher-low alternative to two-bar confirmation.
- Momentum continuation/re-entry state machine.
- Full caution policy for ORCL/CRCL/MSTR.
- Exact current-time RVOL threshold calibration; current 0.8 floor is operational only.
- Rolling 3-year confidence labels and exact intraday replay.
- Value–Price–Return Pendulum live integration.

### Operations
- External push notification transport from the VPS.
- Hung-process / stale-state watchdog separate from systemd Restart=always.
- Data-provider freshness SLA and source-age alarm.

## Product benchmark bar

Mature scanners separate broad universe selection, dynamic scanning, extended-hours context,
relative performance, event/news signals, and notification delivery. The target for this
project is not feature-count parity. The minimum bar is honest coverage + fast discovery +
HARNESS qualification + reliable delivery.
