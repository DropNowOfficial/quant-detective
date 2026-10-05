"""Read immutable source snapshots. Missing/contradictory observations fail closed."""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .calendar import aware_timestamp, exchange_calendar, required_sessions

COLS = ['open', 'high', 'low', 'close', 'volume']


def validate_universe(universe):
    if not isinstance(universe, dict) or universe.get('schema_version') != 1:
        raise ValueError('股票池 schema_version 必须为 1')
    instruments = universe.get('instruments')
    if not isinstance(instruments, list) or not instruments:
        raise ValueError('股票池缺少 instruments')
    symbols, ids = set(), set()
    for item in instruments:
        if not isinstance(item, dict):
            raise ValueError('股票池成员必须是对象')
        symbol = item.get('symbol', '')
        cid = item.get('contract_id')
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,11}', symbol):
            raise ValueError('非法股票代码')
        if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
            raise ValueError('合约 ID 必须为正整数')
        if symbol in symbols or cid in ids:
            raise ValueError('股票池存在重复代码或合约')
        if item.get('exchange') not in {'NASDAQ', 'NYSE', 'AMEX', 'ARCA'}:
            raise ValueError('首版只接受已明确的美国主交易所')
        if item.get('group') not in {'core', 'discovery', 'benchmark'}:
            raise ValueError('股票池组别必须为 core/discovery/benchmark')
        symbols.add(symbol); ids.add(cid)
    for symbol in ['QQQ', 'SOXX']:
        if not any(i['symbol'] == symbol and i['group'] == 'benchmark' for i in instruments):
            raise ValueError(f'缺少基准 {symbol}')
    return instruments


def load_manifest(raw_dir):
    path = Path(raw_dir).parent/'request_manifest.json'
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text())
        return {r['file']: r for r in rows if isinstance(r, dict)}
    except (ValueError, TypeError, KeyError):
        return {}  # Every source still has to prove its own request contract.


def load_instrument(raw_dir, instrument, target, manifest):
    symbol = instrument['symbol']
    path = Path(raw_dir)/f'{symbol}_daily.json'
    evidence = dict(file=path.name, sha256=None, source=None, retrieved_at=None,
                    adjustment='NOT_EXPLICITLY_REPORTED', last_session=None, missing_dates=[], invalid_dates=[])
    if not path.is_file():
        return None, evidence, ['MISSING_FILE']
    content = path.read_bytes()
    evidence['sha256'] = hashlib.sha256(content).hexdigest()
    try:
        doc = json.loads(content)
        if not isinstance(doc, dict): raise ValueError('source envelope must be an object')
        if doc.get('synthetic') is True: raise ValueError('synthetic source not permitted')
        contract, request, data = doc['contract'], doc['request'], doc['data']
        if not all(isinstance(x, dict) for x in [contract, request, data]): raise ValueError('invalid source object')
        recorded = manifest.get(path.name, {})
        if recorded.get('sha256') == evidence['sha256']:
            request = {**recorded.get('request', {}), **request}
        if (doc['symbol'] != symbol or contract['symbol'] != symbol or
            contract['underlying_contract_id'] != instrument['contract_id'] or
            contract['exchange'] != instrument['exchange'] or
            request.get('contract_id') != instrument['contract_id']):
            return None, evidence, ['IDENTITY_MISMATCH']
        if (request.get('outside_rth') is not False or request.get('security_type') != 'STK' or
            request.get('step') != 'ONE_DAY' or data.get('chart_step') != 86400):
            return None, evidence, ['SOURCE_CONTRACT']
        evidence['source'] = data.get('source')
        evidence['retrieved_at'] = aware_timestamp(doc['retrieved_at']).isoformat()
        evidence['delayed'] = data.get('delayed')
        if data.get('source') != 'Last': return None, evidence, ['PRICE_SOURCE']
        keys = ['time'] + COLS
        if any(not isinstance(data.get(k), list) for k in keys): raise ValueError('missing source arrays')
        if len({len(data[k]) for k in keys}) != 1 or not data['time']: raise ValueError('unequal or empty arrays')
        times = pd.DatetimeIndex([aware_timestamp(t) for t in data['time']])
        dates = pd.DatetimeIndex(times.tz_convert('America/New_York').date)
        before = dates <= target
        future_times, future_dates = times[~before], dates[~before]
        evidence['ignored_future_rows'] = int((~before).sum())
        evidence['ignored_future_time_defects'] = bool(not future_times.is_monotonic_increasing or future_dates.has_duplicates)
        dates, times = dates[before], times[before]
        if not times.is_monotonic_increasing or dates.has_duplicates: return None, evidence, ['TIME_ORDER']
        cal = exchange_calendar()
        if any(not cal.is_session(d) or cal.session_open(d) != t for d,t in zip(dates,times)):
            return None, evidence, ['SESSION_TIMESTAMP']
        frame = pd.DataFrame({k:np.array(data[k], dtype=object)[before] for k in COLS}, index=dates)
        boolean = frame.map(lambda x: isinstance(x, bool)).any(axis=1)
        frame = frame.apply(pd.to_numeric, errors='coerce').astype(float)
        valid = pd.Series(np.isfinite(frame.to_numpy()).all(axis=1), index=dates)
        valid &= ~boolean & (frame[['open','high','low','close']] > 0).all(axis=1) & (frame.volume >= 0)
        valid &= (frame.high >= frame[['open','close']].max(axis=1)) & (frame.low <= frame[['open','close']].min(axis=1)) & (frame.high >= frame.low)
        expected = required_sessions(target, 21 if instrument['group']=='benchmark' else 61)
        evidence['required_sessions'] = len(expected)
        missing = expected.difference(dates)
        invalid = dates[~valid].intersection(expected)
        evidence.update(last_session=str(dates[-1].date()) if len(dates) else None,
                        source_rows_through_session=len(frame),
                        missing_dates=[str(d.date()) for d in missing],
                        invalid_dates=[str(d.date()) for d in invalid],
                        quarantined_older_rows=int((~valid & (dates < expected[0])).sum()))
        blocks = []
        if aware_timestamp(evidence['retrieved_at']) < cal.session_close(target)+pd.Timedelta(minutes=30):
            blocks.append('SOURCE_NOT_FINAL')
        if target not in dates: blocks.append('STALE')
        if len(missing): blocks.append('MISSING_SESSIONS')
        if len(invalid): blocks.append('INVALID_OHLCV')
        actions = data.get('corp_actions', [])
        if not isinstance(actions, list): raise ValueError('corporate actions must be a list')
        for action in actions:
            if not isinstance(action, dict): raise ValueError('invalid corporate action')
            if 'split' in str(action.get('type', '')).lower():
                event_date = pd.Timestamp(action.get('date'))
                if pd.isna(event_date): raise ValueError('split date missing')
                if event_date.tzinfo: event_date = event_date.tz_localize(None)
                if expected[0] <= event_date.normalize() <= target:
                    blocks.append('SPLIT_ADJUSTMENT_UNKNOWN')
        # Reindex before returning: missing dates never shorten indicator windows.
        frame.loc[~valid, COLS] = np.nan
        return frame.reindex(expected), evidence, list(dict.fromkeys(blocks))
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError) as exc:
        evidence['schema_error'] = str(exc)[:180]
        return None, evidence, ['DATA_SCHEMA']
