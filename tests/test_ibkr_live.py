"""Synthetic fixed-clock hybrid quality and transition tests; no live services."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json

import pytest

from market_data.ibkr_live import HybridDaemon, _live_state, _realtime_quote
from market_data.quality import BarFact, BarQualityInput, evaluate_bars
from market_data.quality_profiles import US_PUBLIC_5M_POLICY
from market_data.session_clock import schedule_at

NOW = datetime(2026, 10, 6, 13, 41, 30, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
OPEN_MS = int(datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)


def quote(*, now_ms=NOW_MS, **overrides):
    return {"last":115.0,"change_pct":2.0,"bid":114.9,"ask":115.1,
            "market_data_availability":"R","last_status":"TRADE","updated_ms":now_ms,
            **overrides}


def structural(state="WATCH", *, captured_at_ms=NOW_MS, **evidence_overrides):
    # Preserve both bars completed at source receipt and any still-open bar.
    facts = [BarFact(start, start+300_000, start+300_000 <= captured_at_ms)
             for start in range(OPEN_MS, captured_at_ms+1, 300_000)]
    evidence = {"schema_version":1,"policy_id":US_PUBLIC_5M_POLICY.policy_id,
                "calendar_name":"XNYS","captured_at_ms":captured_at_ms,
                "bars":[asdict(fact) for fact in facts],"invalid_rows":0,
                "complete_rvol_sessions":20,"last_daily_session":"2026-10-05",
                **evidence_overrides}
    quality = evaluate_bars(BarQualityInput(captured_at_ms,evidence["captured_at_ms"],
                            tuple(BarFact(**fact) for fact in evidence["bars"]),
                            evidence["invalid_rows"],evidence["complete_rvol_sessions"],
                            evidence["last_daily_session"]),US_PUBLIC_5M_POLICY,
                            schedule_at(captured_at_ms,calendar_name="XNYS",interval_ms=300_000,grace_ms=60_000))
    return {
        "symbol":"NVDA","status":"OK","state":state,
        "known_at":datetime.fromtimestamp(captured_at_ms/1000,timezone.utc).isoformat(),
        "daily":{"ma5":100.0,"atr5":10.0,"ma5_slope_1d":1.0 if state.startswith("ENTRY") else 0.0,
                 "ma5_3point_slope":1.0},
        "intraday":{"rth_vwap_approx":101.0,"same_time_rvol":1.1,"d5_atr":0.1,
                    "change_pct":0.2,"two_completed_5m_above_vwap_and_ma5":state=="ENTRY_CONFIRMED"},
        "quality":asdict(quality),"quality_policy_id":US_PUBLIC_5M_POLICY.policy_id,
        "quality_evidence":evidence,
    }


def live_row(q, s=None, qqq_change=0.8):
    s = s or structural()
    return _live_state("NVDA",q,s,qqq_change,now_ms=NOW_MS,
                       quality=evaluate_bars(BarQualityInput(NOW_MS,s["quality_evidence"]["captured_at_ms"],
                           tuple(BarFact(**fact) for fact in s["quality_evidence"]["bars"]),
                           s["quality_evidence"]["invalid_rows"],s["quality_evidence"]["complete_rvol_sessions"],
                           s["quality_evidence"]["last_daily_session"]),US_PUBLIC_5M_POLICY,
                           schedule_at(NOW_MS,calendar_name="XNYS",interval_ms=300_000,grace_ms=60_000)))


def test_live_leader_no_chase_uses_ibkr_price():
    row=live_row(quote(change_pct=2.2))
    assert row["state"]=="LEADER_HOT_NO_CHASE"
    assert row["d5_atr"]==1.5
    assert row["relative_change_vs_qqq_pp"] > 1.0


def test_entry_confirmation_must_already_exist_structurally():
    row=live_row(quote(last=101.0,change_pct=0.8),structural("ENTRY_CONFIRMED"),0.5)
    assert row["state"]=="ENTRY_CONFIRMED"
    row2=live_row(quote(last=101.0,change_pct=0.8),structural("WATCH"),0.5)
    assert row2["state"]!="ENTRY_CONFIRMED"


@pytest.mark.parametrize("price,expected",[(98.5,True),(103.5,True),(98.49,False),(103.51,False)])
def test_hybrid_entry_geometry_is_preserved(price,expected):
    s=structural("ENTRY_CONFIRMED")
    s["intraday"]["rth_vwap_approx"]=98.0
    row=live_row(quote(last=price,change_pct=0.8),s,0.5)
    assert (row["state"]=="ENTRY_CONFIRMED") is expected


class FakeGateway:
    def __init__(self, authenticated=True, clock=lambda:NOW.timestamp()):
        self.authenticated=authenticated
        self.clock=clock
    def auth_status(self):
        return {"authenticated":self.authenticated,"connected":self.authenticated}
    def ensure_accounts(self):
        return ["U1"]
    def resolve_symbols(self, symbols):
        return ({s:{"conid":i+1} for i,s in enumerate(symbols)}, {})
    def tickle(self):
        return {}
    def snapshots(self, contracts):
        now_ms=int(self.clock()*1000)
        return {"QQQ":quote(now_ms=now_ms,last=200.0,change_pct=0.5),
                "NVDA":quote(now_ms=now_ms)}


def fake_scan(symbols, *, now=None, clock=None):
    captured = (clock() if clock else now or NOW)
    return {"rows":[structural(captured_at_ms=int(captured.timestamp()*1000))]}


def daemon(tmp_path, *, gateway=None, scan_fn=fake_scan, clock=lambda:NOW.timestamp(), **kwargs):
    return HybridDaemon(symbols=("NVDA",),gateway=gateway or FakeGateway(clock=clock),scan_fn=scan_fn,
                        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
                        snapshot_seconds=kwargs.pop("snapshot_seconds",2),
                        structure_seconds=kwargs.pop("structure_seconds",60),clock=clock,**kwargs)


def test_daemon_degrades_explicitly_when_ibkr_auth_missing(tmp_path):
    d=daemon(tmp_path,gateway=FakeGateway(False))
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="DEGRADED_PUBLIC_ONLY"
    assert out["ibkr_error"]
    assert out["rows"][0]["reason"].startswith("IBKR unavailable")


def test_daemon_uses_ibkr_fast_path_when_authenticated(tmp_path):
    d=daemon(tmp_path)
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
    row=out["rows"][0]
    assert row["price"]==115.0
    assert row["state"]=="LEADER_HOT_NO_CHASE"
    assert (tmp_path/"state.json").exists()
    assert (tmp_path/"events.jsonl").exists()


def test_delayed_ibkr_quote_cannot_drive_fast_path(tmp_path):
    class DelayedGateway(FakeGateway):
        def snapshots(self, contracts):
            return {s:{**q,"market_data_availability":"D"} for s,q in super().snapshots(contracts).items()}
    d=daemon(tmp_path,gateway=DelayedGateway())
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="DEGRADED_PUBLIC_ONLY"
    assert "no realtime-subscribed quotes" in out["ibkr_error"]


def test_initialize_writes_bootstrapping_state_before_structure_scan(tmp_path):
    observed={}
    def scan(symbols, *, now=None, clock=None):
        observed.update(json.loads((tmp_path/"state.json").read_text()))
        return fake_scan(symbols,now=now,clock=clock)
    d=daemon(tmp_path,gateway=FakeGateway(False),scan_fn=scan)
    d.initialize()
    assert observed["mode"]=="BOOTSTRAPPING"
    assert observed["phase"]=="initial_structure_scan"
    assert observed["started_at_utc"]==NOW.isoformat()
    assert observed["generated_at_utc"]==NOW.isoformat()


def test_run_id_changes_between_daemon_instances(tmp_path):
    d1=daemon(tmp_path,gateway=FakeGateway(False))
    d1.initialize()
    d2=daemon(tmp_path,gateway=FakeGateway(False))
    d2.initialize()
    assert d2.run_id != d1.run_id


def test_failed_ibkr_auth_is_backed_off_for_30_seconds(tmp_path):
    class FailingGateway:
        def __init__(self):
            self.calls=0
        def auth_status(self):
            self.calls += 1
            raise RuntimeError("offline")
    now=[NOW.timestamp()]
    gw=FailingGateway()
    d=daemon(tmp_path,gateway=gw,clock=lambda:now[0])
    d.initialize()
    assert gw.calls == 1
    d.cycle()
    assert gw.calls == 1
    now[0] += 29
    d.cycle()
    assert gw.calls == 1
    now[0] += 1
    d.cycle()
    assert gw.calls == 2


class FakeDiscovery:
    def __init__(self, symbol="XYZ"):
        self.symbol=symbol
    def tick(self):
        return {"active":True,"paced":False,"scan_display_name":"Top % Gainers","returned":1}
    def candidates(self, limit=24):
        return [{"symbol":self.symbol,"conid":99,"exchange":"NASDAQ","company_name":self.symbol+" INC",
                 "first_seen":1.0,"last_seen":2.0,"scan_hits":{"Top % Gainers":{"rank":0}},
                 "scan_hit_count":1,"best_rank":0,"multi_scan":False}]


class DiscoveryGateway(FakeGateway):
    def __init__(self, stock_type="Common", clock=lambda:NOW.timestamp()):
        super().__init__(True,clock)
        self.stock_type=stock_type
    def snapshots(self, contracts):
        out=super().snapshots(contracts)
        out["QQQ"]["stock_type"]="ETF"
        out["NVDA"]["stock_type"]="Common"
        if "XYZ" in contracts:
            out["XYZ"]=quote(now_ms=int(self.clock()*1000),last=50.0,change_pct=3.0,stock_type=self.stock_type)
        return out


def dynamic_scan(symbols, *, now=None, clock=None):
    report=fake_scan(symbols,now=now,clock=clock)
    rows=[]
    for symbol in symbols:
        row=deepcopy(report["rows"][0])
        row["symbol"]=symbol
        if symbol=="XYZ":
            row["daily"].update(ma5=49.0,atr5=5.0)
            row["intraday"]["rth_vwap_approx"]=49.5
        rows.append(row)
    return {"rows":rows}


def test_discovered_stock_flows_into_harness_on_next_structure_refresh(tmp_path):
    now=[NOW.timestamp()]
    clock=lambda:now[0]
    d=daemon(tmp_path,gateway=DiscoveryGateway("Common",clock),scan_fn=dynamic_scan,
             discovery=FakeDiscovery("XYZ"),discovery_structure_limit=24,clock=clock)
    d.initialize()
    first=d.cycle()
    assert first["discovery_current"] is True
    assert first["discovery_scope"]=="IBKR_US_MAJOR_TOP_N"
    assert first["discovery_candidates"][0]["symbol"]=="XYZ"
    assert first["discovery_candidates"][0]["eligible_stock_type"] is True
    assert first["discovery_candidates"][0]["structural_enriched"] is False
    assert "XYZ" not in {row["symbol"] for row in first["rows"]}
    now[0]+=61
    second=d.cycle()
    assert "XYZ" in {row["symbol"] for row in second["rows"]}


def test_etf_discovery_does_not_enter_stock_harness(tmp_path):
    d=daemon(tmp_path,gateway=DiscoveryGateway("ETF"),scan_fn=dynamic_scan,
             discovery=FakeDiscovery("XYZ"),discovery_structure_limit=24)
    d.initialize()
    out=d.cycle()
    candidate=out["discovery_candidates"][0]
    assert candidate["stock_type"]=="ETF"
    assert candidate["eligible_stock_type"] is False
    assert "XYZ" not in d.discovery_symbols
    assert "XYZ" not in {row["symbol"] for row in out["rows"]}


def test_updated_ms_one_is_not_realtime():
    assert _realtime_quote(quote(updated_ms=1),now_ms=NOW_MS,max_age_ms=6000) is False


@pytest.mark.parametrize("overrides,expected",[
    ({"updated_ms":None},False),({"updated_ms":True},False),({"updated_ms":0},False),
    ({"updated_ms":NOW_MS-6000},False),({"updated_ms":NOW_MS-5999},True),
    ({"updated_ms":NOW_MS+5000},True),({"updated_ms":NOW_MS+5001},False),
    ({"updated_ms":float(NOW_MS)},True),({"updated_ms":NOW_MS+0.5},False),
    ({"updated_ms":str(NOW_MS)},False),({"updated_ms":float("inf")},False),
    ({"last_status":None},False),({"last_status":"HALTED"},False),
    ({"last_status":"PREVIOUS_CLOSE"},False),({"market_data_availability":"D"},False),
    ({"market_data_availability":None},False),({"last":0},False),({"last":-1},False),
    ({"last":True},False),({"last":float("nan")},False),
    ({"event_ms":NOW_MS-6000},False),({"event_ms":NOW_MS+5001},False),
    ({"event_ms":True},False),({"event_ms":float(NOW_MS)},True),
])
def test_quote_quality_boundaries(overrides,expected):
    assert _realtime_quote(quote(**overrides),now_ms=NOW_MS,max_age_ms=6000) is expected


def test_missing_quote_timestamp_and_status_do_not_get_defaults():
    q=quote()
    del q["updated_ms"]
    assert _realtime_quote(q,now_ms=NOW_MS,max_age_ms=6000) is False
    q=quote()
    del q["last_status"]
    assert _realtime_quote(q,now_ms=NOW_MS,max_age_ms=6000) is False
    assert _realtime_quote(None,now_ms=NOW_MS,max_age_ms=6000) is False


def confirmed_scan(symbols, *, now=None, clock=None):
    captured=(clock() if clock else now or NOW)
    return {"rows":[structural("ENTRY_CONFIRMED",captured_at_ms=int(captured.timestamp()*1000))]}


@pytest.mark.parametrize("authenticated",[False,True])
def test_stale_public_fallback_cannot_retain_confirmation(tmp_path,authenticated):
    now=[NOW.timestamp()]
    class MissingStockGateway(FakeGateway):
        def snapshots(self, contracts):
            return {"QQQ":super().snapshots(contracts)["QQQ"]}
    d=daemon(tmp_path,gateway=MissingStockGateway(authenticated,lambda:now[0]),scan_fn=confirmed_scan,
             clock=lambda:now[0],structure_seconds=600)
    d.initialize()
    first=d.cycle()
    assert first["rows"][0]["state"]=="ENTRY_CONFIRMED"
    original=deepcopy(d.structural["NVDA"]["quality_evidence"])
    now[0]+=120
    row=d.cycle()["rows"][0]
    assert row["state"]!="ENTRY_CONFIRMED"
    assert row["state"]=="DATA_UNAVAILABLE"
    assert "STALE_RESPONSE" in row["quality"]["reason_codes"]
    assert row["quality"]["valid_until_ms"] is None
    assert d.structural["NVDA"]["quality_evidence"]==original
    assert any(event["state"]=="DATA_UNAVAILABLE" for event in map(json.loads,(tmp_path/"events.jsonl").read_text().splitlines()))


def test_quote_fresh_but_structure_stale_cannot_confirm(tmp_path):
    now=[NOW.timestamp()]
    class EntryGateway(FakeGateway):
        def snapshots(self, contracts):
            out=super().snapshots(contracts)
            out["NVDA"].update(last=101.0,change_pct=0.8)
            return out
    d=daemon(tmp_path,gateway=EntryGateway(clock=lambda:now[0]),scan_fn=confirmed_scan,
             clock=lambda:now[0],structure_seconds=600)
    d.initialize()
    first=d.cycle()["rows"][0]
    assert first["state"]=="ENTRY_CONFIRMED"
    assert first["quality"]["valid_until_ms"]==NOW_MS+6000
    now[0]+=120
    row=d.cycle()["rows"][0]
    assert row["state"]!="ENTRY_CONFIRMED"
    assert row["quality"]["observation_ok"] is False
    assert "STALE_RESPONSE" in row["quality"]["reason_codes"]


def test_stale_qqq_quote_does_not_supply_relative_change(tmp_path):
    class StaleQQQGateway(FakeGateway):
        def snapshots(self, contracts):
            out=super().snapshots(contracts)
            out["QQQ"]["updated_ms"]=NOW_MS-6000
            return out
    d=daemon(tmp_path,gateway=StaleQQQGateway())
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
    row=out["rows"][0]
    assert row["relative_change_vs_qqq_pp"] is None
    assert row["state"]=="LEADER_HOT_NO_CHASE"
    assert row["leader_reasons"]==["IBKR day +2.00%"]


def test_source_quote_expiry_can_bound_structure_before_snapshot_expiry(tmp_path):
    now=[NOW.timestamp()]
    class OlderEntryGateway(FakeGateway):
        def snapshots(self, contracts):
            out=super().snapshots(contracts)
            out["NVDA"].update(last=101.0,change_pct=0.8,updated_ms=int(now[0]*1000)-5999)
            return out
    d=daemon(tmp_path,gateway=OlderEntryGateway(clock=lambda:now[0]),scan_fn=confirmed_scan,clock=lambda:now[0])
    d.initialize()
    row=d.cycle()["rows"][0]
    assert row["state"]=="ENTRY_CONFIRMED"
    assert row["quality"]["valid_until_ms"]==NOW_MS+1


def test_original_open_bar_does_not_mature_and_schedule_is_refreshed(tmp_path):
    now=[NOW.timestamp()]
    d=daemon(tmp_path,gateway=FakeGateway(False),scan_fn=confirmed_scan,clock=lambda:now[0],structure_seconds=600)
    d.initialize()
    before=d.cycle()["rows"][0]
    assert before["state"]=="ENTRY_CONFIRMED"
    # The 09:40 bar was open at receipt. It is now calendar-due but remains open in evidence.
    now[0]=datetime(2026,10,6,13,46,tzinfo=timezone.utc).timestamp()
    after=d.cycle()["rows"][0]
    assert after["state"]=="DATA_UNAVAILABLE"
    assert after["quality"]["last_bar_end_ms"]==OPEN_MS+600_000
    assert after["quality"]["expected_bar_end_ms"]==OPEN_MS+900_000
    assert "STALE_BAR" in after["quality"]["reason_codes"]


@pytest.mark.parametrize("mutation",["missing","malformed","wrong_policy","invalid_rows","insufficient_history"])
def test_unqualified_structural_evidence_cannot_confirm(tmp_path,mutation):
    def scan(symbols, *, now=None, clock=None):
        report=confirmed_scan(symbols,now=now,clock=clock)
        s=report["rows"][0]
        if mutation=="missing":
            s.pop("quality_evidence")
        elif mutation=="malformed":
            s["quality_evidence"]["bars"]=[{"open_ms":True}]
        elif mutation=="wrong_policy":
            s["quality_evidence"]["policy_id"]="unknown"
        elif mutation=="invalid_rows":
            s["quality_evidence"]["invalid_rows"]=1
        else:
            s["quality_evidence"]["complete_rvol_sessions"]=19
        return report
    d=daemon(tmp_path,gateway=FakeGateway(False),scan_fn=scan)
    d.initialize()
    row=d.cycle()["rows"][0]
    assert row["state"]!="ENTRY_CONFIRMED"
    assert row["quality"]["confirmation_ok"] is False
    if mutation=="insufficient_history":
        assert row["state"]=="ENTRY_ARMED"
        assert row["quality"]["observation_ok"] is True


def test_daemon_passes_clock_to_scan_and_evaluates_after_snapshot_acquisition(tmp_path):
    now=[NOW.timestamp()]
    observed=[]
    def scan(symbols, *, now=None, clock=None):
        assert clock is not None
        observed.append(clock())
        return confirmed_scan(symbols,now=now,clock=clock)
    class SlowGateway(FakeGateway):
        def snapshots(self, contracts):
            now[0]+=6
            out=super().snapshots(contracts)
            out["NVDA"].update(last=101.0,change_pct=0.8)
            return out
    d=daemon(tmp_path,gateway=SlowGateway(clock=lambda:now[0]),scan_fn=scan,clock=lambda:now[0])
    d.initialize()
    state=d.cycle()
    row=state["rows"][0]
    assert observed==[NOW]
    assert state["mode"]=="IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
    assert row["state"]=="ENTRY_CONFIRMED"
    assert row["quality"]["evaluated_at_ms"]==NOW_MS+6000
    assert state["generated_at_utc"]==datetime.fromtimestamp(now[0],timezone.utc).isoformat()
    assert row["structural_known_at"]==NOW.isoformat()
    assert d.structural["NVDA"]["quality_evidence"]["captured_at_ms"]==NOW_MS
    assert all(event["at_utc"]==state["generated_at_utc"] for event in state["events_this_cycle"])


def test_stale_snapshot_changes_mode_and_recovers_after_full_refresh(tmp_path):
    now=[NOW.timestamp()]
    gw=FakeGateway(clock=lambda:now[0])
    raw_snapshots=gw.snapshots
    def source_snapshots(contracts):
        out=raw_snapshots(contracts)
        out["NVDA"].update(last=101.0,change_pct=0.8)
        return out
    gw.snapshots=source_snapshots
    d=daemon(tmp_path,gateway=gw,scan_fn=confirmed_scan,clock=lambda:now[0],structure_seconds=600)
    d.initialize()
    first=d.cycle()
    assert first["mode"]=="IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
    assert first["rows"][0]["state"]=="ENTRY_CONFIRMED"
    gw.snapshots=lambda contracts:{s:{**q,"updated_ms":1} for s,q in source_snapshots(contracts).items()}
    now[0]+=120
    unavailable=d.cycle()
    assert unavailable["mode"]=="DEGRADED_PUBLIC_ONLY"
    assert unavailable["rows"][0]["state"]=="DATA_UNAVAILABLE"
    assert any(event.get("event_type")=="MODE_CHANGE" for event in unavailable["events_this_cycle"])
    gw.snapshots=source_snapshots
    # A fresh quote alone cannot restore old structural content.
    assert d.cycle()["rows"][0]["state"]=="DATA_UNAVAILABLE"
    d.refresh_structure(force=True)
    recovered=d.cycle()
    assert recovered["rows"][0]["state"]=="ENTRY_CONFIRMED"
    assert recovered["rows"][0]["quality"]["observation_ok"] is True
    assert d.structural["NVDA"]["quality_evidence"]["captured_at_ms"]==NOW_MS+120000


def test_public_relative_leader_is_recomputed_when_cached_benchmark_expires(tmp_path):
    now=[NOW.timestamp()]
    def scan(symbols, *, now=None, clock=None):
        report=fake_scan(symbols,now=now,clock=clock)
        s=report["rows"][0]
        s.update(state="LEADER_WATCH",benchmark_dependency={"change_pct":-1.0,"reason_codes":[],
                 "quality":{**s["quality"],"valid_until_ms":NOW_MS+1000}})
        return report
    d=daemon(tmp_path,gateway=FakeGateway(False),scan_fn=scan,clock=lambda:now[0],structure_seconds=600)
    d.initialize()
    assert d.cycle()["rows"][0]["state"]=="LEADER_WATCH"
    now[0]+=1
    row=d.cycle()["rows"][0]
    assert row["state"]=="WATCH"
    assert row["relative_change_vs_qqq_pp"] is None


def test_custom_snapshot_period_controls_quote_expiry(tmp_path):
    class OldGateway(FakeGateway):
        def snapshots(self, contracts):
            return {s:{**q,"updated_ms":NOW_MS-7000} for s,q in super().snapshots(contracts).items()}
    d=daemon(tmp_path,gateway=OldGateway(),snapshot_seconds=3)
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="IBKR_LIVE_PLUS_PUBLIC_STRUCTURE"
    assert out["rows"][0]["quality"]["valid_until_ms"]==NOW_MS+2000


def test_structural_expiry_bounds_fresh_quote_confirmation(tmp_path):
    now=[NOW.timestamp()]
    class EntryGateway(FakeGateway):
        def snapshots(self, contracts):
            out=super().snapshots(contracts)
            out["NVDA"].update(last=101.0,change_pct=0.8)
            return out
    d=daemon(tmp_path,gateway=EntryGateway(clock=lambda:now[0]),scan_fn=confirmed_scan,
             clock=lambda:now[0],structure_seconds=600)
    d.initialize()
    now[0]+=119.999
    row=d.cycle()["rows"][0]
    assert row["state"]=="ENTRY_CONFIRMED"
    assert row["quality"]["valid_until_ms"]==NOW_MS+120000


def test_live_state_rejects_expired_quality_even_with_confirmation_flag():
    from market_data.quality import QualityResult
    q=QualityResult("VALID",(),True,True,NOW_MS-1000,NOW_MS,None,None,2)
    row=_live_state("NVDA",quote(last=101.0),structural("ENTRY_CONFIRMED"),0.5,
                    now_ms=NOW_MS,quality=q)
    assert row["state"]=="DATA_UNAVAILABLE"
    assert row["quality"]["observation_ok"] is False


def test_session_close_cannot_keep_hybrid_confirmation(tmp_path):
    now=[NOW.timestamp()]
    d=daemon(tmp_path,scan_fn=confirmed_scan,clock=lambda:now[0],structure_seconds=100000)
    d.initialize()
    now[0]=datetime(2026,10,6,20,0,tzinfo=timezone.utc).timestamp()
    row=d.cycle()["rows"][0]
    assert row["state"]=="MARKET_CLOSED"
    assert "SESSION_NOT_OPEN" in row["quality"]["reason_codes"]


def test_quote_timestamp_normalization_keeps_original_source_unchanged():
    q=quote(updated_ms=float(NOW_MS),event_ms=float(NOW_MS))
    original=deepcopy(q)
    assert _realtime_quote(q,now_ms=NOW_MS,max_age_ms=6000) is True
    assert q==original
    assert type(q["updated_ms"]) is float
    assert type(q["event_ms"]) is float


def test_real_gateway_numeric_timestamp_qualifies_without_capture_substitution():
    from market_data.ibkr_cpg import ClientPortalGateway
    class Response:
        status=200
        def __enter__(self):
            return self
        def __exit__(self,*args):
            return False
        def read(self):
            return json.dumps([{"conid":1,"31":"101.0","83":"0.8%","6509":"RpB","_updated":NOW_MS}]).encode()
    gw=ClientPortalGateway(opener=lambda *args,**kwargs:Response(),sleeper=lambda _:None)
    gw._accounts_ready=True
    q=gw.snapshots({"NVDA":{"conid":1}})["NVDA"]
    assert type(q["updated_ms"]) is float
    assert _realtime_quote(q,now_ms=NOW_MS,max_age_ms=6000) is True
    assert _realtime_quote(q,now_ms=NOW_MS+6000,max_age_ms=6000) is False
