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
