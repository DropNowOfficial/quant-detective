"""One-shot public K-lines and the continuous public-data screening terminal."""
import argparse
import json
from . import providers
from .server import make_server
from .transport import fetch


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
    parser = argparse.ArgumentParser(description='公开行情与持续分钟扫描；无密钥、无交易执行。candles命令仅展示K线。')
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
    args = parser.parse_args()
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
