# Hosted quote validity

The hosted public-data fallback keeps its existing MA5, ATR5, leader triggers, geometry, and same-time RVOL formulas. Quote validity is an input gate, independent of those formulas. This code never places orders.

## Source evidence

Every usable live quote retains its source 5-minute bar start and end, original HTTP receipt time, source/evaluation session, evaluation time, and expiry boundaries. Report generation, news retrieval, revalidation, and publication never refresh the receipt. The latest price remains the latest bar's close, including an unfinished bar; it is not silently replaced with the latest completed close.

- Original live receipt age must be at most 120 seconds at evaluation and each material publication.
- Receipt/source timestamps more than 5 seconds ahead of their applicable clock are rejected. Missing, malformed, naive, or inconsistent timestamps fail closed. A rejected snapshot stays rejected until a new acquisition replaces it.
- A bar expires at its end plus 5 minutes plus 60 seconds, exclusive. Thus the 10:00–10:05 bar is usable at 10:07 and 10:10:59, but not at 10:11 without newer evidence.
- The daily reference must be the previous actual exchange session. Today's daily bar remains excluded, including after close. Daily/history cache receipts are provenance, not live-price receipts. Invalid daily references are not cached, so a later source recovery can be used.

## Confirmation and sessions

A snapshot's completed bars are determined at its original HTTP receipt. An unfinished snapshot never becomes completed just because a report or publisher uses it later.

ENTRY_CONFIRMED additionally requires the current XNYS regular session and the freshest two consecutive completed RTH bars, with the newest confirmation bar still fresh. Existing VWAP/MA5 and RVOL thresholds are unchanged. A new incomplete price cannot revive an old confirmation. Zero-volume bars cannot be skipped to select an older confirming pair.

The calendar includes exchange holidays, early closes, and DST. The already-locked exchange-calendars 4.13.2 package is promoted to a runtime dependency; no factor framework is imported. Fresh PRE/POST source observations remain eligible for the existing observation/leader states, including the scheduled 20:02 scan using a still-fresh 19:55–20:00 POST bar. This does not add overnight source data. No new ENTRY_CONFIRMED event is published after the close, including when an earlier scan crosses the close while doing network work.

## Diagnostics, context, and publication

Invalid stock inputs remain in rows as DATA_INVALID/INVALID_DATA with rejection reasons and evidence; they produce no material event. Stale QQQ removes only relative-strength support. Independent stock leader reasons remain available. Stale NQ/ES/QQQ values are retained as observed historical diagnostics, while their current price/change fields are unavailable.

The publisher rechecks evidence immediately before each material POST. Unsupported/expired events receive publication_rejections entries and do not acquire deduplication markers. The GitHub heartbeat remains separate from material events. Existing historical comments are not rewritten.

The complete scan report is written before publication and again in a finally block after publication, preserving partial-failure diagnostics. The workflow always attempts to upload market-watch.json with a unique run/attempt artifact name and 30-day retention. A failure before any report can be produced is reported as a missing artifact, not fabricated scan evidence.

## Verification scope

Synthetic temporal regressions reproduce and prevent both a fresh POST price reusing old RTH confirmation and a ten-hour-old quote becoming ENTRY_CONFIRMED. Tests cover receipt/bar boundaries, incomplete snapshots, delayed news/publication, stale proxy fallback, missing/future times, prior-session dates, holidays, early closes, DST, partial output, and deduplication. Synthetic prices are test inputs, not actual market signals. Local verification is not a deployment or a live-data accuracy certification.
