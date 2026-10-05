import argparse
import json
import sys
from pathlib import Path
from .replay import run_replay

def main():
    parser=argparse.ArgumentParser(description='Offline synthetic quote replay only')
    commands=parser.add_subparsers(dest='command',required=True)
    replay=commands.add_parser('replay')
    for name in ('input','rules','database','output'): replay.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try: report=run_replay(args.input,args.rules,args.database,args.output)
    except (ValueError,OSError) as e:
        print(str(e),file=sys.stderr); return 2
    print(json.dumps(report,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': sys.exit(main())
