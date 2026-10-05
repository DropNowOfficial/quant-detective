# Quant Detective LIVE Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** Deliver the requested public-data scanning terminal as the default homepage.
**Architecture:** Pure deterministic closed-bar feature engine + bounded background scanner + existing local HTTP server and a dependency-free interactive frontend. Existing research remains secondary.
**Tech Stack:** Python standard library, pytest, existing provider adapters, HTML/CSS/JS, Playwright QA.
**Spec:** docs/superpowers/specs/2026-10-04-live-screener-design.md

## Global Constraints
12-second per-GET deadline; 1m/5m/15m only; public/no credentials/no execution; same-source benchmarks; closed-bar calculations; no raw market data in Git; research artifacts untouched; MINUTE_MA5_V1 distinct from daily HARNESS.

## Review Focus
- Newly closed bar or stale mutable response must not acquire false validity.
- Gate volume contract units and OKX quote volume may be missing.
- Large universes must show partial coverage, not imply whole-market 12s freshness.
- Tabs, market/interval changes and delayed responses must not contaminate each other.
- Service errors, stale/paused views and replay must never show a current READY signal.

### Task 1: Deterministic features and source volume fields
Files: market_data/features.py, market_data/providers.py, tests/test_live_features.py.
Produces analyze(observation, benchmark, instrument, now_ms, band=0.5, min_rvol=1.2, min_turnover=0) -> dict; rank_rows(rows) -> list.
- [x] Write analytical tests covering finite inputs, closed-bar future invariance, timeline alignment, exact quote/base VWAP, Gate units, missingness and freshness; run red.
- [x] Implement minute-only versioned rule and row explanations; retain source receipts; add normalized volume fields; run green.

### Task 2: Continuous scanner and HTTP integration
Files: market_data/live.py, market_data/server.py, market_data/cli.py, tests/test_live_service.py.
Consumes analyze and provider get_catalog/get_candles. Produces LiveService.snapshot(config), history(id), close().
- [x] Write fake-provider/clock tests: watchlist defaults, actual directory matching, sequential batch rotation, per-subscription isolation, expiry, backoff and no overlapping scan cycles; run red.
- [x] Implement bounded scanning with 12s cadence, metadata/history and explicit partial coverage; GET live/history and research routes; run green.

### Task 3: Operational homepage and navigation
Files: market_data/live.html, tests/browser_live.cjs, README.md, docs/live-screener.zh-CN.md.
Consumes /api/live and /api/live/history plus existing catalog/candles.
- [x] Build responsive terminal with ranked rows, source controls, catalog search/watchlist, thresholds, signal explanation/chart, age/status, pause, replay, CSV/JSON, secondary research and old market page.
- [x] Browser tests: row selection, search and filters, interval/market races, stale states, null/tiny values, historical labels, 320/768/1440px. Real-source probe with exact failures preserved.

### Task 4: Validation and delivery
- [x] make verify, browser tests, independent whole-change review; fix material failures with regression coverage.
- [x] Update verification receipt and startup docs; commit and push to DropNowOfficial/quant-detective (prior explicit authorization); no merge to main.
