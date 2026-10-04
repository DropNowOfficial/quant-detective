import json
from http.client import HTTPConnection
from threading import Thread
from urllib.parse import urlsplit


def test_market_server_routes_only_public_get_and_guards_host(monkeypatch):
    from market_data import server
    calls=[]
    def catalog(market,fetch):
        calls.append(market)
        return {'ok':True,'market':market,'instruments':[{'symbol':'BTCUSDT','name':'BTC'}]}
    monkeypatch.setattr(server.providers,'get_catalog',catalog)
    app=server.make_server(0)
    worker=Thread(target=app.serve_forever,daemon=True);worker.start()
    try:
        def request(path,host=None,method='GET'):
            c=HTTPConnection('127.0.0.1',app.server_port,timeout=3)
            c.request(method,path,headers={'Host':host or f'127.0.0.1:{app.server_port}'})
            r=c.getresponse();status=r.status;body=r.read();c.close();return status,body
        status,body=request('/api/catalog?market=binance_spot')
        assert status==200 and json.loads(body)['instruments'][0]['symbol']=='BTCUSDT'
        assert request('/api/catalog?market=binance_spot','foreign.example')[0]==403
        assert request('/api/catalog?market=a&market=b')[0]==400
        assert request('/api/catalog?url=https://example.com')[0]==400
        assert request('/../research_outputs/summary.json')[0]==404
        assert request('/api/orders',method='POST')[0]==405
        assert calls==['binance_spot']
    finally:
        app.shutdown();app.server_close();worker.join()
