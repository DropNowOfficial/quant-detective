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
        ("/iserver/marketdata/snapshot", [{"conid":1,"31":"123.45","83":"1.20","84":"123.4","86":"123.5","7762":"1.2M"}]),
    ])
    gw=ClientPortalGateway(opener=opener, sleeper=lambda _:None)
    assert gw.auth_status()["authenticated"]
    gw._accounts_ready=True
    out=gw.snapshots({"NVDA":{"conid":1}})
    assert out["NVDA"]["last"] == 123.45
    assert out["NVDA"]["change_pct"] == 1.2
    assert out["NVDA"]["volume"] == 1_200_000
    assert all("/orders" not in url for _,url in opener.calls)


def test_adapter_blocks_unknown_endpoint():
    gw=ClientPortalGateway(opener=opener_for([]))
    with pytest.raises(ValueError):
        gw._request("POST","/iserver/account/orders")
