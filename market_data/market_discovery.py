"""Read-only provider-native market discovery for U.S. stocks.

This layer only answers: which symbols deserve HARNESS analysis now?
It does not create entry signals and it has no order path.
"""
from __future__ import annotations

import time

DISCOVERY_SCAN_NAMES = (
    "Top % Gainers",
    "Top % Losers",
    "Hot Contracts by Volume",
    "Top Trade Count",
    "Top Volume Rate",
)
DISCOVERY_INSTRUMENT = "STK"
DISCOVERY_LOCATION = "STK.US.MAJOR"
ALLOWED_STOCK_TYPES = frozenset({"Common", "ADR", "REIT", "CORP"})


class DiscoveryError(RuntimeError):
    pass


class IBKRMarketDiscovery:
    def __init__(
        self,
        gateway,
        *,
        scan_names=DISCOVERY_SCAN_NAMES,
        params_ttl_seconds=960,
        candidate_ttl_seconds=90,
        min_scan_interval_seconds=1.05,
        clock=time.time,
    ):
        self.gateway = gateway
        self.scan_names = tuple(scan_names)
        self.params_ttl_seconds = float(params_ttl_seconds)
        self.candidate_ttl_seconds = float(candidate_ttl_seconds)
        self.min_scan_interval_seconds = max(1.0, float(min_scan_interval_seconds))
        self.clock = clock
        self._scan_specs = ()
        self._params_loaded_at = 0.0
        self._last_scan_at = 0.0
        self._cursor = 0
        self._pool = {}

    def _load_specs(self):
        now = self.clock()
        if self._scan_specs and now - self._params_loaded_at < self.params_ttl_seconds:
            return self._scan_specs
        params = self.gateway.scanner_params()
        rows = params.get("scan_type_list") or []
        if not isinstance(rows, list):
            raise DiscoveryError("IBKR scanner params missing scan_type_list")
        by_name = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            display = row.get("display_name")
            code = row.get("code")
            instruments = row.get("instruments") or []
            if display and code and DISCOVERY_INSTRUMENT in instruments:
                by_name[str(display)] = str(code)
        specs = tuple((name, by_name[name]) for name in self.scan_names if name in by_name)
        if not specs:
            raise DiscoveryError("no requested IBKR stock scanners are currently available")
        self._scan_specs = specs
        self._params_loaded_at = now
        self._cursor %= len(specs)
        return specs

    def _prune(self, now):
        cutoff = now - self.candidate_ttl_seconds
        self._pool = {
            symbol: item for symbol, item in self._pool.items()
            if item.get("last_seen", 0.0) >= cutoff
        }

    def tick(self):
        specs = self._load_specs()
        now = self.clock()
        if self._last_scan_at and now - self._last_scan_at < self.min_scan_interval_seconds:
            self._prune(now)
            return {
                "active": True,
                "paced": True,
                "candidate_pool_size": len(self._pool),
                "available_scan_count": len(specs),
            }

        display_name, code = specs[self._cursor]
        self._cursor = (self._cursor + 1) % len(specs)
        payload = self.gateway.scanner_run(
            instrument=DISCOVERY_INSTRUMENT,
            location=DISCOVERY_LOCATION,
            scan_type=code,
        )
        self._last_scan_at = now
        contracts = payload.get("contracts") or []
        for rank, row in enumerate(contracts):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            conid = row.get("con_id")
            if not symbol or not isinstance(conid, int):
                continue
            item = self._pool.setdefault(symbol, {
                "symbol": symbol,
                "conid": conid,
                "exchange": row.get("listing_exchange"),
                "company_name": row.get("company_name"),
                "first_seen": now,
                "scan_hits": {},
            })
            item["conid"] = conid
            item["exchange"] = row.get("listing_exchange") or item.get("exchange")
            item["company_name"] = row.get("company_name") or item.get("company_name")
            item["last_seen"] = now
            item["scan_hits"][display_name] = {
                "rank": rank,
                "scan_data": row.get("scan_data"),
                "seen_at": now,
            }
        self._prune(now)
        return {
            "active": True,
            "paced": False,
            "scan_display_name": display_name,
            "scan_code": code,
            "returned": len(contracts),
            "candidate_pool_size": len(self._pool),
            "available_scan_count": len(specs),
            "missing_requested_scans": [name for name in self.scan_names if name not in {x[0] for x in specs}],
        }

    def candidates(self, limit=24):
        now = self.clock()
        self._prune(now)
        rows = []
        for item in self._pool.values():
            copy = dict(item)
            hits = copy.get("scan_hits", {})
            copy["scan_hit_count"] = len(hits)
            copy["best_rank"] = min((hit.get("rank", 9999) for hit in hits.values()), default=9999)
            copy["multi_scan"] = copy["scan_hit_count"] >= 2
            rows.append(copy)
        rows.sort(key=lambda item: (
            -item["scan_hit_count"],
            item["best_rank"],
            -item.get("last_seen", 0.0),
            item["symbol"],
        ))
        return rows[:max(0, int(limit))]
