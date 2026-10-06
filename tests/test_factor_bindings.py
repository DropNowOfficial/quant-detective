"""Bindings preserve report values, source clocks and research-only status."""
from dataclasses import asdict
from datetime import timedelta
import importlib.util


from factors.models import FactorRef
from factors.registry import mathematical_fingerprint
from test_factor_models import T, quality
from market_data.quality import QualityResult

IDS = {
    'us.daily.ma5_slope_1': 0.125,
    'us.intraday.distance_ma5_atr5': -0.075,
    'us.intraday.relative_qqq_change': 0.625,
    'us.intraday.rvol_same_time20': 0.85,
    'us.intraday.vwap_rth_hlc3': 101.125,
    'minute.rvol_prior20': 1.25,
    'minute.vwap60': 202.25,
    'research.daily.volume_ratio20': 0.875,
    'research.daily.adv20': 12345678.25,
}


def binding_module():
    assert importlib.util.find_spec('factors.bindings'), 'source-bound report wrapper is missing'
    from factors import bindings
    return bindings


def source_times():
    return dict(observed_at=(T-timedelta(minutes=10)).isoformat(),
                source_published_at=(T-timedelta(minutes=9)).isoformat(),
                provider_available_at=(T-timedelta(minutes=8)).isoformat(),
                ingested_at=(T-timedelta(minutes=7)).isoformat(),
                computed_at=T.isoformat(), effective_available_at=T.isoformat(),
                source_ref='synthetic original report', input_hash='a'*64,
                input_refs=['synthetic original bars'])


def reports():
    common = dict(quality=asdict(quality()), factor_evidence=source_times())
    us = dict(common, symbol='SYNTH', daily={'ma5_slope_1d': IDS['us.daily.ma5_slope_1']},
              intraday={'d5_atr': IDS['us.intraday.distance_ma5_atr5'],
                        'same_time_rvol': IDS['us.intraday.rvol_same_time20'],
                        'rth_vwap_approx': IDS['us.intraday.vwap_rth_hlc3']},
              relative_change_vs_qqq_pp=IDS['us.intraday.relative_qqq_change'])
    minute = dict(common, symbol='SYNTH', metrics={'rvol20': IDS['minute.rvol_prior20'],
                                                  'vwap60': IDS['minute.vwap60']})
    research = dict(common, symbol='SYNTH', features={'volume_ratio': IDS['research.daily.volume_ratio20'],
                                                      'adv20': IDS['research.daily.adv20']})
    return dict(us_report={'rows': [us]}, minute_report={'rows': [minute]},
                research_report={'rows': [research]})


def test_nine_bindings_equal_existing_report_values():
    binding = binding_module()
    result = binding.observations_from_report(reports(), recorded_at=T+timedelta(hours=1))
    assert {item.ref.factor_id: item.value for item in result} == IDS
    assert all(item.ref.version == '1.0.0' for item in result)
    assert all(item.observed_at == T-timedelta(minutes=10) and item.computed_at == T for item in result)
    assert all(item.ingested_at == T-timedelta(minutes=7) for item in result)


def test_recorded_at_cannot_replace_input_times_or_hash():
    binding = binding_module()
    first = binding.observations_from_report(reports(), recorded_at=T)
    second = binding.observations_from_report(reports(), recorded_at=T+timedelta(days=2))
    assert first == second
    from factors import registry
    assert [mathematical_fingerprint(registry.get(item.ref), item) for item in first] == [
        mathematical_fingerprint(registry.get(item.ref), item) for item in second]


def test_three_rvol_definitions_are_distinct():
    binding = binding_module()
    from factors import registry
    definitions = [registry.get(FactorRef(factor_id=id, version='1.0.0')) for id in
                   ('us.intraday.rvol_same_time20', 'minute.rvol_prior20', 'research.daily.volume_ratio20')]
    assert len({item.ref.factor_id for item in definitions}) == 3
    assert len({item.formula_text for item in definitions}) == 3
    assert len({str(item.window) for item in definitions}) == 3


def test_unimplemented_components_have_no_fake_values():
    binding = binding_module()
    from factors import registry
    locked = [item for item in registry.definitions() if item.calculator_key is None]
    assert len(locked) == 7
    assert {item.name for item in locked} == {
        'Amihud', 'Residual Volatility', 'Residual Momentum', 'Surprise Discrepancy',
        'Capital Allocation Intent', 'Effective Factor Dimension', 'Exposure Graph Multiplex'}
    report = reports()
    report['components'] = {item.ref.factor_id: 99 for item in locked}
    actual = binding.observations_from_report(report, recorded_at=T)
    assert not {item.ref for item in locked} & {item.ref for item in actual}
    assert binding.observations_from_report({}, recorded_at=T) == []
    only_us = binding.observations_from_report(report['us_report'], recorded_at=T)
    assert len(only_us) == 5


def test_stale_factor_card_keeps_last_value_with_age_and_block_reason():
    binding = binding_module()
    observation = binding.observations_from_report(reports(), recorded_at=T)[0]
    card = binding.evidence_card(observation, now=T+timedelta(minutes=5))
    assert card['observation']['value'] == observation.value
    assert card['age_seconds'] == 900
    assert card['current_observation'] is False
    assert 'EXPIRED_QUALITY' in card['block_reasons']
    assert card['lifecycle_state'] == 'LEGACY_UNVALIDATED'
    assert card['VALIDATION_STATUS'] == 'NEEDS BACKTEST'


def test_missing_or_ambiguous_source_times_are_not_invented():
    binding = binding_module()
    report = reports()['us_report']
    del report['rows'][0]['factor_evidence']['source_published_at']
    del report['rows'][0]['factor_evidence']['provider_available_at']
    result = binding.observations_from_report(report, recorded_at=T)
    assert result and all(item.pit_grade == 'RECONSTRUCTED' for item in result)
    assert all('SOURCE_PUBLICATION_TIME_UNKNOWN' in item.availability_basis for item in result)
    assert result[0].source_published_at == T-timedelta(minutes=7)
    assert 'SOURCE_PUBLICATION_TIME_UNKNOWN' in str(binding.binding_diagnostics(report))
    native = dict(symbol='SYNTH', daily={'ma5_slope_1d': 1.0}, known_at=T.isoformat(),
                  quality=asdict(quality()), quality_evidence={'captured_at_ms': int(T.timestamp()*1000)})
    assert binding.observations_from_report({'rows': [native]}, recorded_at=T) == []


def test_binding_preserves_missing_and_zero_values():
    binding = binding_module()
    report = reports()['minute_report']
    row = report['rows'][0]
    row['metrics']['rvol20'] = 0.0
    row['metrics']['vwap60'] = None
    result = {item.ref.factor_id: item for item in binding.observations_from_report(report, recorded_at=T)}
    assert result['minute.rvol_prior20'].value == 0.0
    assert result['minute.vwap60'].value is None
    assert result['minute.vwap60'].missing_reason == 'REPORT_VALUE_MISSING'
    assert not result['minute.vwap60'].quality.confirmation_ok


def test_dependency_expiry_and_original_times_are_preserved():
    binding = binding_module()
    report = reports()['us_report']
    row = report['rows'][0]
    row['benchmark_dependency'] = dict(known_at=T.isoformat(), quality=asdict(quality(
        valid_until_ms=int((T+timedelta(seconds=10)).timestamp()*1000))))
    actual = {item.ref.factor_id: item for item in binding.observations_from_report(report, recorded_at=T)}
    relative = actual['us.intraday.relative_qqq_change']
    assert relative.dependency_available_ats == [T]
    assert relative.quality.valid_until_ms == int((T+timedelta(seconds=10)).timestamp()*1000)
    assert actual['us.intraday.distance_ma5_atr5'].quality.valid_until_ms > relative.quality.valid_until_ms


def test_invalid_or_nonfinite_report_never_becomes_numeric_evidence():
    binding = binding_module()
    report = reports()['minute_report']
    report['rows'][0]['metrics']['rvol20'] = True
    report['rows'][0]['metrics']['vwap60'] = float('nan')
    assert binding.observations_from_report(report, recorded_at=T) == []
    assert 'INVALID_FACTOR_VALUE' in str(binding.binding_diagnostics(report))


def test_native_receipt_bound_value_is_exploratory_and_original_capture_survives():
    binding = binding_module()
    row = dict(symbol='SYNTH', source='synthetic source', known_at=T.isoformat(),
               received_at_utc=T.isoformat(), signal_time_ms=int((T-timedelta(minutes=1)).timestamp()*1000),
               metrics={'rvol20': 1.2}, quality=asdict(quality()),
               quality_evidence={'captured_at_ms': int(T.timestamp()*1000), 'bars': []})
    item = binding.observations_from_report({'rows':[row]}, recorded_at=T+timedelta(days=2))[0]
    assert item.value == row['metrics']['rvol20']
    assert item.source_published_at == item.provider_available_at == item.ingested_at == T
    assert item.pit_grade == 'RECONSTRUCTED'
    card = binding.evidence_card(item, now=T)
    assert card['actual_source_published_at'] is None
    assert card['actual_provider_available_at'] is None
    assert card['receipt_bound'] == T.isoformat()
    assert 'PIT_EVIDENCE_REQUIRED' in card['block_reasons']
    assert not card['eligible_for_production']


def test_absent_research_receipt_provenance_has_no_observations():
    binding = binding_module()
    report={'research_report': {'rows': [dict(symbol='SYNTH', features={'adv20':123.0,'volume_ratio':1.1})]}}
    assert binding.observations_from_report(report, recorded_at=T) == []
    assert 'SOURCE_PROVENANCE_MISSING' in str(binding.binding_diagnostics(report))


def test_native_us_binding_preserves_daily_intraday_history_receipts():
    binding = binding_module()
    from datetime import UTC, datetime
    now=datetime(2026,10,6,14,11,tzinfo=UTC)
    capture=int(now.timestamp()*1000)
    daily_capture=capture-86400000
    history_capture=capture-18000000
    row=dict(symbol='SYNTH',source='synthetic native US report',known_at=now.isoformat(),
             daily={'ma5_slope_1d':.2,'previous_session_date':'2026-10-05'},
             intraday={'d5_atr':.1,'same_time_rvol':.9,'rth_vwap_approx':102.0,
                       'current_bar_time_utc':'2026-10-06T14:10:00Z'},
             relative_change_vs_qqq_pp=.7,
             quality=asdict(quality(evaluated_at_ms=capture,valid_until_ms=capture+60000,
                                   last_bar_end_ms=capture-60000)),
             quality_evidence={'captured_at_ms':capture,'daily':{'captured_at_ms':daily_capture},
                               'history':{'captured_at_ms':history_capture}})
    # Daily receipt must be after that original daily session's close.
    row['quality_evidence']['daily']['captured_at_ms']=int(datetime(2026,10,5,21,tzinfo=UTC).timestamp()*1000)
    result=binding.observations_from_report({'generated_at_utc':now.isoformat(),'rows':[row]},recorded_at=now+timedelta(days=2))
    assert len(result)==5
    daily=next(item for item in result if item.ref.factor_id=='us.daily.ma5_slope_1')
    assert daily.observed_at==datetime(2026,10,5,20,tzinfo=UTC)
    assert daily.ingested_at==datetime(2026,10,5,21,tzinfo=UTC)
    rvol=next(item for item in result if item.ref.factor_id=='us.intraday.rvol_same_time20')
    assert rvol.dependency_available_ats==[now-timedelta(hours=5)]
    assert all(item.pit_grade=='RECONSTRUCTED' for item in result)


def test_existing_minute_analyzer_values_are_wrapped_without_recalculation():
    binding = binding_module()
    from test_live_features import run, NOW
    from datetime import UTC, datetime
    original=run()
    assert original['metrics']['vwap_basis']=='quote/base'
    wrapped=binding.observations_from_report({'rows':[original]}, recorded_at=datetime.fromtimestamp(NOW/1000,UTC))
    assert {item.ref.factor_id:item.value for item in wrapped}=={
        'minute.rvol_prior20':original['metrics']['rvol20'],
        'minute.vwap60':original['metrics']['vwap60']}
    assert all(item.quality == QualityResult(**{**original['quality'],'reason_codes':tuple(original['quality']['reason_codes'])}) for item in wrapped)


def test_native_minute_vwap_basis_remains_visible_in_evidence():
    binding=binding_module()
    from test_live_features import run
    original=run()
    observation=next(item for item in binding.observations_from_report({'rows':[original]},recorded_at=T)
                     if item.ref.factor_id=='minute.vwap60')
    assert 'VWAP_BASIS=quote/base' in observation.availability_basis


def test_same_time_rvol_preserves_distinct_session_sample_evidence():
    binding=binding_module()
    report=reports()['us_report']
    report['rows'][0]['intraday']['same_time_rvol_samples']=20
    observation=next(item for item in binding.observations_from_report(report,recorded_at=T)
                     if item.ref.factor_id=='us.intraday.rvol_same_time20')
    assert 'RVOL_COMPARABLE_SESSIONS=20' in observation.availability_basis
