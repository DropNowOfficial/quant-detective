"""Persistent hybrid watcher: IBKR fast snapshots + slower structural scans.

The daemon has no order path. IBKR is used for fast live observations; completed
RTH daily/5-minute structure and public news remain in the structural watcher.
If IBKR is unavailable, the daemon explicitly degrades to public-only mode.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from .ibkr_cpg import ClientPortalGateway
from .market_discovery import ALLOWED_STOCK_TYPES, IBKRMarketDiscovery
from .quality import BarFact, BarQualityInput, QualityResult, evaluate_bars, evaluate_quote
from .quality_profiles import US_PUBLIC_5M_POLICY, ibkr_quote_policy
from .session_clock import schedule_at
from .us_watch import DEFAULT_SYMBOLS, classify, scan_once


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


def _quote_quality(quote, *, now_ms: int, max_age_ms: int) -> QualityResult:
    # CPG's numeric parser returns floats. Only lossless integer-valued source
    # timestamps can be normalized; receipt/evaluation time is never a fallback.
    if isinstance(quote, dict):
        quote = dict(quote)
        for key in ("updated_ms", "event_ms"):
            value = quote.get(key)
            if isinstance(value, float) and math.isfinite(value) and value.is_integer():
                quote[key] = int(value)
    return evaluate_quote(quote, now_ms=now_ms, max_age_ms=max_age_ms)


def _realtime_quote(quote, *, now_ms: int, max_age_ms: int) -> bool:
    return _quote_quality(quote, now_ms=now_ms, max_age_ms=max_age_ms).observation_ok


def _unavailable_quality(now_ms, reason):
    return QualityResult("UNAVAILABLE", (reason,), False, False, now_ms,
                         None, None, None, 0)


def _structural_quality(structural, *, now_ms, schedule):
    evidence = structural.get("quality_evidence")
    if evidence is None:
        return _unavailable_quality(now_ms, "MISSING_STRUCTURAL_EVIDENCE")
    try:
        if (not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int
                or evidence["schema_version"] != 1
                or evidence.get("policy_id") != US_PUBLIC_5M_POLICY.policy_id
                or evidence.get("calendar_name") != "XNYS"
                or not isinstance(evidence.get("bars"), list)):
            raise ValueError("unsupported structural evidence")
        data = BarQualityInput(
            now_ms, evidence["captured_at_ms"],
            tuple(BarFact(**fact) for fact in evidence["bars"]),
            evidence["invalid_rows"], evidence["complete_rvol_sessions"],
            evidence["last_daily_session"],
        )
    except (KeyError, TypeError, ValueError):
        return _unavailable_quality(now_ms, "INVALID_STRUCTURAL_EVIDENCE")
    return evaluate_bars(data, US_PUBLIC_5M_POLICY, schedule)


def _current_quality(quality: QualityResult, now_ms: int) -> QualityResult:
    if not isinstance(quality, QualityResult):
        raise TypeError("quality must be a QualityResult")
    if quality.observation_ok and (type(quality.valid_until_ms) is not int
            or now_ms >= quality.valid_until_ms or now_ms < quality.evaluated_at_ms):
        return replace(quality, state="UNAVAILABLE", observation_ok=False,
                       confirmation_ok=False, valid_until_ms=None, evaluated_at_ms=now_ms,
                       reason_codes=(*quality.reason_codes, "EXPIRED_QUALITY"))
    return quality


def _combined_quality(structural: QualityResult, quote: QualityResult, now_ms: int) -> QualityResult:
    structural, quote = _current_quality(structural, now_ms), _current_quality(quote, now_ms)
    observation_ok = structural.observation_ok and quote.observation_ok
    confirmation_ok = structural.confirmation_ok and quote.confirmation_ok
    return replace(
        structural, state="VALID" if confirmation_ok else "OBSERVATION_ONLY" if observation_ok else "UNAVAILABLE",
        reason_codes=tuple(dict.fromkeys((*structural.reason_codes, *quote.reason_codes))),
        observation_ok=observation_ok, confirmation_ok=confirmation_ok, evaluated_at_ms=now_ms,
        valid_until_ms=min(structural.valid_until_ms, quote.valid_until_ms) if observation_ok else None,
    )


def _public_state(structural, *, now_ms, quality):
    """Recompute cached public classification with bounded benchmark evidence."""
    quality = _current_quality(quality, now_ms)
    benchmark = structural.get("benchmark_dependency") or {}
    benchmark_quality = benchmark.get("quality") or {}
    expiry = benchmark_quality.get("valid_until_ms")
    qqq_change = benchmark.get("change_pct") if (
        benchmark_quality.get("observation_ok") is True and type(expiry) is int
        and now_ms < expiry and not benchmark.get("reason_codes")
        and benchmark_quality.get("last_bar_end_ms") == quality.last_bar_end_ms
    ) else None
    decision = classify(structural.get("daily") or {}, structural.get("intraday") or {},
                        qqq_change=qqq_change, quality=quality)
    retained_benchmark = _finite(decision["relative_change_vs_qqq_pp"])
    if retained_benchmark:
        quality = replace(quality, valid_until_ms=min(quality.valid_until_ms, expiry))
    return {**structural, **decision, "quality": asdict(quality),
            "benchmark_quality": benchmark_quality if retained_benchmark else None}


def _live_state(symbol, quote, structural, qqq_change, *, now_ms: int, quality: QualityResult) -> dict:
    """Keep hybrid geometry; callers supply joint populated-dependency quality."""
    quality = _current_quality(quality, now_ms)
    price = quote.get("last")
    change = quote.get("change_pct")
    daily = structural.get("daily") or {}
    intra = structural.get("intraday") or {}
    ma5, atr5 = daily.get("ma5"), daily.get("atr5")
    d5 = (price - ma5) / atr5 if _finite(price) and _finite(ma5) and _finite(atr5) and atr5 > 0 else None
    rs = change - qqq_change if quality.observation_ok and _finite(change) and _finite(qqq_change) else None
    leader_reasons = []
    if quality.observation_ok and _finite(change) and change >= 1.0:
        leader_reasons.append(f"IBKR day +{change:.2f}%")
    if _finite(rs) and rs >= 0.5:
        leader_reasons.append(f"IBKR vs QQQ +{rs:.2f}pp")
    public_state = structural.get("state")
    vwap = intra.get("rth_vwap_approx")
    above_vwap = _finite(price) and _finite(vwap) and price >= vwap
    if not quality.observation_ok:
        state = "MARKET_CLOSED" if "SESSION_NOT_OPEN" in quality.reason_codes else "DATA_UNAVAILABLE"
        reason = ", ".join(quality.reason_codes)
    elif quality.confirmation_ok and public_state == "ENTRY_CONFIRMED" and _finite(d5) and -0.15 <= d5 <= 0.35 and above_vwap:
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
        "relative_change_vs_qqq_pp": rs,
        "leader_reasons": leader_reasons,
        "structural_state": public_state,
        "structural_known_at": structural.get("known_at"),
        "rth_vwap_approx": vwap,
        "same_time_rvol": intra.get("same_time_rvol"),
        "ibkr_updated_ms": quote.get("updated_ms"),
        "market_data_availability": quote.get("market_data_availability"),
        "quality": asdict(quality),
    }


class HybridDaemon:
    def __init__(
        self,
        symbols=DEFAULT_SYMBOLS,
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
        self.quote_policy = ibkr_quote_policy(snapshot_seconds)
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
        self.discovery_candidates = ()
        self.discovery_report = None
        self.discovery_error = None
        self.run_id = uuid.uuid4().hex
        self.previous_states = {}
        self.previous_mode = None
        try:
            prior = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            self.previous_mode = prior.get("mode")
            self.previous_states = {
                row.get("symbol"): row.get("state")
                for row in prior.get("rows", [])
                if row.get("symbol") and row.get("state")
            }
        except Exception:
            pass
        self.started_at = self._utc_now().isoformat()
        self.last_log_signature = None
        self.last_log_at = 0.0

    def _utc_now(self):
        """Adapt the daemon's injected epoch-seconds clock to the scan API."""
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def initialize(self):
        try:
            self.auth = self.gateway.auth_status()
            if self.auth.get("authenticated"):
                self.gateway.ensure_accounts()
                wanted = tuple(dict.fromkeys((*self.symbols, "QQQ")))
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
            "generated_at_utc": datetime.fromtimestamp(self.last_auth, timezone.utc).isoformat(),
            "started_at_utc": self.started_at,
            "run_id": self.run_id,
            "mode": "BOOTSTRAPPING",
            "phase": "initial_structure_scan",
            "ibkr_auth": self.auth,
            "contracts_resolved": len(self.contracts),
            "contract_errors": self.contract_errors,
            "discovery_current": False,
            "discovery_exhaustive": False,
            "discovery_scope": "CORE_WATCHLIST_ONLY",
            "discovery_error": self.discovery_error,
            "discovery": self.discovery_report,
            "discovery_candidates": [],
            "discovery_symbol_count": 0,
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
        report = self.scan_fn(symbols=targets, now=datetime.fromtimestamp(now, timezone.utc),
                              clock=self._utc_now)
        self.structural_report = report
        self.structural = _row_map(report)
        self.last_structure = self.clock()

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
        discovery_current = False
        raw_discovery_symbols = ()
        try:
            self._refresh_auth()
            if not self.auth.get("authenticated"):
                raise RuntimeError("IBKR gateway session is not authenticated")
            if not self.contracts:
                wanted = tuple(dict.fromkeys((*self.symbols, "QQQ")))
                self.contracts, self.contract_errors = self.gateway.resolve_symbols(wanted)
            if self.discovery is None:
                self.discovery = IBKRMarketDiscovery(self.gateway)

            try:
                self.discovery_report = self.discovery.tick()
                candidates = self.discovery.candidates(limit=self.discovery_structure_limit)
                raw_discovery_symbols = tuple(
                    item["symbol"] for item in candidates if item["symbol"] not in self.symbols
                )
                base_symbols = set((*self.symbols, "QQQ"))
                self.contracts = {
                    symbol: contract for symbol, contract in self.contracts.items()
                    if symbol in base_symbols or symbol in raw_discovery_symbols
                }
                for item in candidates:
                    self.contracts.setdefault(item["symbol"], {
                        "symbol": item["symbol"],
                        "conid": item["conid"],
                        "exchange": item.get("exchange"),
                    })
                self.discovery_error = None
                discovery_current = True
            except Exception as discovery_exc:
                self.discovery_error = f"{type(discovery_exc).__name__}: {str(discovery_exc)[:240]}"

            quotes = self.gateway.snapshots(self.contracts)
            if not quotes:
                raise RuntimeError("IBKR snapshot returned no resolved quotes")

            self.discovery_symbols = tuple(
                symbol for symbol in raw_discovery_symbols
                if (quotes.get(symbol) or {}).get("stock_type") in ALLOWED_STOCK_TYPES
            )
            self.discovery_candidates = tuple({
                **item,
                "stock_type": (quotes.get(item["symbol"]) or {}).get("stock_type"),
                "eligible_stock_type": (quotes.get(item["symbol"]) or {}).get("stock_type") in ALLOWED_STOCK_TYPES,
                "structural_enriched": item["symbol"] in self.structural,
            } for item in (self.discovery.candidates(limit=self.discovery_structure_limit) if discovery_current else []))

        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:240]}"

        # Acquisition and queue delays count as elapsed time. Use one injected
        # post-acquisition clock for the decisions, state file, and local events.
        now = self.clock()
        now_ms = int(now * 1000)
        quote_qualities = {symbol: _quote_quality(q, now_ms=now_ms,
                            max_age_ms=self.quote_policy.response_max_age_ms)
                           for symbol, q in quotes.items()}
        if error is None:
            if any(q.observation_ok for q in quote_qualities.values()):
                mode = "IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
            else:
                error = "RuntimeError: IBKR returned no realtime-subscribed quotes with valid current timestamps/prices"
        qqq_quality = quote_qualities.get("QQQ")
        qqq_change = (quotes.get("QQQ") or {}).get("change_pct") if (
            qqq_quality is not None and qqq_quality.observation_ok) else None
        schedule = schedule_at(now_ms, calendar_name="XNYS",
                               interval_ms=US_PUBLIC_5M_POLICY.interval_ms,
                               grace_ms=US_PUBLIC_5M_POLICY.publication_grace_ms)
        active_symbols = tuple(dict.fromkeys((*self.symbols, *self.discovery_symbols))) if mode.startswith("IBKR_") else self.symbols
        rows = []
        for symbol in active_symbols:
            original = self.structural.get(symbol)
            if not original:
                continue
            structural_quality = _structural_quality(original, now_ms=now_ms, schedule=schedule)
            structural = _public_state(original, now_ms=now_ms, quality=structural_quality)
            quote = quotes.get(symbol)
            quote_quality = quote_qualities.get(symbol) or _quote_quality(
                quote, now_ms=now_ms, max_age_ms=self.quote_policy.response_max_age_ms)
            if mode.startswith("IBKR_") and quote_quality.observation_ok:
                # The retained public structural_state has its own benchmark
                # deadline, independently of the fast path's live QQQ source.
                public_quality = replace(structural_quality,
                                         valid_until_ms=structural["quality"]["valid_until_ms"])
                quality = _combined_quality(public_quality, quote_quality, now_ms)
                if quality.observation_ok and _finite(quote.get("change_pct")) and _finite(qqq_change):
                    quality = _combined_quality(quality, qqq_quality, now_ms)
                row = _live_state(symbol, quote, structural, qqq_change,
                                  now_ms=now_ms, quality=quality)
                row["quality_policy_id"] = self.quote_policy.policy_id
                row["structural_quality"] = asdict(structural_quality)
                row["benchmark_quality"] = asdict(qqq_quality) if qqq_quality is not None else None
                row["structural_benchmark_quality"] = structural["benchmark_quality"]
            else:
                reason = ("IBKR realtime quote unavailable/not subscribed for this symbol; showing structural state only"
                          if mode.startswith("IBKR_") else "IBKR unavailable; public structural state only")
                row = {
                    "symbol": symbol, "state": structural["state"],
                    "reason": f"{reason}; {structural['reason']}",
                    "structural_state": structural["state"], "structural_known_at": original.get("known_at"),
                    "public_change_pct": (structural.get("intraday") or {}).get("change_pct"),
                    "d5_atr": (structural.get("intraday") or {}).get("d5_atr"),
                    "relative_change_vs_qqq_pp": structural["relative_change_vs_qqq_pp"],
                    "leader_reasons": structural["leader_reasons"],
                    "ibkr_quote_missing": True,
                    "market_data_availability": (quote or {}).get("market_data_availability"),
                    "quality": structural["quality"],
                    "structural_quality": asdict(structural_quality),
                    "benchmark_quality": structural["benchmark_quality"],
                    "quality_policy_id": US_PUBLIC_5M_POLICY.policy_id,
                }
            row["structural_quality_policy_id"] = US_PUBLIC_5M_POLICY.policy_id
            row["quote_quality"] = asdict(quote_quality)
            row["quote_quality_policy_id"] = self.quote_policy.policy_id
            rows.append(row)

        priority = {"ENTRY_CONFIRMED": 0, "ENTRY_ARMED": 1, "LEADER_HOT_NO_CHASE": 2,
                    "LEADER_WATCH": 3, "EXTENDED": 4, "WATCH": 5}
        rows.sort(key=lambda r: (priority.get(r.get("state"), 9), -(r.get("change_pct") or -999), r["symbol"]))
        now_iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
        material = {"ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH",
                    "DATA_UNAVAILABLE", "MARKET_CLOSED"}
        events = []
        if mode != self.previous_mode:
            event = {"at_utc": now_iso, "event_type": "MODE_CHANGE", "previous": self.previous_mode,
                     "state": mode, "mode": mode, "reason": error}
            _append_jsonl(self.events_path, event)
            events.append(event)
        self.previous_mode = mode
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
                    "relative_change_vs_qqq_pp": row.get("relative_change_vs_qqq_pp"),
                    "quality": row.get("quality"),
                    "quality_policy_id": row.get("quality_policy_id"),
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
            "discovery_current": bool(discovery_current and mode.startswith("IBKR_")),
            "discovery_exhaustive": False,
            "discovery_scope": "IBKR_US_MAJOR_TOP_N" if discovery_current and mode.startswith("IBKR_") else "CORE_WATCHLIST_ONLY",
            "discovery_error": self.discovery_error,
            "discovery": self.discovery_report,
            "discovery_candidates": list(self.discovery_candidates),
            "discovery_symbol_count": len(self.discovery_symbols),
            "snapshot_seconds": self.snapshot_seconds,
            "structure_seconds": self.structure_seconds,
            "last_structure_age_seconds": max(0.0, now - self.last_structure),
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
