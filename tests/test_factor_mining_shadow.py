from datetime import datetime,timedelta,timezone
import importlib,json
from pathlib import Path
import pytest
from research_lab.factor_mining.contracts import Evidence,FactorObservation,blocked_hypothesis


def api():
    assert (Path(__file__).parents[1]/'research_lab/factor_mining/shadow.py').exists(), 'shadow output interface missing'
    return importlib.import_module('research_lab.factor_mining.shadow')


def observation():
    t=datetime(2026,1,1,21,tzinfo=timezone.utc)
    e=Evidence(t,t+timedelta(seconds=1),t+timedelta(seconds=1),'synthetic://returns','v1','synthetic-generated','SYNTHETIC_FIXTURE')
    return FactorObservation('custom.demo','ACME','dimensionless',1.,'VALID',None,t+timedelta(minutes=1),(e,))


def test_shadow_output_retains_lineage_without_claiming_signal_readiness():
    o=observation();out=api().shadow_snapshot([o],generated_at=o.decision_time)
    assert out['mode']=='research_shadow' and out['schema_version']==1
    assert out['official_signal_eligible'] is False and out['operational_freshness_verified'] is False
    row=out['observations'][0]
    assert row['value']==1. and row['validation_status']=='NEEDS BACKTEST'
    assert row['evidence'][0]['license_id']=='synthetic-generated'
    assert row['evidence'][0]['available_at']==o.evidence[0].available_at.isoformat()
    assert json.loads(json.dumps(out,allow_nan=False))==out


def test_unavailable_stays_json_null_and_reasoned():
    o=observation();missing=blocked_hypothesis('analyst_surprise',entity='ACME',decision_time=o.decision_time)
    out=api().shadow_snapshot([missing],generated_at=o.decision_time)
    assert out['observations'][0]['value'] is None
    assert out['observations'][0]['reason']=='BLOCKED_MISSING_PIT_CONSENSUS'


def test_shadow_cannot_backdate_generation_or_duplicate_observations():
    o=observation()
    with pytest.raises(ValueError): api().shadow_snapshot([o],generated_at=o.decision_time-timedelta(seconds=1))
    with pytest.raises(ValueError): api().shadow_snapshot([o,o],generated_at=o.decision_time)
    with pytest.raises(ValueError): api().shadow_snapshot([{'value':1}],generated_at=o.decision_time)
