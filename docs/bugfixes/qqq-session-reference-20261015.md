# QQQ market-change reference: local-only correction

## Defect and evidence boundary

The October15 audit retained watcher outputs with QQQ reference747.58, while a separately collected dated daily series identifies October14 close728.9500122070312. At the saved MSFT/META event quote prices this changes the approximate displayed market changes from−3.62%/−3.09% to−1.16%/−0.62%.

The original HTTP responses were not retained. This is a reconstruction using saved event quotes plus later-collected dated daily evidence, not an exact replay of the original HTTP inputs. The audit's two synthetic regressions reproduced the buggy metadata priority on baseline tree matching main1c88e08. Original failing tests/logs are retained in the delivery evidence. Their undated `previousClose` alone cannot prove a session-close contract; updated tests supply explicitly dated synthetic daily evidence and separately require unknown when only undated metadata exists.

## Minimal implementation

QQQ only: request one month of regular daily OHLCV and select exactly one row dated to the prior XNYS session of the quote's own New York session date. Require instrument identity/USD/NewYork, a daily timestamp anchored at actual session open, positive close, and source receipt after that session's actual close and no later than evaluation. Preserve source URL, reference date, close timestamp, actual receipt and basis. No metadata close fallback is permitted for QQQ.

Premarket, RTH and postmarket quotes on one session use the same previous-session close. Weekend/holiday lookback uses exchange calendar; early closes and DST use calendar timestamps. Stale, future, cross-date or out-of-session quotes cannot produce a live change. Recheck quote freshness after the additional request. Missing/duplicate/wrong-date/malformed reference evidence leaves the price observation intact and change/reference unknown, with an explicit reason. Reference completion is separate from intraday quote freshness; an already acquired prior close does not need a new120-second receipt to remain dated evidence.

NQ/ES behavior, classification functions, ENTRY conditions, notification flow and thresholds are unchanged. Correcting a QQQ number may change an existing relative-strength description, but no new market veto or selection condition has been introduced. The extra public daily request is only per QQQ context acquisition; no durable job or deployment was created.

## Tests

`tests/test_market_reference_contract.py` covers the two audit regression intents and RTH/PRE/POST, weekends/holidays/year boundaries, UTC/NewYork date boundary, DST, early-close completion, missing/duplicate/nonpositive references, stale or future quote receipts, request failure and quotes expiring during the reference request. An existing synthetic scanner helper now stamps daily bars at actual session open instead of an arbitrary14:00UTC; its price and strategy inputs are unchanged.

Run `make verify` from a fresh checkout with the pinned dependencies. The delivery packet contains local verification output and offline reproduction commands. Local results do not mean this patch was published, deployed or run against fresh production HTTP responses.
