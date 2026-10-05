from market_data.ibkr_live import HybridDaemon, _live_state


def structural(state="WATCH"):
    return {
        "symbol":"NVDA","status":"OK","state":state,"known_at":"2026-10-05T16:00:00Z",
        "daily":{"ma5":100.0,"atr5":10.0},
        "intraday":{"rth_vwap_approx":101.0,"same_time_rvol":1.1,"d5_atr":0.1},
    }


def test_live_leader_no_chase_uses_ibkr_price():
    row=_live_state("NVDA",{"last":115.0,"change_pct":2.2,"bid":114.9,"ask":115.1,"market_data_availability":"R"},structural(),0.8)
    assert row["state"]=="LEADER_HOT_NO_CHASE"
    assert row["d5_atr"]==1.5
    assert row["relative_change_vs_qqq_pp"] > 1.0


def test_entry_confirmation_must_already_exist_structurally():
    row=_live_state("NVDA",{"last":101.0,"change_pct":0.8,"market_data_availability":"R"},structural("ENTRY_CONFIRMED"),0.5)
    assert row["state"]=="ENTRY_CONFIRMED"
    row2=_live_state("NVDA",{"last":101.0,"change_pct":0.8},structural("WATCH"),0.5)
    assert row2["state"]!="ENTRY_CONFIRMED"


class FakeGateway:
    def __init__(self, authenticated=True):
        self.authenticated=authenticated
    def auth_status(self):
        return {"authenticated":self.authenticated,"connected":self.authenticated}
    def ensure_accounts(self):
        return ["U1"]
    def resolve_symbols(self, symbols):
        return ({s:{"conid":i+1} for i,s in enumerate(symbols)}, {})
    def tickle(self):
        return {}
    def snapshots(self, contracts):
        return {
            "QQQ":{"last":200.0,"change_pct":0.5,"market_data_availability":"R"},
            "NVDA":{"last":115.0,"change_pct":2.0,"bid":114.9,"ask":115.1,"market_data_availability":"R"},
        }


def fake_scan(symbols):
    return {"rows":[structural()]}


def test_daemon_degrades_explicitly_when_ibkr_auth_missing(tmp_path):
    d=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(False),scan_fn=fake_scan,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="DEGRADED_PUBLIC_ONLY"
    assert out["ibkr_error"]
    assert out["rows"][0]["reason"].startswith("IBKR unavailable")


def test_daemon_uses_ibkr_fast_path_when_authenticated(tmp_path):
    d=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(True),scan_fn=fake_scan,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
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
            return {
                "QQQ":{"last":200.0,"change_pct":0.5,"market_data_availability":"D"},
                "NVDA":{"last":115.0,"change_pct":2.0,"market_data_availability":"D"},
            }
    d=HybridDaemon(
        symbols=("NVDA",),gateway=DelayedGateway(True),scan_fn=fake_scan,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    out=d.cycle()
    assert out["mode"]=="DEGRADED_PUBLIC_ONLY"
    assert "no realtime-subscribed quotes" in out["ibkr_error"]


def test_initialize_writes_bootstrapping_state_before_structure_scan(tmp_path):
    state_path=tmp_path/"state.json"
    observed={}
    def scan(symbols):
        import json
        observed.update(json.loads(state_path.read_text()))
        return {"rows":[structural()]}
    d=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(False),scan_fn=scan,
        state_path=state_path,events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    assert observed["mode"]=="BOOTSTRAPPING"
    assert observed["phase"]=="initial_structure_scan"


def test_run_id_changes_between_daemon_instances(tmp_path):
    d1=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(False),scan_fn=fake_scan,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d1.initialize()
    first=d1.run_id
    d2=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(False),scan_fn=fake_scan,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d2.initialize()
    assert d2.run_id != first


def test_failed_ibkr_auth_is_backed_off_for_30_seconds(tmp_path):
    class FailingGateway:
        def __init__(self):
            self.calls=0
        def auth_status(self):
            self.calls += 1
            raise RuntimeError("offline")
    now=[1000.0]
    gw=FailingGateway()
    d=HybridDaemon(
        symbols=("NVDA",),gateway=gw,scan_fn=fake_scan,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,clock=lambda:now[0],
    )
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
        return [{
            "symbol":self.symbol,
            "conid":99,
            "exchange":"NASDAQ",
            "company_name":self.symbol+" INC",
            "first_seen":1.0,
            "last_seen":2.0,
            "scan_hits":{"Top % Gainers":{"rank":0}},
            "scan_hit_count":1,
            "best_rank":0,
            "multi_scan":False,
        }]


class DiscoveryGateway(FakeGateway):
    def __init__(self, stock_type="Common"):
        super().__init__(True)
        self.stock_type=stock_type
    def snapshots(self, contracts):
        out={
            "QQQ":{"last":200.0,"change_pct":0.5,"market_data_availability":"R","last_status":"TRADE","stock_type":"ETF"},
            "NVDA":{"last":115.0,"change_pct":2.0,"market_data_availability":"R","last_status":"TRADE","stock_type":"Common"},
        }
        if "XYZ" in contracts:
            out["XYZ"]={"last":50.0,"change_pct":3.0,"market_data_availability":"R","last_status":"TRADE","stock_type":self.stock_type}
        return out


def dynamic_scan(symbols):
    rows=[]
    for symbol in symbols:
        rows.append({
            "symbol":symbol,"status":"OK","state":"WATCH","known_at":"2026-10-05T16:00:00Z",
            "daily":{"ma5":49.0 if symbol=="XYZ" else 100.0,"atr5":5.0 if symbol=="XYZ" else 10.0},
            "intraday":{"rth_vwap_approx":49.5 if symbol=="XYZ" else 101.0,"same_time_rvol":1.1,"d5_atr":0.1},
        })
    return {"rows":rows}


def test_discovered_stock_flows_into_harness_on_next_structure_refresh(tmp_path):
    now=[1000.0]
    d=HybridDaemon(
        symbols=("NVDA",),gateway=DiscoveryGateway("Common"),scan_fn=dynamic_scan,
        discovery=FakeDiscovery("XYZ"),discovery_structure_limit=24,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,clock=lambda:now[0],
    )
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
    d=HybridDaemon(
        symbols=("NVDA",),gateway=DiscoveryGateway("ETF"),scan_fn=dynamic_scan,
        discovery=FakeDiscovery("XYZ"),discovery_structure_limit=24,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    out=d.cycle()
    candidate=out["discovery_candidates"][0]
    assert candidate["stock_type"]=="ETF"
    assert candidate["eligible_stock_type"] is False
    assert "XYZ" not in d.discovery_symbols
    assert "XYZ" not in {row["symbol"] for row in out["rows"]}


def test_halted_quote_cannot_drive_realtime_state():
    from market_data.ibkr_live import _realtime_quote
    assert not _realtime_quote({
        "last":115.0,
        "last_status":"HALTED",
        "market_data_availability":"R",
    })
