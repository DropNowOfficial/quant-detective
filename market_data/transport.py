"""One bounded, unauthenticated HTTP GET. No retry, redirect or source fallback."""
from datetime import datetime, timezone
import json
import subprocess
from urllib.parse import parse_qsl, unquote, urlsplit


ENDPOINTS = {
    'data-api.binance.vision': {'/api/v3/klines', '/api/v3/exchangeInfo'},
    'fapi.binance.com': {'/fapi/v1/klines', '/fapi/v1/exchangeInfo'},
    'dapi.binance.com': {'/dapi/v1/klines', '/dapi/v1/exchangeInfo'},
    'api.gateio.ws': {'/api/v4/futures/usdt/contracts', '/api/v4/futures/usdt/candlesticks'},
    'www.okx.com': {'/api/v5/public/instruments', '/api/v5/market/candles', '/api/v5/market/history-candles'},
    'www.nasdaqtrader.com': {'/dynamic/SymDir/nasdaqlisted.txt', '/dynamic/SymDir/otherlisted.txt'},
    'push2.eastmoney.com': {'/api/qt/clist/get'},
    'push2his.eastmoney.com': {'/api/qt/stock/kline/get'},
    'query.sse.com.cn': {'/sseQuery/commonQuery.do'},
    'www.szse.cn': {'/api/report/ShowReport'},
    'www.cninfo.com.cn': {'/new/data/szse_stock.json'},
}
FORBIDDEN_PARAMETERS = {'apikey', 'api_key', 'key', 'secret', 'signature', 'token', 'access_token'}


class FetchError(Exception):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(receipt.get('error', 'Public request failed'))


def validate_url(url):
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    allowed = path in ENDPOINTS.get(parsed.hostname, set())
    if parsed.hostname == 'query1.finance.yahoo.com':
        allowed = ((path.startswith('/v8/finance/chart/') and '..' not in path)
                   or path == '/v1/finance/search')
    if not (parsed.scheme == 'https' and allowed and parsed.port in {None, 443}):
        raise ValueError('Only approved public HTTPS market-data endpoints are allowed')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Credentials and fragments are not allowed')
    if any(k.lower() in FORBIDDEN_PARAMETERS for k, _ in parse_qsl(parsed.query)):
        raise ValueError('Authentication parameters are not allowed')


def fetch(url, kind='json'):
    validate_url(url)
    if kind not in {'json', 'text', 'bytes'}:
        raise ValueError('Unsupported response kind')
    receipt = {'url': url, 'requested_at_utc': datetime.now(timezone.utc).isoformat(),
               'received_at_utc': None, 'http_status': None, 'ok': False,
               'error': None, 'error_code': None, 'timeout_seconds': 12}
    args = ['curl', '--disable', '--silent', '--show-error', '--max-time', '12',
            '--connect-timeout', '12', '--max-filesize', '16777216',
            '--write-out', '\n%{http_code}', url]
    referers = {'query.sse.com.cn': 'https://www.sse.com.cn/assortment/stock/list/share/',
                'www.szse.cn': 'https://www.szse.cn/market/product/stock/list/index.html',
                'www.cninfo.com.cn': 'https://www.cninfo.com.cn/'}
    if urlsplit(url).hostname in referers:
        args[2:2] = ['--referer', referers[urlsplit(url).hostname], '--user-agent', 'QuantDetective/0.1 (public read-only data)']
    try:
        options = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
        if kind != 'bytes':
            options.update(text=True, encoding='utf-8', errors='replace')
        process = subprocess.Popen(args, **options)
        try:
            output, error = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            receipt.update(error_code='TIMEOUT', error='请求超过12秒，已停止该请求。')
            raise FetchError(receipt)
        body, _, status = output.rpartition(b'\n' if kind == 'bytes' else '\n')
        if isinstance(error, bytes):
            error = error.decode('utf-8', errors='replace')
        receipt['http_status'] = int(status) if status.isdigit() and int(status) else None
        if process.returncode:
            receipt.update(error_code='TIMEOUT' if process.returncode == 28 else 'NETWORK_ERROR',
                           error='请求超时，已停止。' if process.returncode == 28 else f'网络请求失败（curl {process.returncode}）：{error.strip()[:240]}')
            raise FetchError(receipt)
        if receipt['http_status'] != 200:
            receipt.update(error_code='HTTP_ERROR', error=f'HTTP {receipt["http_status"]}；未补造K线，也未替换来源。')
            raise FetchError(receipt)
        if kind == 'json':
            try:
                data = json.loads(body)
            except (ValueError, TypeError):
                receipt.update(error_code='UPSTREAM_NON_JSON', error='HTTP 200，但响应不是可解析的JSON；不可作为行情。')
                raise FetchError(receipt)
        else:
            data = body
        receipt['ok'] = True
        return {'data': data, 'receipt': receipt}
    except OSError as exc:
        receipt.update(error_code='CURL_UNAVAILABLE', error=f'无法启动公开HTTP读取器：{exc}')
        raise FetchError(receipt) from exc
    finally:
        receipt['received_at_utc'] = datetime.now(timezone.utc).isoformat()
