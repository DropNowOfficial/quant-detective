from market_data.market_discovery import IBKRMarketDiscovery


class Gateway:
    def __init__(self):
        self.params_calls=0
        self.run_calls=[]
    def scanner_params(self):
        self.params_calls += 1
        return {
            "scan_type_list":[
                {"display_name":"Top % Gainers","code":"GAIN","instruments":["STK"]},
                {"display_name":"Hot Contracts by Volume","code":"HOTVOL","instruments":["STK"]},
            ]
        }
    def scanner_run(self, *, instrument, location, scan_type, filters=None):
        self.run_calls.append((instrument,location,scan_type))
        if scan_type=="GAIN":
            return {"contracts":[
                {"symbol":"AAA","con_id":1,"listing_exchange":"NASDAQ","company_name":"AAA","scan_data":"4.2%"},
                {"symbol":"BBB","con_id":2,"listing_exchange":"NYSE","company_name":"BBB","scan_data":"3.8%"},
            ]}
        return {"contracts":[
            {"symbol":"BBB","con_id":2,"listing_exchange":"NYSE","company_name":"BBB","scan_data":"2.1x"},
            {"symbol":"CCC","con_id":3,"listing_exchange":"NASDAQ","company_name":"CCC","scan_data":"1.9x"},
        ]}


def test_discovery_rotates_provider_scanners_and_ranks_consensus():
    now=[1000.0]
    gw=Gateway()
    d=IBKRMarketDiscovery(
        gw,
        scan_names=("Top % Gainers","Hot Contracts by Volume"),
        clock=lambda:now[0],
    )
    first=d.tick()
    assert first["scan_display_name"]=="Top % Gainers"
    now[0]+=2
    second=d.tick()
    assert second["scan_display_name"]=="Hot Contracts by Volume"
    assert gw.params_calls==1
    rows=d.candidates()
    assert rows[0]["symbol"]=="BBB"
    assert rows[0]["scan_hit_count"]==2
    assert rows[0]["multi_scan"] is True


def test_discovery_pacing_blocks_faster_than_one_request_per_second():
    now=[1000.0]
    gw=Gateway()
    d=IBKRMarketDiscovery(gw, clock=lambda:now[0], min_scan_interval_seconds=1.05)
    d.tick()
    calls=len(gw.run_calls)
    now[0]+=0.5
    paced=d.tick()
    assert paced["paced"] is True
    assert len(gw.run_calls)==calls
    now[0]+=0.6
    d.tick()
    assert len(gw.run_calls)==calls+1


def test_discovery_candidates_expire():
    now=[1000.0]
    d=IBKRMarketDiscovery(Gateway(), clock=lambda:now[0], candidate_ttl_seconds=10)
    d.tick()
    assert d.candidates()
    now[0]+=11
    assert d.candidates()==[]
