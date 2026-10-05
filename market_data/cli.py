"""One-shot public K-lines, local LIVE UI, and headless U.S. watch."""
import argparse
import json
import re

from . import providers
from .server import make_server
from .transport import fetch
from .us_watch import run as run_us_watch
from .universe import CORE_FALLBACK_SYMBOLS
from .ibkr_cpg import ClientPortalGateway
from .ibkr_live import HybridDaemon


def print_candles(result):
    print(f'来源：{result.get("source")}｜品种：{result.get("symbol")}｜周期：{result.get("interval")}｜请求根数：{result.get("requested_count")}')
    print(f'请求时间（UTC）：{result.get("requested_at_utc")}｜HTTP：{result.get("http_status")}')
    if not result.get('ok'):
        print('获取失败：' + str(result.get('error')))
        print('URL：' + str(result.get('source_url')))
        return
    fields = ['open_time_utc', 'open', 'high', 'low', 'close', 'volume']
    headers = ['开盘时间（UTC）', '开盘价', '最高价', '最低价', '收盘价', '成交量']
    if result['market'] == 'binance_spot':
        fields.append('taker_buy_volume')
        headers.append('主动买量')
    print('\t'.join(headers + ['状态']))
    for row in result.get('rows', []):
        print('\t'.join(str(row.get(k)) if row.get(k) is not None else '未提供' for k in fields) + '\t' + ('已收盘' if row.get('closed') else '未收盘'))
    if result['market'] == 'gate_usdt':
        print('来源 B 没有主动买量。')
    print('成交量单位：' + str(result.get('volume_unit')))
    for warning in result.get('warnings', []):
        print('说明：' + str(warning))
    print(f'实际返回 {len(result.get("rows", []))} 根；上游请求尝试 {result.get("request_count")} 次。单次快照，无交易信号。')


def main():
    parser = argparse.ArgumentParser(description='公开行情、持续分钟扫描和无浏览器美股监控；无交易执行。')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['catalog', 'candles']:
        command = sub.add_parser(name)
        command.add_argument('--market', choices=providers.MARKETS, default='binance_spot')
        command.add_argument('--json', action='store_true')
        if name == 'candles':
            command.add_argument('--symbol', default='SOLUSDT')
            command.add_argument('--interval', choices=providers.INTERVALS, default='1m')
            command.add_argument('--limit', type=int, default=5)

    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8767)

    watch = sub.add_parser('watch')
    watch.add_argument('--symbols', default=','.join(CORE_FALLBACK_SYMBOLS))
    watch.add_argument('--poll-seconds', type=int, default=60)
    watch.add_argument('--duration-minutes', type=int, default=0)
    watch.add_argument('--once', action='store_true')
    watch.add_argument('--github-alerts', action='store_true')
    watch.add_argument('--output')

    ibkr = sub.add_parser('ibkr-watch')
    ibkr.add_argument('--symbols', default=','.join(CORE_FALLBACK_SYMBOLS))
    ibkr.add_argument('--snapshot-seconds', type=float, default=2.0)
    ibkr.add_argument('--structure-seconds', type=float, default=60.0)
    ibkr.add_argument('--gateway-url', default='https://127.0.0.1:5000/v1/api')
    ibkr.add_argument('--state-path', default='runtime/market-watch/state.json')
    ibkr.add_argument('--events-path', default='runtime/market-watch/events.jsonl')
    ibkr.add_argument('--once', action='store_true')

    args = parser.parse_args()

    if args.command == 'ibkr-watch':
        symbols = tuple(dict.fromkeys(x.strip().upper() for x in args.symbols.split(',') if x.strip()))
        if not symbols or len(symbols) > 60 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,15}', x) for x in symbols):
            parser.exit(2, 'ibkr-watch symbols invalid or too many (max 60)\n')
        try:
            gateway = ClientPortalGateway(args.gateway_url)
            HybridDaemon(
                symbols=symbols,
                gateway=gateway,
                snapshot_seconds=args.snapshot_seconds,
                structure_seconds=args.structure_seconds,
                state_path=args.state_path,
                events_path=args.events_path,
            ).run(once=args.once)
        except (ValueError, TypeError, OSError, RuntimeError) as exc:
            parser.exit(2, f'ibkr-watch failed: {exc}\n')
        return

    if args.command == 'watch':
        symbols = tuple(dict.fromkeys(s.strip().upper() for s in args.symbols.split(',') if s.strip()))
        if not symbols or len(symbols) > 60 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,15}', s) for s in symbols):
            parser.exit(2, 'watch symbols invalid or too many (max 60)\n')
        try:
            run_us_watch(
                symbols=symbols,
                poll_seconds=args.poll_seconds,
                duration_minutes=args.duration_minutes,
                github_alerts=args.github_alerts,
                once=args.once,
                output=args.output,
            )
        except (ValueError, TypeError, OSError) as exc:
            parser.exit(2, f'watch failed: {exc}\n')
        return

    if args.command == 'serve':
        app = make_server(args.port)
        print(f'Quant Detective LIVE：http://127.0.0.1:{app.server_port}/（公开行情分批12秒轮询，页面打开后启动扫描）', flush=True)
        try:
            app.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.server_close()
        return

    try:
        result = (providers.get_catalog(args.market, fetch) if args.command == 'catalog' else
                  providers.get_candles(args.market, args.symbol, args.interval, args.limit, fetch))
        if args.json or args.command == 'catalog':
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print_candles(result)
        if not result.get('ok'):
            parser.exit(2)
    except (ValueError, TypeError, OSError) as exc:
        parser.exit(2, f'无法完成公开读取：{exc}\n')


if __name__ == '__main__':
    main()
