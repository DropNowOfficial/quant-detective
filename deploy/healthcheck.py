from __future__ import annotations

import json
from pathlib import Path
import sys

from runtime_health import state_health


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        path = Path(args[0] if args else '/var/lib/quant-detective/state.json')
        max_age = float(args[1] if len(args) > 1 else '15')
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError
        data = json.loads(path.read_text(encoding='utf-8'))
        age = state_health(data, max_age)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({'ok': False, 'reason': 'INVALID_OR_STALE_RUNTIME_STATE'}))
        return 2
    print(json.dumps({'ok': True, 'age_seconds': round(age, 2), 'mode': data['mode'],
                      'run_id': data['run_id'], 'scope': 'daemon_liveness_only'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
