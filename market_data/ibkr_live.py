"""Persistent hybrid watcher: IBKR fast snapshots + slower structural scans.

The daemon has no order path. IBKR is used for fast live observations; completed
RTH daily/5-minute structure and public news remain in the structural watcher.
If IBKR is unavailable, the daemon explicitly degrades to public-only mode.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from .ibkr_cpg import ClientPortalGateway
from .market_discovery import IBKRMarketDiscovery
from .us_watch import scan_once
from .universe import CORE_FALLBACK_SYMBOLS, COVERAGE_CORE_FALLBACK, COVERAGE_IBKR_DYNAMIC


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        tmp = Path(handle.name)
    os.replace(tmp, path)


def _append_jsonl(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _row_map(report):
    return {r["symbol"]: r for r in report.get("rows", []) if r.get("status") == "OK" and r.get("symbol")}


def _realtime_quote(quote):
    availability = str((quote or {}).get("market_data_availability") or "")
    return bool(quote and _finite(quote.get("last")) and availability.startswith("R"))


def _live_state(symbol, quote, structural, broad_market_change, qqq_change):
    price = quote.get("last")
    change = quote.get("change_pct")
    daily = structural.get("daily") or {}
    intra = structural.get("intraday") or {}
    ma5, atr5 = daily.get("ma5"), daily.get("atr5")
    d5 = (price - ma5) / atr5 if _finite(price) and _finite(ma5) and _finite(atr5) and atr5 > 0 else None
    rs_spy = change - broad_market_change if _finite(change) and _finite(broad_market_change) else None
    rs_qqq = change - qqq_change if _finite(change) and _finite(qqq_change) else None
    leader_reasons = []
    if _finite(change) and change >= 1.0:
        leader_reasons.append(f"IBKR day +{change:.2f}%")
    if _finite(rs_spy) and rs_spy >= 0.5:
        leader_reasons.append(f"IBKR vs SPY +{rs_spy:.2f}pp")
    public_state = structural.get("state")
    vwap = intra.get("rth_vwap_approx")
    above_vwap = _finite(price) and _finite(vwap) and price >= vwap
    if public_state == "ENTRY_CONFIRMED" and _finite(d5) and -0.15 <= d5 <= 0.35 and above_vwap:
        state = "ENTRY_CONFIRMED"
        reason = "structural entry confirmation remains valid under current IBKR snapshot"
    elif leader_reasons and _finite(d5) and d5 > 0.35:
        state = "LEADER_HOT_NO_CHASE"
        reason = "IBKR live leader detected but price is extended above completed-day MA5"
    elif leader_reasons:
        state = "LEADER_WATCH"
        reason = "IBKR live leader detected; full structural entry gate is not satisfied"
    elif public_state in {"ENTRY_ARMED", "ENTRY_CONFIRMED"}:
        state = "ENTRY_ARMED"
        reason = "public structural setup remains armed; current IBKR live leader trigger is absent"
    elif _finite(d5) and d5 > 0.35:
        state = "EXTENDED"
        reason = "current IBKR price is > +0.35 ATR above completed-day MA5"
    else:
        state = "WATCH"
        reason = "no material live or structural transition"
    return {
        "symbol": symbol,
        "state": state,
        "reason": reason,
        "price": price,
        "change_pct": change,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "volume": quote.get("volume"),
        "d5_atr": d5,
        "relative_change_vs_spy_pp": rs_spy,
        "relative_change_vs_qqq_pp": rs_qqq,
        "leader_reasons": leader_reasons,
        "structural_state": public_state,
        "structural_known_at": structural.get("known_at"),
        "rth_vwap_approx": vwap,
        "same_time_rvol": intra.get("same_time_rvol"),
        "ibkr_updated_ms": quote.get("updated_ms"),
        "market_data_availability": quote.get("market_data_availability"),
    }


class HybridDaemon:
    def __init__(
        self,
        symbols=CORE_FALLBACK_SYMBOLS,
        gateway=None,
        snapshot_seconds=2.0,
        structure_seconds=60.0,
        state_path="runtime/market-watch/state.json",
        events_path="runtime/market-watch/events.jsonl",
        scan_fn=scan_once,
        clock=time.time,
        sleeper=time.sleep,
        discovery=None,
        discovery_structure_limit=24,
    ):
        if snapshot_seconds < 1.0:
            raise ValueError("snapshot_seconds must be >= 1")
        if structure_seconds < 30:
            raise ValueError("structure_seconds must be >= 30")
        self.symbols = tuple(dict.fromkeys(s.upper() for s in symbols))
        self.gateway = gateway or ClientPortalGateway()
        self.snapshot_seconds = float(snapshot_seconds)
        self.structure_seconds = float(structure_seconds)
        self.state_path = state_path
        self.events_path = events_path
        self.scan_fn = scan_fn
        self.clock = clock
        self.sleep = sleeper
        self.contracts = {}
        self.contract_errors = {}
        self.structural = {}
        self.structural_report = None
        self.last_structure = 0.0
        self.last_tickle = 0.0
        self.last_auth = 0.0
        self.auth = {"authenticated": False, "connected": False}
        self.discovery = discovery
        self.discovery_structure_limit = max(0, int(discovery_structure_limit))
        self.discovery_symbols = ()
        self.discovery_report = None
        self.discovery_error = None
        self.run_id = uuid.uuid4().hex
        self.previous_states = {}
        try:
            prior = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            self.previous_states = {
                row.get("symbol"): row.get("state")
                for row in prior.get("rows", [])
                if row.get("symbol") and row.get("state")
            }
        except Exception:
            pass
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.last_log_signature = None
        self.last_log_at = 0.0

    def initialize(self):
        try:
            self.auth = self.gateway.auth_status()
            if self.auth.get("authenticated"):
                self.gateway.ensure_accounts()
                wanted = tuple(dict.fromkeys((*self.symbols, "SPY", "QQQ")))
                self.contracts, self.contract_errors = self.gateway.resolve_symbols(wanted)
                if self.discovery is None:
                    self.discovery = IBKRMarketDiscovery(self.gateway)
        except Exception as exc:
            self.auth = {
                "authenticated": False,
                "connected": False,
                "initialization_error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
        self.last_auth = self.clock()
        _atomic_json(self.state_path, {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "started_at_utc": self.started_at,
            "run_id": self.run_id,
            "mode": "BOOTSTRAPPING",
            "phase": "initial_structure_scan",
            "ibkr_auth": self.auth,
            "contracts_resolved": len(self.contracts),
            "contract_errors": self.contract_errors,
            "coverage_scope": COVERAGE_IBKR_DYNAMIC if self.discovery_report else COVERAGE_CORE_FALLBACK,
            "market_wide_discovery": bool(self.discovery_report),
            "discovery_exhaustive": False,
            "discovery": self.discovery_report,
            "discovery_error": self.discovery_error,
            "discovery_symbol_count": len(self.discovery_symbols),
            "structural_symbol_count": len(self.structural),
            "capability_gaps": [
                "market_breadth_not_integrated",
                "sector_industry_relative_strength_not_integrated",
                "news_first_market_discovery_not_integrated",
                "historical_3y_confidence_not_integrated",
                "value_price_return_pendulum_not_integrated",
                "external_push_notification_not_configured",
            ],
            "snapshot_seconds": self.snapshot_seconds,
            "structure_seconds": self.structure_seconds,
            "rows": [],
            "events_this_cycle": [],
        })
        self.refresh_structure(force=True)

    def refresh_structure(self, force=False):
        now = self.clock()
        if not force and now - self.last_structure < self.structure_seconds:
            return
        targets = tuple(dict.fromkeys((*self.symbols, *self.discovery_symbols)))
        report = self.scan_fn(symbols=targets)
        self.structural_report = report
        self.structural = _row_map(report)
        self.last_structure = now

    def _refresh_auth(self):
        now = self.clock()
        if now - self.last_auth >= 30:
            self.last_auth = now
            self.auth = self.gateway.auth_status()
        if self.auth.get("authenticated") and now - self.last_tickle >= 45:
            self.gateway.tickle()
            self.last_tickle = now

    def cycle(self):
        self.refresh_structure()
        quotes = {}
        mode = "DEGRADED_PUBLIC_ONLY"
        error = None
        try:
            self._refresh_auth()
            if not self.auth.get("authenticated"):
                raise RuntimeError("IBKR gateway session is not authenticated")
            if not self.contracts:
                wanted = tuple(dict.fromkeys((*self.symbols, "SPY", "QQQ")))
                self.contracts, self.contract_errors = self.gateway.resolve_symbols(wanted)
            if self.discovery is None:
                self.discovery = IBKRMarketDiscovery(self.gateway)
            try:
                self.discovery_report = self.discovery.tick()
                candidates = self.discovery.candidates(limit=self.discovery_structure_limit)
                self.discovery_symbols = tuple(
                    item["symbol"] for item in candidates if item["symbol"] not in self.symbols
                )
                for item in candidates:
                    self.contracts.setdefault(item["symbol"], {
                        "symbol": item["symbol"],
                        "conid": item["conid"],
                        "exchange": item.get("exchange"),
                    })
                self.discovery_error = None
            except Exception as discovery_exc:
                self.discovery_error = f"{type(discovery_exc).__name__}: {str(discovery_exc)[:240]}"
            quotes = self.gateway.snapshots(self.contracts)
            if not quotes:
                raise RuntimeError("IBKR snapshot returned no resolved quotes")
            realtime_count = sum(_realtime_quote(q) for q in quotes.values())
            if realtime_count == 0:
                raise RuntimeError("IBKR returned no realtime-subscribed quotes (field 6509 is not Realtime)")
            mode = "IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:240]}"

        broad_market_change = (quotes.get("SPY") or {}).get("change_pct")
        qqq_change = (quotes.get("QQQ") or {}).get("change_pct")
        active_symbols = tuple(dict.fromkeys((*self.symbols, *self.discovery_symbols)))
        rows = []
        if mode.startswith("IBKR_"):
            for symbol in active_symbols:
                structural = self.structural.get(symbol)
                quote = quotes.get(symbol)
                if structural and _realtime_quote(quote):
                    rows.append(_live_state(symbol, quote, structural, broad_market_change, qqq_change))
                elif structural:
                    availability = (quote or {}).get("market_data_availability")
                    rows.append({
                        "symbol": symbol,
                        "state": structural.get("state", "WATCH"),
                        "reason": "IBKR realtime quote unavailable/not subscribed for this symbol; showing structural state only",
                        "structural_state": structural.get("state"),
                        "ibkr_quote_missing": True,
                        "market_data_availability": availability,
                    })
        else:
            for symbol in active_symbols:
                structural = self.structural.get(symbol)
                if structural:
                    rows.append({
                        "symbol": symbol,
                        "state": structural.get("state", "WATCH"),
                        "reason": "IBKR unavailable; public structural state only",
                        "structural_state": structural.get("state"),
                        "public_change_pct": (structural.get("intraday") or {}).get("change_pct"),
                        "d5_atr": (structural.get("intraday") or {}).get("d5_atr"),
                    })

        priority = {"ENTRY_CONFIRMED": 0, "ENTRY_ARMED": 1, "LEADER_HOT_NO_CHASE": 2,
                    "LEADER_WATCH": 3, "EXTENDED": 4, "WATCH": 5}
        rows.sort(key=lambda r: (priority.get(r.get("state"), 9), -(r.get("change_pct") or -999), r["symbol"]))
        now_iso = datetime.now(timezone.utc).isoformat()
        material = {"ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH"}
        events = []
        for row in rows:
            old = self.previous_states.get(row["symbol"])
            new = row.get("state")
            if new != old and (new in material or old in material):
                event = {
                    "at_utc": now_iso,
                    "symbol": row["symbol"],
                    "previous": old,
                    "state": new,
                    "mode": mode,
                    "reason": row.get("reason"),
                    "price": row.get("price"),
                    "change_pct": row.get("change_pct"),
                    "d5_atr": row.get("d5_atr"),
                    "relative_change_vs_spy_pp": row.get("relative_change_vs_spy_pp"),
                    "relative_change_vs_qqq_pp": row.get("relative_change_vs_qqq_pp"),
                }
                _append_jsonl(self.events_path, event)
                events.append(event)
            self.previous_states[row["symbol"]] = new

        state = {
            "generated_at_utc": now_iso,
            "started_at_utc": self.started_at,
            "run_id": self.run_id,
            "mode": mode,
            "ibkr_error": error,
            "ibkr_auth": self.auth,
            "contracts_resolved": len(self.contracts),
            "contract_errors": self.contract_errors,
            "coverage_scope": COVERAGE_IBKR_DYNAMIC if self.discovery_report else COVERAGE_CORE_FALLBACK,
            "market_wide_discovery": bool(self.discovery_report),
            "discovery_exhaustive": False,
            "discovery": self.discovery_report,
            "discovery_error": self.discovery_error,
            "discovery_symbol_count": len(self.discovery_symbols),
            "structural_symbol_count": len(self.structural),
            "capability_gaps": [
                "market_breadth_not_integrated",
                "sector_industry_relative_strength_not_integrated",
                "news_first_market_discovery_not_integrated",
                "historical_3y_confidence_not_integrated",
                "value_price_return_pendulum_not_integrated",
                "external_push_notification_not_configured",
            ],
            "snapshot_seconds": self.snapshot_seconds,
            "structure_seconds": self.structure_seconds,
            "last_structure_age_seconds": max(0.0, self.clock() - self.last_structure),
            "rows": rows,
            "events_this_cycle": events,
        }
        _atomic_json(self.state_path, state)
        return state

    def run(self, once=False):
        self.initialize()
        while True:
            started = self.clock()
            state = self.cycle()
            compact = {
                "generated_at_utc": state["generated_at_utc"],
                "run_id": state["run_id"],
                "mode": state["mode"],
                "ibkr_error": state["ibkr_error"],
                "material": [
                    {"symbol": r["symbol"], "state": r.get("state"), "price": r.get("price"),
                     "change_pct": r.get("change_pct", r.get("public_change_pct")),
                     "d5_atr": r.get("d5_atr")}
                    for r in state["rows"] if r.get("state") in {
                        "ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH"
                    }
                ],
            }
            signature = (
                state["mode"],
                tuple((x["symbol"], x["state"]) for x in compact["material"]),
                bool(state["ibkr_error"]),
            )
            now = self.clock()
            if signature != self.last_log_signature or now - self.last_log_at >= 60:
                print(json.dumps(compact, ensure_ascii=False, allow_nan=False), flush=True)
                self.last_log_signature = signature
                self.last_log_at = now
            if once:
                return state
            elapsed = self.clock() - started
            self.sleep(max(0.0, self.snapshot_seconds - elapsed))


def run(**kwargs):
    return HybridDaemon(**kwargs).run()
