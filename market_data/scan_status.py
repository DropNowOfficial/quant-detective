"""Bounded coverage evidence shared by the scanner and health journal."""
import re

REJECTION_CODES = frozenset({
    'MISSING_OR_INVALID_RECEIPT_TIME', 'RECEIPT_EXPIRED', 'RECEIPT_IN_FUTURE',
    'MISSING_OR_INVALID_BAR_TIME', 'BAR_IN_FUTURE', 'BAR_EXPIRED',
    'BAR_SESSION_DATE_MISMATCH', 'BAR_OUTSIDE_EQUITY_SESSION',
    'DAILY_SESSION_MISMATCH', 'MISSING_OR_INCONSISTENT_PROVENANCE',
    'PREVIOUSLY_REJECTED_SNAPSHOT',
})


def coverage_details(report, symbols=None):
    requested = list(symbols) if symbols is not None else report.get('symbols_requested')
    rows = report.get('rows')
    if (not isinstance(requested, list) or not 1 <= len(requested) <= 60
            or any(not isinstance(s, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,15}', s) for s in requested)
            or len(set(requested)) != len(requested) or not isinstance(rows, list)
            or len(rows) != len(requested)
            or any(not isinstance(row, dict) or not isinstance(row.get('symbol'), str)
                   or row['symbol'] not in requested or row.get('status') not in ('OK', 'ERROR', 'INVALID_DATA') for row in rows)
            or len({row['symbol'] for row in rows}) != len(requested)):
        return None
    missing, reasons = [], {}
    for row in rows:
        if row['status'] == 'OK':
            continue
        symbol = row['symbol']
        missing.append(symbol)
        intra = row.get('intraday')
        validity = intra.get('quote_validity') if isinstance(intra, dict) else None
        raw = validity.get('rejection_reasons') if isinstance(validity, dict) else None
        codes = sorted({code for code in raw if isinstance(code, str) and code in REJECTION_CODES}) if isinstance(raw, list) else []
        reasons[symbol] = codes or ['ACQUISITION_ERROR' if row['status'] == 'ERROR' else 'INVALID_QUOTE']
    return {'usable': len(requested) - len(missing), 'requested': len(requested),
            'missing': sorted(missing), 'reasons': dict(sorted(reasons.items()))}
