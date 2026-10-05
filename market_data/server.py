"""Local UI for public observations. No arbitrary files, URLs or write endpoints."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
from urllib.parse import parse_qs, urlsplit
from . import providers
from .transport import FetchError, fetch
from .live import LiveService


class MarketHandler(BaseHTTPRequestHandler):
    def local_request(self):
        allowed = {f'{host}:{self.server.server_port}' for host in ['127.0.0.1', 'localhost']}
        hosts = self.headers.get_all('Host') or []
        origin = self.headers.get('Origin')
        if len(hosts) != 1 or hosts[0].lower() not in allowed or (origin and origin not in {'http://' + h for h in allowed}):
            self.send_error(403, 'Local Host and Origin required')
            return False
        return True

    def send_json(self, value, status=200):
        body = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def public_fetch(self, url, kind='json'):
        # An aborted browser request must not initiate more historical pages.
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            disconnected = bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b''
        except OSError:
            disconnected = True
        if disconnected:
            raise FetchError({'url': url, 'ok': False, 'http_status': None,
                              'requested_at_utc': datetime.now(timezone.utc).isoformat(),
                              'received_at_utc': datetime.now(timezone.utc).isoformat(),
                              'error_code': 'CANCELLED', 'error': '客户端已断开，停止新的分页请求。'})
        return self.server.fetcher(url, kind=kind)

    def do_GET(self):
        if not self.local_request():
            return
        try:
            parsed = urlsplit(self.path)
            pages = {'/': 'live.html', '/index.html': 'live.html', '/market': 'template.html', '/research': 'research.html'}
            if parsed.path in pages:
                body = Path(__file__).with_name(pages[parsed.path]).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == '/health':
                self.send_json({'ok': True, 'mode': 'PUBLIC_READ_ONLY', 'real_time_claim': False,
                                'orders_enabled': False, 'api_keys_used': False, 'refresh_seconds': 12})
                return
            if parsed.path == '/api/research-summary':
                path = Path(__file__).resolve().parent.parent / 'research_outputs' / 'summary.json'
                try:
                    summary = json.loads(path.read_text())
                except (OSError, ValueError):
                    self.send_json({'ok': False, 'error': '此安装未包含冻结研究摘要；请从完整Git源码目录启动。LIVE扫描仍可独立使用。'}, 503)
                    return
                self.send_json(summary)
                return
            allowed = {'/api/catalog': {'market'}, '/api/candles': {'market', 'symbol', 'interval', 'limit'},
                       '/api/live': {'client', 'market', 'interval', 'scope', 'symbols', 'band', 'min_rvol', 'min_turnover'},
                       '/api/live/history': {'session'}, '/api/live/detail': {'session', 'symbol'}}
            if parsed.path not in allowed:
                self.send_error(404)
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) - allowed[parsed.path] or any(len(v) != 1 for v in query.values()):
                raise ValueError('查询参数重复或不受支持')
            values = {k: v[0] for k, v in query.items()}
            if parsed.path == '/api/live':
                self.send_json(self.server.live.snapshot(values, client=values.pop('client', 'default')))
                return
            if parsed.path == '/api/live/history':
                self.send_json(self.server.live.history(values.get('session', '')))
                return
            if parsed.path == '/api/live/detail':
                self.send_json(self.server.live.detail(values.get('session', ''), values.get('symbol', '')))
                return
            market = values.get('market', 'binance_spot')
            if parsed.path == '/api/catalog':
                result = providers.get_catalog(market, self.public_fetch)
            else:
                result = providers.get_candles(market, values.get('symbol', 'SOLUSDT'),
                                               values.get('interval', '1m'), int(values.get('limit', '5')),
                                               self.public_fetch)
            self.send_json(result)
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json({'ok': False, 'error': str(exc), 'rows': [], 'instruments': []}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FetchError as exc:
            self.send_json({'ok': False, **exc.receipt, 'rows': [], 'instruments': []}, 502)

    def do_POST(self):
        self.send_error(405, 'Only read-only GET is supported')

    do_PUT = do_DELETE = do_PATCH = do_POST


class MarketServer(ThreadingHTTPServer):
    def server_close(self):
        if hasattr(self, 'live'):
            self.live.close()
        super().server_close()


def make_server(port=8767, fetcher=fetch):
    server = MarketServer(('127.0.0.1', port), MarketHandler)
    server.daemon_threads = True
    server.fetcher = fetcher
    server.live = LiveService(fetcher=fetcher)
    return server
