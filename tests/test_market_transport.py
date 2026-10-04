import subprocess
import pytest


def test_market_get_is_single_public_request_and_non200_fails(monkeypatch):
    from market_data import transport
    calls=[]
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):calls.append(args)
        def communicate(self,timeout):
            assert timeout==12
            return '{"message":"restricted"}\n451',''
    monkeypatch.setattr(transport.subprocess,'Popen',Process)
    with pytest.raises(transport.FetchError) as caught:transport.fetch('https://fapi.binance.com/fapi/v1/exchangeInfo')
    assert caught.value.receipt['http_status']==451
    assert len(calls)==1 and '--max-time' in calls[0]
    assert calls[0][1] == '--disable', 'Ignore local curlrc credentials, redirects and retry settings'
    assert '--user' not in calls[0] and '-H' not in calls[0]


def test_market_total_deadline_kills_request(monkeypatch):
    from market_data import transport
    killed=[]
    class Process:
        returncode=-9
        def __init__(self,*args,**kwargs):self.n=0
        def communicate(self,timeout=None):
            self.n+=1
            if self.n==1:raise subprocess.TimeoutExpired('curl',12)
            return 'partial',''
        def kill(self):killed.append(True)
    monkeypatch.setattr(transport.subprocess,'Popen',Process)
    with pytest.raises(transport.FetchError) as caught:transport.fetch('https://www.okx.com/api/v5/public/instruments?instType=SWAP')
    assert killed==[True] and caught.value.receipt['error_code']=='TIMEOUT'
    assert caught.value.receipt['http_status'] is None


def test_http200_html_is_not_successful_market_data(monkeypatch):
    from market_data import transport
    class Process:
        returncode=0
        def __init__(self,*args,**kwargs):pass
        def communicate(self,timeout):return '<html>Site Unavailable</html>\n200',''
    monkeypatch.setattr(transport.subprocess,'Popen',Process)
    with pytest.raises(transport.FetchError) as caught:transport.fetch('https://www.okx.com/api/v5/public/instruments?instType=SWAP')
    assert caught.value.receipt['http_status']==200
    assert caught.value.receipt['error_code']=='UPSTREAM_NON_JSON'


def test_market_transport_rejects_unapproved_or_credentialed_urls():
    from market_data import transport
    for url in ['http://data-api.binance.vision/api/v3/klines','https://key:secret@data-api.binance.vision/api/v3/klines','https://example.com/prices','https://fapi.binance.com/fapi/v1/order','https://data-api.binance.vision/api/v3/klines?apiKey=secret']:
        with pytest.raises(ValueError):transport.fetch(url)


def test_public_catalog_binary_is_preserved(monkeypatch):
    from market_data import transport
    body=b'PK\x03\x04\x00\xff\x00'
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):assert not kwargs.get('text',False)
        def communicate(self,timeout):return body+b'\n200',b''
    monkeypatch.setattr(transport.subprocess,'Popen',Process)
    out=transport.fetch('https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110x',kind='bytes')
    assert out['data']==body
