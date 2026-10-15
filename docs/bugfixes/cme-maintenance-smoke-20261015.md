# CME maintenance and delayed-reopen smoke acceptance

The initial PR55 smoke ran at 2026-10-15 22:09:09 UTC / 18:09:09 New York.
NQ/ES observations were timestamped 20:59:59 UTC / 16:59:59 New York.
The 11-minute observation-age gate correctly withheld those values; the smoke
contract incorrectly treated every withheld futures proxy as an unexpected failure.
This patch adds a separate, explicitly limited acceptance, not a freshness override.

## Source contract and bounded scope

- CME ES/NQ ordinary hours: Sunday 18:00 through Friday 17:00 ET;
  daily maintenance 17:00–18:00 ET.
  [NQ official product](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.timeAndSales.html?videoId=6396076863112)
  [ES official product](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.timeAndSales.html?videoId=6392288721112)
- [Yahoo official delay table](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)
  lists CME, ICE Data Services, 10 minutes. This is a provider convention, not
  evidence of the exact latency of any individual HTTP response.
- Only ordinary Monday–Thursday maintenance and the first 10 minutes after
  reopening qualify. The exception ends at 18:10 ET, exclusively. It does NOT
  promise that a first completed five-minute bar must have arrived then.
- Actual observation must fall within the prior 5-minute interval plus existing
  60-second grace of min(now minus 10 minutes, 17:00), and strictly before 17:00.
  Same-day date, source identity, exact CME futures symbol, USD and fresh receipt
  are required. Old-day bars, missing/future/expired receipts and unknown identity
  cannot qualify. The grace is our declared tolerance, not a vendor guarantee.
- exchange_calendars 4.13.2 CMES is a generic calendar, NOT an authoritative
  ES/NQ product schedule. It is used only to exclude special/closed days.
  Both today and tomorrow must also be regular XNYS days. Friday, weekend,
  holidays, early closes and unsupported schedules receive no exception.
  This conservative scope may reject legitimate observations on special dates;
  it does not claim complete CME holiday coverage. New York ZoneInfo handles DST.

## Semantics

EXPECTED_FUTURES_LIMITATION means stock checks passed, while futures context is
unavailable during an evidenced maintenance / delayed-reopening window.
Futures price and change remain null; observed values are diagnostic only.
No ENTRY, selection threshold, alert rule or global age limit changes.

Futures timestamps are treated as observations. In particular, 16:59:59 is not
assumed to begin a five-minute bar ending at 17:04:59. Futures bar end is null
and completed_at_receipt is false. The existing 11-minute timestamp-age budget
is unchanged. Legacy source_bar_start_utc is retained for schema compatibility;
for futures it denotes the source observation timestamp, not a certified bar open.
The availability diagnostic never certifies real-time eligibility, even when the
ordinary quote-age gate passes outside the exception.

No original HTTP response from the failing CI scan was retained. Its derived
artifact supports the observed timestamps, but lacks new exchange/currency
metadata, so it cannot be retroactively promoted to a verified limited pass.
New fixtures are explicitly synthetic and provide that missing identity evidence.
The real new-head CI run is reported separately, with its actual timing and result.
