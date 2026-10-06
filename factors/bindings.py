"""Wrap existing outputs without calculating indicators or inventing source clocks.

Receipt-bound unknown publication clocks are explicitly reconstructed exploratory
upper bounds. ``recorded_at`` checks the call clock; it never becomes input time.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import math

from .models import FactorObservation, FactorRef, require_utc
from .registry import fingerprint
from market_data.quality import QualityResult
from market_data.session_clock import schedule_at

BINDINGS = {
    'us': [('us.daily.ma5_slope_1', 'daily', 'ma5_slope_1d'),
           ('us.intraday.distance_ma5_atr5', 'intraday', 'd5_atr'),
           ('us.intraday.relative_qqq_change', None, 'relative_change_vs_qqq_pp'),
           ('us.intraday.rvol_same_time20', 'intraday', 'same_time_rvol'),
           ('us.intraday.vwap_rth_hlc3', 'intraday', 'rth_vwap_approx')],
    'minute': [('minute.rvol_prior20', 'metrics', 'rvol20'), ('minute.vwap60', 'metrics', 'vwap60')],
    'research': [('research.daily.volume_ratio20', 'features', 'volume_ratio'),
                 ('research.daily.adv20', 'features', 'adv20')],
}
UNKNOWN_SOURCE = 'SOURCE_PUBLICATION_TIME_UNKNOWN'
UNKNOWN_PROVIDER = 'PROVIDER_AVAILABILITY_TIME_UNKNOWN'


def _time(value):
    if isinstance(value, datetime):
        return require_utc(value)
    if not isinstance(value, str):
        raise ValueError('SOURCE_PROVENANCE_MISSING')
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('UTC_REQUIRED')
    return parsed.astimezone(UTC)


def _ms(value):
    if type(value) is not int or value < 0:
        raise ValueError('SOURCE_PROVENANCE_MISSING')
    return datetime.fromtimestamp(value / 1000, UTC)


def _rows(report):
    """Accept native rows or explicit us_report/minute_report/research_report."""
    if not isinstance(report, dict):
        raise ValueError('INVALID_REPORT')
    for kind in BINDINGS:
        nested = report.get(kind + '_report')
        if nested is not None:
            for row, _, generated in _rows(nested):
                yield row, kind, generated
    rows = report.get('rows', [report] if 'symbol' in report else [])
    if not isinstance(rows, list):
        raise ValueError('INVALID_REPORT')
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = ('us' if 'daily' in row or 'intraday' in row else
                'minute' if 'metrics' in row else 'research' if 'features' in row else None)
        if kind:
            yield row, kind, report.get('generated_at_utc')


def _quality(row):
    value = row.get('quality')
    if isinstance(value, QualityResult):
        return value
    if not isinstance(value, dict):
        raise ValueError('SOURCE_QUALITY_MISSING')
    return QualityResult(**{**value, 'reason_codes': tuple(value.get('reason_codes', ()))})


def _native_evidence(row, kind, id, generated):
    evidence = row.get('quality_evidence') or {}
    if kind == 'research':
        # Native research feature maps have no original event/receipt clocks.
        # A caller must attach actual source evidence; a run clock cannot do it.
        raise ValueError('SOURCE_PROVENANCE_MISSING')
    quality = _quality(row)
    if kind == 'minute':
        observed = _ms(row.get('signal_time_ms'))
        receipt = _time(row.get('received_at_utc'))
        inputs = {'metrics': row.get('metrics'), 'quality_evidence': evidence}
        dependency_receipts = []
        benchmark = row.get('benchmark_dependency') or {}
        if benchmark.get('captured_at_ms') is not None:
            dependency_receipts.append(_ms(benchmark['captured_at_ms']))
    else:
        intra = row.get('intraday') or {}
        if id == 'us.daily.ma5_slope_1':
            daily = evidence.get('daily') or {}
            receipt = _ms(daily.get('captured_at_ms'))
            day = (row.get('daily') or {}).get('previous_session_date')
            day_clock = _time(day + 'T16:00:00Z') if isinstance(day, str) else None
            schedule = schedule_at(int(day_clock.timestamp()*1000) if day_clock else -1,
                                   calendar_name='XNYS', interval_ms=300_000, grace_ms=0)
            observed = _ms(schedule.close_ms)
            inputs = {'daily': row.get('daily'), 'quality_evidence': daily}
            dependency_receipts = []
        else:
            receipt = _ms(evidence.get('captured_at_ms'))
            observed = _time(intra.get('current_bar_time_utc'))
            if id in {'us.intraday.rvol_same_time20', 'us.intraday.vwap_rth_hlc3'}:
                observed = _ms(quality.last_bar_end_ms)
            inputs = {'daily': row.get('daily'), 'intraday': intra, 'quality_evidence': evidence}
            dependency_receipts = []
            if id in {'us.intraday.distance_ma5_atr5', 'us.intraday.relative_qqq_change'}:
                dependency_receipts.append(_ms((evidence.get('daily') or {}).get('captured_at_ms')))
            if id == 'us.intraday.rvol_same_time20':
                dependency_receipts.append(_ms((evidence.get('history') or {}).get('captured_at_ms')))
    computed = _time(generated) if generated else _ms(quality.evaluated_at_ms)
    # Dependencies were ingested before the existing result was computed.
    ingested = max(receipt, *dependency_receipts) if dependency_receipts else receipt
    source = row.get('source')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('SOURCE_PROVENANCE_MISSING')
    return dict(observed_at=observed, source_published_at=None, provider_available_at=None,
                ingested_at=ingested, computed_at=computed,
                effective_available_at=max(ingested, computed), source_ref=source,
                input_hash=fingerprint(inputs), input_refs=[source + '@' + receipt.isoformat()],
                dependency_available_ats=dependency_receipts,
                original_receipt_at=receipt)



def _dependency_evidence(value):
    """Retain original dependency inputs, source identity and receipt clocks.

    Wrapper/computation/freshness evaluation clocks are operational, not source
    input identity. Original capture/publication/provider timestamps stay bound.
    """
    operational = {'recorded_at', 'computed_at', 'generated_at_utc',
                   'evaluated_at_ms', 'valid_until_ms', 'response_age_seconds'}
    if isinstance(value, dict):
        return {key: _dependency_evidence(item) for key, item in value.items()
                if key not in operational}
    if isinstance(value, (list, tuple)):
        return [_dependency_evidence(item) for item in value]
    return value


def _wrap(row, kind, id, value, generated):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value)):
        raise ValueError('INVALID_FACTOR_VALUE')
    explicit = row.get('factor_evidence')
    if explicit:
        original = explicit.get(id, explicit)
        times = dict(original)
        receipt = _time(times.get('original_receipt_at', times.get('ingested_at')))
    else:
        times = _native_evidence(row, kind, id, generated)
        receipt = times.pop('original_receipt_at')
    basis = ['Original source-bound report value; exploratory only']
    if not explicit:
        basis.append('COMPUTATION_TIME_UPPER_BOUND: original report generation or quality evaluation clock; exact calculation timestamp not captured')
        basis.append('INPUT_HASH_BASIS: original report output and captured dependency evidence, not a raw-source-file hash')
    if id == 'us.intraday.rvol_same_time20' and type((row.get('intraday') or {}).get('same_time_rvol_samples')) is int:
        basis.append('RVOL_COMPARABLE_SESSIONS=' + str(row['intraday']['same_time_rvol_samples']))
    if id == 'minute.vwap60' and (row.get('metrics') or {}).get('vwap_basis'):
        basis.append('VWAP_BASIS=' + row['metrics']['vwap_basis'])
    for field, code in [('source_published_at', UNKNOWN_SOURCE), ('provider_available_at', UNKNOWN_PROVIDER)]:
        if times.get(field) is None:
            times[field] = receipt
            basis.append(code + ': original receipt is conservative upper bound, not exact source clock')
    quality = _quality(row)
    dependencies = [_time(x) for x in times.get('dependency_available_ats', [])]
    if id == 'us.intraday.relative_qqq_change':
        dependency = row.get('benchmark_dependency') or {}
        original_dependency = _dependency_evidence(dependency)
        dependency_hash = fingerprint(original_dependency)
        times['input_hash'] = fingerprint({'stock_report_input_hash': times['input_hash'],
                                           'benchmark_dependency': original_dependency})
        source_identity = dependency.get('source_ref') or dependency.get('source') or 'SOURCE_IDENTITY_UNKNOWN'
        original_receipt = dependency.get('known_at') or 'SOURCE_RECEIPT_UNKNOWN'
        dependency_ref = ('benchmark_dependency:QQQ; source=' + str(source_identity)
                          + '; known_at=' + str(original_receipt) + '; evidence_sha256=' + dependency_hash)
        times['input_refs'] = [*times['input_refs'], dependency_ref]
        if dependency.get('known_at'):
            dependencies.append(_time(dependency['known_at']))
        other_quality = dependency.get('quality') or {}
        expiry = other_quality.get('valid_until_ms')
        if quality.valid_until_ms is not None and type(expiry) is int:
            if expiry <= quality.evaluated_at_ms:
                quality = replace(quality, state='UNAVAILABLE', observation_ok=False,
                                  confirmation_ok=False, valid_until_ms=None,
                                  reason_codes=(*quality.reason_codes, 'STALE_BENCHMARK'))
            else:
                quality = replace(quality, valid_until_ms=min(quality.valid_until_ms, expiry))
    if value is None:
        quality = replace(quality, state='UNAVAILABLE', observation_ok=False,
                          confirmation_ok=False, valid_until_ms=None,
                          reason_codes=(*quality.reason_codes, 'REPORT_VALUE_MISSING'))
    fields = {name: _time(times.get(name)) for name in (
        'observed_at', 'source_published_at', 'provider_available_at', 'ingested_at',
        'computed_at', 'effective_available_at')}
    fields['effective_available_at'] = max([fields['effective_available_at'], *dependencies])
    return FactorObservation(ref=FactorRef(factor_id=id, version='1.0.0'),
                             instrument_id=row['symbol'], **fields, value=value,
                             missing_reason='REPORT_VALUE_MISSING' if value is None else None,
                             pit_grade='RECONSTRUCTED', source_ref=times['source_ref'],
                             input_hash=times['input_hash'], input_refs=times['input_refs'],
                             quality=quality, availability_basis='; '.join(basis),
                             dependency_available_ats=dependencies)


def _bind(report):
    observations, diagnostics = [], []
    for row, kind, generated in _rows(report):
        for id, section, field in BINDINGS[kind]:
            values = row.get(section) or {} if section else row
            if field not in values:
                continue
            try:
                observation = _wrap(row, kind, id, values[field], generated)
                observations.append(observation)
                for code in (UNKNOWN_SOURCE, UNKNOWN_PROVIDER):
                    if code in observation.availability_basis:
                        diagnostics.append(dict(factor_id=id, instrument_id=row.get('symbol'), code=code))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                code = str(exc)
                if code not in {'INVALID_FACTOR_VALUE', 'SOURCE_PROVENANCE_MISSING', 'SOURCE_QUALITY_MISSING', 'UTC_REQUIRED'}:
                    code = 'INVALID_SOURCE_EVIDENCE'
                diagnostics.append(dict(factor_id=id, instrument_id=row.get('symbol'), code=code))
    return observations, diagnostics


def observations_from_report(report: dict, *, recorded_at: datetime) -> list[FactorObservation]:
    require_utc(recorded_at)
    return _bind(report)[0]


def binding_diagnostics(report: dict) -> list[dict]:
    return _bind(report)[1]


def evidence_card(observation: FactorObservation, *, now: datetime) -> dict:
    """Retain last evidence; availability is separate from factor eligibility."""
    require_utc(now)
    quality = observation.quality
    now_ms = int(now.timestamp()*1000)
    expired = quality.valid_until_ms is not None and now_ms >= quality.valid_until_ms
    reasons = list(quality.reason_codes)
    if expired:
        reasons.append('EXPIRED_QUALITY')
    if observation.effective_available_at > now:
        reasons.append('NOT_YET_AVAILABLE')
    if observation.pit_grade == 'RECONSTRUCTED':
        reasons.append('PIT_EVIDENCE_REQUIRED')
    reasons.append('FACTOR_EFFECTIVENESS_UNVALIDATED')
    unknown_source = UNKNOWN_SOURCE in observation.availability_basis
    unknown_provider = UNKNOWN_PROVIDER in observation.availability_basis
    for flag, code in ((unknown_source, UNKNOWN_SOURCE), (unknown_provider, UNKNOWN_PROVIDER)):
        if flag:
            reasons.append(code)
    builtin = observation.ref.factor_id in {id for entries in BINDINGS.values() for id, _, _ in entries}
    return dict(observation=observation.model_dump(mode='json'),
                age_seconds=max(0, (now-observation.observed_at).total_seconds()),
                receipt_age_seconds=max(0, (now-observation.ingested_at).total_seconds()),
                current_observation=quality.observation_ok and not expired and observation.effective_available_at <= now,
                eligible_for_production=False, block_reasons=list(dict.fromkeys(reasons)),
                actual_computed_at=None if 'COMPUTATION_TIME_UPPER_BOUND' in observation.availability_basis else observation.computed_at.isoformat(),
                actual_source_published_at=None if unknown_source else observation.source_published_at.isoformat(),
                actual_provider_available_at=None if unknown_provider else observation.provider_available_at.isoformat(),
                receipt_bound=observation.source_published_at.isoformat() if unknown_source else
                              observation.provider_available_at.isoformat() if unknown_provider else None,
                CONTRACT_STATUS='READY' if builtin else 'NOT READY',
                DATA_STATUS='READY' if quality.observation_ok and not expired and observation.effective_available_at <= now else 'UNAVAILABLE',
                VALIDATION_STATUS='NEEDS BACKTEST',
                lifecycle_state='LEGACY_UNVALIDATED' if builtin else 'CANDIDATE')
