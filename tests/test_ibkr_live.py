from market_data.ibkr_live import HybridDaemon, _live_state


def structural(state="WATCH"):
    return {
        "symbol":"NVDA","status":"OK","state":state,"known_at":"2026-10-05T16:00:00Z",
        "daily":{"ma5":100.0,"atr5":10.0},
        "intraday":{"rth_vwap_approx":101.0,"same_time_rvol":1.1,"d5_atr":0.1},
    }


def test_live_leader_no_chase_uses_ibkr_price():
    row=_live_state("NVDA",{"last":115.0,"change_pct":2.2,"bid":114.9,"ask":115.1,"market_data_availability":"R"},structural(),0.7,0.8)
    assert row["state"]=="LEADER_HOT_NO_CHASE"
    assert row["d5_atr"]==1.5
    assert row["relative_change_vs_spy_pp"] > 1.0


def test_entry_confirmation_must_already_exist_structurally():
    row=_live_state("NVDA",{"last":101.0,"change_pct":0.8,"market_data_availability":"R"},structural("ENTRY_CONFIRMED"),0.4,0.5)
    assert row["state"]=="ENTRY_CONFIRMED"
    row2=_live_state("NVDA",{"last":101.0,"change_pct":0.8},structural("WATCH"),0.4,0.5)
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
            "SPY":{"last":700.0,"change_pct":0.4,"market_data_availability":"R"},
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
                "SPY":{"last":700.0,"change_pct":0.4,"market_data_availability":"D"},
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
    def __init__(self):
        self.round=0
    def tick(self):
        self.round += 1
        return {
            "coverage_scope":"IBKR_US_MAJOR_DYNAMIC",
            "market_wide_discovery":True,
            "exhaustive":False,
            "scan_display_name":"Top % Gainers",
            "returned":1,
        }
    def candidates(self, limit=60):
        symbol="XYZ" if self.round == 1 else "ABC"
        return [{
            "symbol":symbol,
            "conid":99 if symbol=="XYZ" else 100,
            "exchange":"NASDAQ",
            "company_name":symbol+" INC",
            "first_seen":1.0,
            "last_seen":2.0,
            "scan_hits":{"Top % Gainers":{"rank":0}},
        }]


def test_discovery_candidate_is_visible_before_structural_enrichment(tmp_path):
    d=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(True),scan_fn=fake_scan,
        discovery=FakeDiscovery(),discovery_structure_limit=24,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    out=d.cycle()
    assert out["market_wide_discovery"] is True
    assert out["discovery_exhaustive"] is False
    assert out["discovery_candidates"][0]["symbol"]=="XYZ"
    assert out["discovery_candidates"][0]["structural_enriched"] is False
    assert "XYZ" not in {r["symbol"] for r in out["rows"]}


def test_stale_dynamic_contracts_are_pruned(tmp_path):
    discovery=FakeDiscovery()
    d=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(True),scan_fn=fake_scan,
        discovery=discovery,discovery_structure_limit=24,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    d.cycle()
    assert "XYZ" in d.contracts
    d.cycle()
    assert "XYZ" not in d.contracts
    assert "ABC" in d.contracts


def test_halted_quote_cannot_drive_realtime_state():
    from market_data.ibkr_live import _realtime_quote
    assert not _realtime_quote({
        "last":115.0,
        "last_status":"HALTED",
        "market_data_availability":"RpB",
    })
    assert not _realtime_quote({
        "last":115.0,
        "last_status":"PREVIOUS_CLOSE",
        "market_data_availability":"RpB",
    })


class MultiHitDiscovery:
    def tick(self):
        return {
            "coverage_scope":"IBKR_US_MAJOR_DYNAMIC",
            "market_wide_discovery":True,
            "exhaustive":False,
            "scan_display_name":"Hot Contracts by Volume",
            "returned":1,
        }
    def candidates(self, limit=60):
        return [{
            "symbol":"XYZ",
            "conid":99,
            "exchange":"NASDAQ",
            "company_name":"XYZ INC",
            "first_seen":1.0,
            "last_seen":2.0,
            "scan_hits":{
                "Top % Gainers":{"rank":2},
                "Hot Contracts by Volume":{"rank":4},
            },
            "scan_hit_count":2,
            "best_rank":2,
            "multi_scan":True,
        }]


def test_multi_scan_discovery_emits_one_prequalification_event(tmp_path):
    d=HybridDaemon(
        symbols=("NVDA",),gateway=FakeGateway(True),scan_fn=fake_scan,
        discovery=MultiHitDiscovery(),discovery_structure_limit=24,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    first=d.cycle()
    hits=[e for e in first["events_this_cycle"] if e.get("state")=="DISCOVERY_MULTI_HIT"]
    assert len(hits)==1
    assert hits[0]["symbol"]=="XYZ"
    second=d.cycle()
    assert not [e for e in second["events_this_cycle"] if e.get("state")=="DISCOVERY_MULTI_HIT"]


def test_old_discovery_is_not_reported_as_current_after_broker_failure(tmp_path):
    class FlakyGateway(FakeGateway):
        def __init__(self):
            super().__init__(True)
            self.fail=False
        def auth_status(self):
            if self.fail:
                raise RuntimeError("session lost")
            return super().auth_status()

    gw=FlakyGateway()
    d=HybridDaemon(
        symbols=("NVDA",),gateway=gw,scan_fn=fake_scan,
        discovery=MultiHitDiscovery(),discovery_structure_limit=24,
        state_path=tmp_path/"state.json",events_path=tmp_path/"events.jsonl",
        snapshot_seconds=2,structure_seconds=60,
    )
    d.initialize()
    first=d.cycle()
    assert first["market_wide_discovery"] is True
    gw.fail=True
    d.last_auth=0
    second=d.cycle()
    assert second["mode"]=="DEGRADED_PUBLIC_ONLY"
    assert second["market_wide_discovery"] is False
    assert second["coverage_scope"]=="CORE_FALLBACK"
    assert second["discovery_stale"] is True
