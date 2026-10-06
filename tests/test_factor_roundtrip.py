"""Synthetic imported-factor acceptance and installed-wheel resource checks.

All data lives in temporary files/SQLite. No provider, notification or trade is
invoked. The page-resource check is HTTP evidence, not rendered-browser evidence.
"""
from contextlib import closing
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import urlencode

import pytest

from factors.bindings import evidence_card
from factors.importer import InstrumentUniverse, commit_import, discard_store_previews, preview_import
from factors.lifecycle import LifecycleEvent, lifecycle_state, local_operation, production_manifest, record_transition
from factors.models import FactorObservation, FactorRef
from factors.registry import mathematical_fingerprint
from factors.store import FactorStore
from factors.trials import record_trial, trial_evidence
from test_factor_import import MAPPING, csv_bytes, metadata
from test_factor_models import T
from test_factor_routes import request, running
from test_factor_trials import trial

UNIVERSE = InstrumentUniverse('synthetic-v1', {'SYNTH': 'us_equity', 'SYNTH2': 'us_equity'})
REF = FactorRef(factor_id='external_value', version='1')


def import_file(store, directory, *, name='source', data=None, document=None, now=T):
    """Use the public B2 byte/document boundary with actual temporary files."""
    csv_path = directory / (name + '.csv')
    json_path = directory / (name + '.json')
    csv_path.write_bytes(data if data is not None else csv_bytes())
    json_path.write_text(json.dumps(document or metadata()), encoding='utf-8')
    preview = preview_import(csv_path.read_bytes(), json.loads(json_path.read_text(encoding='utf-8')),
                             MAPPING, store=store, now=now, universe=UNIVERSE)
    assert preview.errors == []
    result = commit_import(preview.preview_id, request_id=name, store=store, now=now)
    return preview, result


def transition(store, target, *, event_id, ref=REF, now=T):
    event = LifecycleEvent(event_id=event_id, factor_ref=ref,
        from_state=lifecycle_state(ref, store=store), to_state=target, at=now,
        actor='synthetic local reviewer', reason='Explicit synthetic local review',
        evidence_ids=[], expected_revision=store.revision())
    before = store.revision()
    with local_operation(store, actor_label=event.actor, event=event, now=now,
                         approval_id='approval-' + event_id,
                         approval_reason='Explicit synthetic approval') as context:
        assert store.revision() == before  # Staging is not a store write.
        result = record_transition(event, store=store, approvals={'context': context})
    return event, result


def archived_bytes(path):
    """Read original evidence bytes/hashes independently of model serialization."""
    with closing(sqlite3.connect(path)) as connection:
        return {table: tuple(connection.execute(sql).fetchall()) for table, sql in {
            'definitions': 'SELECT factor_id,version,CAST(payload AS BLOB),audit_hash FROM definitions ORDER BY factor_id,version',
            'datasets': 'SELECT dataset_id,version,CAST(payload AS BLOB),content_hash,audit_hash FROM datasets ORDER BY dataset_id,version',
            'observations': 'SELECT dataset_id,dataset_version,CAST(payload AS BLOB),audit_hash FROM observations ORDER BY dataset_id,dataset_version,instrument_id',
            'trials': 'SELECT run_id,CAST(payload AS BLOB),audit_hash FROM trials ORDER BY run_id',
            'events': 'SELECT event_id,CAST(payload AS BLOB),audit_hash FROM lifecycle_events ORDER BY event_id',
            'checks': 'SELECT check_id,CAST(payload AS BLOB),audit_hash FROM lifecycle_checks ORDER BY check_id',
            'approvals': 'SELECT approval_id,CAST(payload AS BLOB),audit_hash FROM lifecycle_approvals ORDER BY approval_id',
        }.items()}


def read_page_evidence(directory):
    """Exercise real HTTP catalog/cards/history using the retained DB read-only."""
    query = '?factor_id=external_value&version=1'
    with running(directory) as app:
        responses = {}
        for key, path in {
            'catalog': '/api/factors',
            'cards': '/api/factors/observations' + query + '&' + urlencode({'as_of': T.isoformat()}),
            'lifecycle': '/api/factors/lifecycle' + query + '&' + urlencode({'as_of': (T + timedelta(minutes=1)).isoformat()}),
            'trials': '/api/factors/trials' + query,
        }.items():
            status, body = request(app, path)
            assert status == 200
            responses[key] = json.loads(body)
        # Current HTTP response time does not affect synthetic age assertions.
        for card in responses['cards']['cards']:
            card.pop('age_seconds')
            card.pop('receipt_age_seconds')
        return responses


def test_roundtrip_import_quality_catalog_governance(tmp_path):
    path = tmp_path / 'factors.sqlite'
    future = T + timedelta(days=1)
    with FactorStore(path) as store:
        production_before = production_manifest(store)
        preview, committed = import_file(store, tmp_path, data=csv_bytes(
            {}, {'instrument_id': 'SYNTH2', 'value': '0', 'effective_available_at': future.isoformat()}))
        assert preview.expected_revision == 0 and committed.store_revision == 1
        assert {'PIT_EVIDENCE_REQUIRED', 'EXTERNAL_VALUES_UNVERIFIED'} <= set(preview.warnings)
        manifest = store.dataset_manifest(committed.dataset_id, committed.version)
        assert manifest.source_ref == 'synthetic-source'
        assert manifest.file_sha256 == hashlib.sha256((tmp_path / 'source.csv').read_bytes()).hexdigest()
        prior_observations = store.observations(REF, as_of=T, mode='exploratory')
        assert [(row.instrument_id, row.value) for row in prior_observations] == [('SYNTH', 10.0)]
        later_observations = store.observations(REF, as_of=future, mode='exploratory')
        assert [(row.instrument_id, row.value) for row in later_observations] == [('SYNTH', 10.0), ('SYNTH2', 0.0)]
        assert store.observations(REF, as_of=future, mode='live') == []
        with pytest.raises(ValueError, match='^PIT_EVIDENCE_REQUIRED$'):
            store.observations(REF, as_of=T, mode='strict_replay')
        card = evidence_card(prior_observations[0], now=T)
        assert card['observation']['source_ref'] == manifest.source_ref
        assert card['observation']['pit_grade'] == 'RECONSTRUCTED'
        assert card['observation']['quality']['state'] == 'UNAVAILABLE'
        assert not card['current_observation'] and not card['eligible_for_production']
        assert card['VALIDATION_STATUS'] == 'NEEDS BACKTEST'
        assert 'PIT_EVIDENCE_REQUIRED' in card['block_reasons']
        failed = trial(status='FAILED', error_reason='Synthetic preprocessing failure',
                       input_hashes={'source': prior_observations[0].input_hash})
        assert record_trial(failed, store=store) == failed.run_id
        evidence = trial_evidence(failed.run_id, store=store, required_kind='strategy')
        assert not evidence['promotable']
        assert {'TRIAL_NOT_SUCCEEDED', 'REPORT_KIND_MISMATCH', 'EVIDENCE_VALIDATOR_NOT_CONFIGURED'} <= set(evidence['limitations'])
        _, blocked = transition(store, 'approved_active', event_id='blocked-promotion')
        assert not blocked.accepted and blocked.error_code == 'EVIDENCE_VALIDATOR_NOT_CONFIGURED'
        assert blocked.state == 'candidate' and store.lifecycle_history(REF) == []
        _, not_ready = transition(store, 'data_ready', event_id='blocked-readiness')
        assert not not_ready.accepted and not_ready.error_code == 'CURRENT_DATA_CHECK_FAILED'
        assert store.revision() == 2 and store.lifecycle_history(REF) == []
        event, accepted = transition(store, 'quarantine', event_id='accepted-quarantine', now=T + timedelta(minutes=1))
        assert accepted.accepted and accepted.revision == 3
        prior_events = store.lifecycle_history(REF)
        prior_checks = store.lifecycle_check_artifacts(REF)
        prior_approval = store.lifecycle_approval(event.event_id)
        assert prior_events == [event] and len(prior_checks) == 1 and prior_approval is not None
        assert production_manifest(store) == production_before
    discard_store_previews(path)
    previous_evidence_bytes = archived_bytes(path)
    prior_page = read_page_evidence(tmp_path)
    external = next(item for item in prior_page['catalog']['factors'] if item['definition']['ref'] == REF.model_dump())
    assert external['binding_status'] == 'EXTERNAL_VALUE'
    assert external['lifecycle_state'] == 'quarantine' and not external['eligible_for_production']
    assert len(prior_page['cards']['cards']) == 1
    assert prior_page['cards']['cards'][0]['lifecycle_state'] == 'quarantine'
    assert prior_page['trials']['trials'][0]['record']['status'] == 'FAILED'
    with FactorStore(path) as reopened:
        reopened_observations = reopened.observations(REF, as_of=T, mode='exploratory')
        assert reopened_observations == prior_observations
        assert reopened.observations(REF, as_of=future, mode='exploratory') == later_observations
        assert reopened.dataset_manifest(committed.dataset_id, committed.version) == manifest
        assert reopened.trial(failed.run_id) == failed
        assert reopened.lifecycle_history(REF) == prior_events
        assert reopened.lifecycle_check_artifacts(REF) == prior_checks
        assert reopened.lifecycle_approval(event.event_id) == prior_approval
        assert lifecycle_state(REF, store=reopened, as_of=T) == 'candidate'
        assert lifecycle_state(REF, store=reopened) == 'quarantine'
        assert production_manifest(reopened) == production_before and reopened.revision() == 3
    assert read_page_evidence(tmp_path) == prior_page
    assert previous_evidence_bytes == archived_bytes(path)


def test_same_snapshot_same_definition_same_output(tmp_path):
    path = tmp_path / 'factors.sqlite'
    with FactorStore(path) as store:
        original, committed = import_file(store, tmp_path)
        definition = store.resolve_definition(REF)
        row = store.observations(REF, as_of=T, mode='exploratory')[0]
        original_content_hash = mathematical_fingerprint(definition, row)
        previous_evidence_bytes = archived_bytes(path)
    discard_store_previews(path)
    with FactorStore(path) as reopened:
        repeated, replay = import_file(reopened, tmp_path, name='retry', now=T + timedelta(minutes=1),
            data=csv_bytes({'value': '10.0'}, newline='\r\n'),
            document=metadata(dataset_id='different-operational-id', version=8))
        assert replay.replayed and (replay.dataset_id, replay.version) == (committed.dataset_id, committed.version)
        assert repeated.content_hash == original.content_hash
        assert repeated.proposed_manifest.file_sha256 != original.proposed_manifest.file_sha256
        recomputed = FactorObservation.model_validate_json(json.dumps(repeated.sample_rows[0]))
        assert recomputed.computed_at != row.computed_at
        assert mathematical_fingerprint(definition, recomputed) == original_content_hash
        retained = reopened.observations(REF, as_of=T, mode='exploratory')[0]
        rerun_content_hash = mathematical_fingerprint(reopened.resolve_definition(REF), retained)
        assert rerun_content_hash == original_content_hash
        assert retained == row and reopened.revision() == 1
        assert reopened.dataset_manifest(replay.dataset_id, replay.version) == original.proposed_manifest
    discard_store_previews(path)
    assert previous_evidence_bytes == archived_bytes(path)


def test_formula_or_snapshot_change_changes_fingerprint(tmp_path):
    path = tmp_path / 'factors.sqlite'
    with FactorStore(path) as store:
        original, committed = import_file(store, tmp_path)
        row = store.observations(REF, as_of=T, mode='exploratory')[0]
        original_definition = store.resolve_definition(REF)
        original_content_hash = mathematical_fingerprint(original_definition, row)
        formula_only = original_definition.model_copy(update={'formula_text': 'Different descriptive formula'})
        assert mathematical_fingerprint(formula_only, row) != original_content_hash
        record_trial(trial(status='FAILED', error_reason='Original negative result'), store=store)
        transition(store, 'quarantine', event_id='old-decision')
        previous_evidence_bytes = archived_bytes(path)
        changed_document = metadata(version=2)
        changed_document['definition']['ref']['version'] = '2'
        changed_document['definition']['formula_text'] = 'Changed external description; never executed'
        formula, _ = import_file(store, tmp_path, name='new-formula', document=changed_document,
                                 data=csv_bytes({'factor_version': '2'}))
        changed_ref = FactorRef(factor_id='external_value', version='2')
        changed_row = store.observations(changed_ref, as_of=T, mode='exploratory')[0]
        changed_formula_hash = mathematical_fingerprint(store.resolve_definition(changed_ref), changed_row)
        assert changed_formula_hash != original_content_hash
        assert formula.content_hash != original.content_hash
        later = T + timedelta(seconds=1)
        snapshot, _ = import_file(store, tmp_path, name='new-snapshot', now=later, document=metadata(version=3),
            data=csv_bytes({'source_published_at': later.isoformat(), 'provider_available_at': later.isoformat()}))
        changed_snapshot = store.observations(REF, as_of=later, mode='exploratory')[0]
        assert changed_snapshot.value == row.value  # Identity changes even if the supplied value does not.
        assert mathematical_fingerprint(store.resolve_definition(REF), changed_snapshot) != original_content_hash
        assert snapshot.content_hash != original.content_hash and changed_snapshot.input_hash != row.input_hash
        assert store.observations(REF, as_of=T, mode='exploratory') == [row]
        assert lifecycle_state(changed_ref, store=store) == 'candidate'
        assert lifecycle_state(REF, store=store) == 'quarantine'
        assert store.trial('run-1').status == 'FAILED'
    discard_store_previews(path)
    archived_evidence_bytes = archived_bytes(path)
    for table, retained in previous_evidence_bytes.items():
        assert all(record in archived_evidence_bytes[table] for record in retained)
    with FactorStore(path, read_only=True) as reopened:
        assert reopened.observations(REF, as_of=T, mode='exploratory') == [row]
        assert reopened.dataset_manifest(committed.dataset_id, committed.version) == original.proposed_manifest
        assert reopened.trial('run-1').error_reason == 'Original negative result'
        assert reopened.lifecycle_history(REF)[0].event_id == 'old-decision'
    assert archived_bytes(path) == archived_evidence_bytes


def test_installed_wheel_registry_and_factor_page(tmp_path):
    """No checkout imports: install the real wheel and exercise catalog + HTTP."""
    root = Path(__file__).resolve().parents[1]
    wheels = tmp_path / 'wheels'
    installed = tmp_path / 'installed'
    environment = {**os.environ, 'UV_CACHE_DIR': '/tmp/qd-factor-uv-cache', 'UV_LINK_MODE': 'copy'}
    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheels)], cwd=root,
                   env=environment, check=True, capture_output=True, text=True)
    wheel, = wheels.glob('*.whl')
    subprocess.run(['uv', 'pip', 'install', '--no-deps', '--target', str(installed), str(wheel)],
                   cwd=tmp_path, env=environment, check=True, capture_output=True, text=True)
    expected_hash = hashlib.sha256((root / 'contracts/factors/definitions_v1.json').read_bytes()).hexdigest()
    script = r'''
from contextlib import closing
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from threading import Thread
# Editable-install .pth files may add the checkout even with Python -I.
# Remove that exact source root before loading any project module.
sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != Path(sys.argv[3]).resolve()]
sys.path.insert(0, sys.argv[1])
import factors
from factors import registry
from market_data import server
installed = Path(sys.argv[1]).resolve()
assert Path(factors.__file__).resolve().is_relative_to(installed)
assert Path(server.__file__).resolve().is_relative_to(installed)
assert str(Path(sys.argv[3]).resolve()) not in sys.path
resource = Path(registry.__file__).with_name('definitions_v1.json')
assert hashlib.sha256(resource.read_bytes()).hexdigest() == sys.argv[2]
definitions = registry.definitions()
assert len(definitions) == 16
assert sum(item.calculator_key is not None for item in definitions) == 9
for definition in definitions:
    assert registry.get(definition.ref) == definition
assert Path(server.__file__).with_name('factors.html').is_file()
def no_provider(*args, **kwargs):
    raise AssertionError('Packaged resource check must not contact a provider')
app = server.make_server(0, no_provider)
worker = Thread(target=app.serve_forever, daemon=True)
worker.start()
try:
    for path in ('/factors', '/api/factors'):
        with closing(HTTPConnection('127.0.0.1', app.server_port, timeout=3)) as connection:
            connection.request('GET', path)
            response = connection.getresponse()
            assert response.status == 200
            body = response.read()
            if path == '/factors':
                assert b'id="factor-select"' in body and b'/api/factors' in body
            else:
                payload = json.loads(body)
                assert len(payload['factors']) == 16 and not payload['import_enabled']
finally:
    app.shutdown()
    app.server_close()
    worker.join()
print('INSTALLED_WHEEL_OK: 16 definitions/get, canonical resource hash, /factors HTML, /api/factors; no checkout imports')
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(installed), expected_hash, str(root)],
                            cwd=tmp_path, env=environment, check=True, capture_output=True, text=True)
    assert 'INSTALLED_WHEEL_OK' in result.stdout
    print(result.stdout.strip())
