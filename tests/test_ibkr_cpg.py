import io
import json

import pytest

from market_data.ibkr_cpg import ClientPortalGateway, CPGError


class Response:
    def __init__(self, payload, status=200):
        self.payload = json.dumps(payload).encode()
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.payload


def opener_for(routes):
    calls=[]
    def opener(request, timeout=None, context=None):
        calls.append((request.method, request.full_url))
        for needle, payload in routes:
            if needle in request.full_url:
                return Response(payload)
        raise AssertionError(request.full_url)
    opener.calls=calls
    return opener


def test_gateway_rejects_non_loopback():
    with pytest.raises(ValueError):
        ClientPortalGateway("https://example.com/v1/api")


def test_auth_and_snapshot_are_read_only():
    opener=opener_for([
        ("/iserver/auth/status", {"authenticated":True,"connected":True}),
        ("/iserver/accounts", {"accounts":["U1"]}),
        ("/iserver/marketdata/snapshot", [{"conid":1,"31":"123.45","83":"1.20%","84":"123.4","86":"123.5","7762":"1.2M","6509":"RpB","7899":"Common"}]),
    ])
    gw=ClientPortalGateway(opener=opener, sleeper=lambda _:None)
    assert gw.auth_status()["authenticated"]
    gw._accounts_ready=True
    out=gw.snapshots({"NVDA":{"conid":1}})
    assert out["NVDA"]["last"] == 123.45
    assert out["NVDA"]["change_pct"] == 1.2
    assert out["NVDA"]["volume"] == 1_200_000
    assert out["NVDA"]["market_data_availability"] == "RpB"
    assert out["NVDA"]["stock_type"] == "Common"
    snapshot_urls=[url for _,url in opener.calls if "/iserver/marketdata/snapshot" in url]
    assert snapshot_urls and "6509" in snapshot_urls[0] and "7899" in snapshot_urls[0]
    assert all("/orders" not in url for _,url in opener.calls)


def test_adapter_blocks_unknown_endpoint():
    gw=ClientPortalGateway(opener=opener_for([]))
    with pytest.raises(ValueError):
        gw._request("POST","/iserver/account/orders")


def test_allowed_paths_contain_no_order_routes():
    from market_data.ibkr_cpg import ALLOWED_PATHS
    assert ALLOWED_PATHS
    assert all("order" not in path.lower() for path in ALLOWED_PATHS)
    assert set(ALLOWED_PATHS) == {
        "/iserver/auth/status",
        "/tickle",
        "/iserver/accounts",
        "/iserver/secdef/search",
        "/iserver/marketdata/snapshot",
        "/iserver/scanner/params",
        "/iserver/scanner/run",
    }


def test_market_scanner_is_read_only():
    opener=opener_for([
        ("/iserver/scanner/params", {
            "scan_type_list":[{"display_name":"Top % Gainers","code":"GAIN","instruments":["STK"]}]
        }),
        ("/iserver/scanner/run", {"contracts":[{"symbol":"NVDA","con_id":4815747}]}),
    ])
    gw=ClientPortalGateway(opener=opener, sleeper=lambda _:None)
    assert gw.scanner_params()["scan_type_list"][0]["code"] == "GAIN"
    out=gw.scanner_run(instrument="STK", location="STK.US.MAJOR", scan_type="GAIN")
    assert out["contracts"][0]["symbol"] == "NVDA"
    assert all("order" not in url.lower() for _,url in opener.calls)


def test_tickle_uses_get():
    opener=opener_for([("/tickle", {"session":"ok"})])
    gw=ClientPortalGateway(opener=opener, sleeper=lambda _:None)
    assert gw.tickle()["session"] == "ok"
    assert opener.calls[-1][0] == "GET"


def test_last_price_status_preserves_halt_and_previous_close():
    from market_data.ibkr_cpg import _last_price
    assert _last_price("H 123.45") == (123.45, "HALTED")
    assert _last_price("C 122.00") == (122.0, "PREVIOUS_CLOSE")
    assert _last_price("124.10") == (124.1, "TRADE")
