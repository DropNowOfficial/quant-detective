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
                {"display_name":"Unrelated","code":"OTHER","instruments":["FUT.US"]},
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


def test_discovery_rotates_available_whole_market_scanners():
    now=[1000.0]
    gw=Gateway()
    d=IBKRMarketDiscovery(
        gw,
        scan_names=("Top % Gainers","Hot Contracts by Volume","Missing"),
        clock=lambda:now[0],
    )
    first=d.tick()
    assert first["market_wide_discovery"] is True
    assert first["exhaustive"] is False
    assert first["scan_display_name"]=="Top % Gainers"
    now[0]+=2
    second=d.tick()
    assert second["scan_display_name"]=="Hot Contracts by Volume"
    assert gw.params_calls==1
    assert len(gw.run_calls)==2
    assert "Missing" in second["missing_requested_scans"]


def test_candidates_rank_multi_scan_hits_ahead():
    now=[1000.0]
    d=IBKRMarketDiscovery(
        Gateway(),
        scan_names=("Top % Gainers","Hot Contracts by Volume"),
        clock=lambda:now[0],
    )
    d.tick()
    now[0]+=2
    d.tick()
    rows=d.candidates()
    assert rows[0]["symbol"]=="BBB"
    assert len(rows[0]["scan_hits"])==2
