from datetime import datetime, timezone
import argparse
import json
from pathlib import Path

from .artifacts import save_run
from .engine import Policy, screen
from .server import make_server

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser(description='真实日线选股与证据留档；不下单、不连接实时行情')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['screen','serve']:
        p=sub.add_parser(name)
        p.add_argument('--raw',type=Path,default=ROOT/'research/raw')
        p.add_argument('--universe',type=Path,default=ROOT/'stock_screener/universe.json')
        p.add_argument('--output',type=Path,default=ROOT/'screening_runs')
        if name=='serve':p.add_argument('--port',type=int,default=8765)
        else:
            p.add_argument('--as-of',default=None)
            p.add_argument('--session',default=None)
            p.add_argument('--min-price',type=float,default=5)
            p.add_argument('--min-adv20',type=float,default=20000000)
            p.add_argument('--max-atr-fraction',type=float,default=.12)
            p.add_argument('--min-rs20',type=float,default=None)
    args=parser.parse_args()
    try:
        universe=json.loads(args.universe.read_text())
        if args.command=='serve':
            server=make_server(args.raw,universe,args.output,args.port)
            print(f'选股工作台：http://127.0.0.1:{server.server_port}  |  Ctrl+C 停止',flush=True)
            try: server.serve_forever()
            except KeyboardInterrupt: pass
            finally: server.server_close()
        else:
            result=screen(args.raw,universe,Policy(args.min_price,args.min_adv20,args.max_atr_fraction,args.min_rs20),datetime.now(timezone.utc) if args.as_of is None else args.as_of,args.session)
            path=save_run(result,args.output)
            print(json.dumps(dict(session=result['session'],mode=result['mode'],counts=result['counts'],run_id=result['run_id'],output=str(path)),ensure_ascii=False))
    except (ValueError,TypeError,OSError) as exc:
        parser.exit(2,f'无法完成筛选：{exc}\n')


if __name__=='__main__': main()
