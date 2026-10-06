"""Synthetic loopback HTTP tests; external fetches are forbidden."""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
from threading import Thread

import pytest

from factors.store import FactorStore
from market_data.server import make_server


@contextmanager
def running(tmp_path, **kwargs):
    def no_network(*args, **kwargs):
        raise AssertionError("factor routes must not fetch")
    app = make_server(0, no_network, factor_store_path=tmp_path / "factors.sqlite", **kwargs)
    worker = Thread(target=app.serve_forever, daemon=True)
    worker.start()
    try:
        yield app
    finally:
        app.shutdown()
        app.server_close()
        worker.join()


def request(app, path, method="GET", payload=None, headers=None, raw=None):
    connection = HTTPConnection("127.0.0.1", app.server_port, timeout=3)
    body = raw if raw is not None else json.dumps(payload or {}).encode()
    supplied = {"Host": f"127.0.0.1:{app.server_port}"}
    if method == "POST":
        supplied.update({"Origin": f"http://127.0.0.1:{app.server_port}",
                         "Content-Type": "application/json"})
    supplied.update(headers or {})
    supplied = {key: value for key, value in supplied.items() if value is not None}
    try:
        connection.request(method, path, body=body if method != "GET" else None, headers=supplied)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def token(app):
    status, body = request(app, "/api/factors/session")
    assert status == 200
    return json.loads(body)["csrf_token"]


def test_orders_post_stays_405(tmp_path):
    with running(tmp_path, enable_factor_import=True) as app:
        assert request(app, "/api/orders", "POST")[0] == 405
        for method in ("PUT", "PATCH", "DELETE"):
            assert request(app, "/api/factors/import/preview", method)[0] == 405


def test_import_requires_explicit_flag_and_does_not_open_store(tmp_path):
    with running(tmp_path) as app:
        assert request(app, "/api/factors/import/preview", "POST")[0] == 405
        assert request(app, "/api/factors/import/commit", "POST")[0] == 405
        assert request(app, "/api/factors/session")[0] == 404
    assert not (tmp_path / "factors.sqlite").exists()


@pytest.mark.parametrize("headers", [
    {"Origin": None}, {"Origin": "http://foreign.example"},
    {"Origin": "https://127.0.0.1:{port}"}, {"Origin": "http://localhost:{port}"},
    {"Host": "foreign.example"}, {"Host": "127.0.0.1:{port}.foreign.example"},
    {"Origin": "null"}, {"Origin": "http://127.0.0.1:{port}/"},
    {"X-Factor-CSRF": "wrong"}, {"X-Factor-CSRF": None},
])
def test_import_security_boundary(tmp_path, headers):
    with running(tmp_path, enable_factor_import=True) as app:
        session_token = token(app)
        actual = {"X-Factor-CSRF": session_token}
        actual.update({key: value.format(port=app.server_port) if value else value
                       for key, value in headers.items()})
        status, body = request(app, "/api/factors/import/preview", "POST", headers=actual)
        assert status == 403
        assert session_token.encode() not in body
        with FactorStore(tmp_path / "factors.sqlite") as store:
            assert store.revision() == 0


@pytest.mark.parametrize("payload", [
    {"path": "../../etc/passwd"}, {"url": "https://example.test/data.csv"},
    {"script": "print('not executed')"}, {"executable": "anything"},
])
def test_import_rejects_file_url_and_script_fields(tmp_path, payload):
    with running(tmp_path, enable_factor_import=True) as app:
        status, _ = request(app, "/api/factors/import/preview", "POST", payload,
                            {"X-Factor-CSRF": token(app)})
        assert status == 400


def test_import_json_body_and_size_limits(tmp_path):
    with running(tmp_path, enable_factor_import=True) as app:
        headers = {"X-Factor-CSRF": token(app)}
        assert request(app, "/api/factors/import/preview", "POST", headers={
            **headers, "Content-Type": "text/plain"})[0] == 415
        assert request(app, "/api/factors/import/preview", "POST", headers=headers,
                       raw=b'{"x":NaN}')[0] == 400
        assert request(app, "/api/factors/import/preview", "POST", headers=headers,
                       raw=b'{"x":1,"x":2}')[0] == 400
        assert request(app, "/api/factors/import/preview", "POST", raw=b"", headers={**headers, "Content-Length": str(5 * 1024 * 1024 + 1)})[0] == 413


def test_import_rejects_query_and_token_never_logged(tmp_path, capsys):
    with running(tmp_path, enable_factor_import=True) as app:
        session_token = token(app)
        status, body = request(app, f"/api/factors/import/preview?token={session_token}", "POST",
                               headers={"X-Factor-CSRF": session_token})
        assert status == 400
        assert session_token.encode() not in body
    captured = capsys.readouterr()
    assert session_token not in captured.err + captured.out


def test_session_token_changes_between_process_servers(tmp_path):
    with running(tmp_path, enable_factor_import=True) as app:
        first = token(app)
    with running(tmp_path, enable_factor_import=True) as app:
        assert token(app) != first


def valid_payload(universe_version):
    from test_factor_import import csv_bytes, metadata, MAPPING
    return {"csv_text": csv_bytes().decode(), "definition_json": metadata(universe_version=universe_version),
            "mapping": MAPPING}


def test_http_preview_confirm_replay_and_original_manifest(tmp_path):
    from factors.importer import InstrumentUniverse
    from test_factor_import import csv_bytes
    universe = InstrumentUniverse("synthetic-v1", {"SYNTH": "us_equity"})
    with running(tmp_path, enable_factor_import=True, factor_universe=universe) as app:
        headers = {"X-Factor-CSRF": token(app)}
        status, body = request(app, "/api/factors/import/preview", "POST", valid_payload(universe.version), headers)
        first = json.loads(body)
        assert status == 200 and first["ok"] and first["expected_revision"] == 0
        assert first["sample_rows"][0]["pit_grade"] == "RECONSTRUCTED"
        with FactorStore(tmp_path / "factors.sqlite") as store:
            assert store.revision() == 0 and store.definitions() == []
        confirm = {"preview_id": first["preview_id"], "request_id": "click-one"}
        status, body = request(app, "/api/factors/import/commit", "POST", confirm, headers)
        committed = json.loads(body)
        assert status == 200 and committed["result"]["store_revision"] == 1
        status, body = request(app, "/api/factors/import/commit", "POST", confirm, headers)
        assert status == 200 and json.loads(body)["result"]["replayed"]
        repeat = valid_payload(universe.version)
        repeat["csv_text"] = csv_bytes({"value": "10.0"}, newline="\r\n").decode()
        status, body = request(app, "/api/factors/import/preview", "POST", repeat, headers)
        second = json.loads(body)
        assert status == 200 and second["content_hash"] == first["content_hash"]
        assert second["proposed_manifest"]["file_sha256"] != first["proposed_manifest"]["file_sha256"]
        status, body = request(app, "/api/factors/import/commit", "POST", {
            "preview_id": second["preview_id"], "request_id": "click-two"}, headers)
        assert status == 200
        assert json.loads(body)["manifest"] == committed["manifest"]


def test_http_revision_conflict_is_409_and_no_partial_catalog_save(tmp_path):
    from factors.importer import InstrumentUniverse
    from test_factor_models import definition, T
    universe = InstrumentUniverse("synthetic-v1", {"SYNTH": "us_equity"})
    with running(tmp_path, enable_factor_import=True, factor_universe=universe) as app:
        headers = {"X-Factor-CSRF": token(app)}
        status, body = request(app, "/api/factors/import/preview", "POST", valid_payload(universe.version), headers)
        preview_id = json.loads(body)["preview_id"]
        with FactorStore(tmp_path / "factors.sqlite") as store:
            other = definition(ref=definition().ref.model_copy(update={"factor_id": "other"}))
            store.register_definition(other, expected_revision=0, request_id="other")
        status, body = request(app, "/api/factors/import/commit", "POST", {
            "preview_id": preview_id, "request_id": "stale-confirm"}, headers)
        assert status == 409 and json.loads(body)["error"] == "REVISION_CONFLICT"
        with FactorStore(tmp_path / "factors.sqlite") as store:
            assert store.revision() == 1 and [item.ref.factor_id for item in store.definitions()] == ["other"]
            assert store.dataset_manifest("synthetic-import", 1) is None


@pytest.mark.parametrize("field", ["path", "url", "script", "quality", "pit_grade", "ingested_at"])
def test_http_commit_cannot_change_preview_content(tmp_path, field):
    with running(tmp_path, enable_factor_import=True) as app:
        status, _ = request(app, "/api/factors/import/commit", "POST", {
            "preview_id": "a" * 32, "request_id": "confirmation", field: "untrusted"},
            {"X-Factor-CSRF": token(app)})
        assert status == 400


def test_duplicate_headers_transfer_encoding_and_invalid_utf8_are_rejected(tmp_path):
    with running(tmp_path, enable_factor_import=True) as app:
        headers = {"X-Factor-CSRF": token(app)}
        assert request(app, "/api/factors/import/preview", "POST", headers={
            **headers, "Transfer-Encoding": "chunked"})[0] == 411
        assert request(app, "/api/factors/import/preview", "POST", headers=headers, raw=b"\xff")[0] == 400
        connection = HTTPConnection("127.0.0.1", app.server_port, timeout=3)
        try:
            connection.putrequest("POST", "/api/factors/import/preview")
            connection.putheader("Host", f"127.0.0.1:{app.server_port}")
            connection.putheader("Origin", f"http://127.0.0.1:{app.server_port}")
            connection.putheader("X-Factor-CSRF", headers["X-Factor-CSRF"])
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "2")
            connection.endheaders(b"{}")
            response = connection.getresponse()
            assert response.status == 403  # implicit Host plus explicit Host is ambiguous
            response.read()
        finally:
            connection.close()


def test_token_not_logged_on_invalid_http_method(tmp_path, capsys):
    with running(tmp_path, enable_factor_import=True) as app:
        session_token = token(app)
        request(app, "/", method="SECRET" + session_token)
    captured = capsys.readouterr()
    assert session_token not in captured.err + captured.out


def test_cli_factor_import_opt_in(monkeypatch, capsys):
    from market_data import cli
    calls = []
    class App:
        server_port = 8767
        def serve_forever(self):
            pass
        def server_close(self):
            pass
    monkeypatch.setattr(cli, "make_server", lambda *args, **kwargs: calls.append((args, kwargs)) or App())
    monkeypatch.setattr("sys.argv", ["qd-market", "serve", "--factor-import", "--factor-store-path", "runtime/synthetic.sqlite"])
    cli.main()
    assert calls == [((8767,), {"factor_store_path": "runtime/synthetic.sqlite", "enable_factor_import": True})]
    capsys.readouterr()


def test_opt_in_health_reports_writes_without_orders(tmp_path):
    with running(tmp_path, enable_factor_import=True) as app:
        status, body = request(app, "/health")
        payload = json.loads(body)
        assert status == 200 and payload["factor_import_enabled"] is True
        assert payload["mode"] == "LOCAL_RESEARCH_IMPORT"
        assert payload["orders_enabled"] is False


def test_http_concurrent_duplicate_confirm_is_one_dataset(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from factors.importer import InstrumentUniverse
    universe = InstrumentUniverse("synthetic-v1", {"SYNTH": "us_equity"})
    with running(tmp_path, enable_factor_import=True, factor_universe=universe) as app:
        headers = {"X-Factor-CSRF": token(app)}
        _, body = request(app, "/api/factors/import/preview", "POST", valid_payload(universe.version), headers)
        preview_id = json.loads(body)["preview_id"]
        barrier = Barrier(2)
        def confirm(index):
            barrier.wait()
            status, body = request(app, "/api/factors/import/commit", "POST", {
                "preview_id": preview_id, "request_id": f"concurrent-{index}"}, headers)
            assert status == 200
            return json.loads(body)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, (0, 1)))
        assert sum(not result["result"]["replayed"] for result in results) == 1
        with FactorStore(tmp_path / "factors.sqlite") as store:
            assert store.revision() == 1
            assert len(store.definitions()) == 1


def test_expired_preview_http_410(tmp_path, monkeypatch):
    from datetime import timedelta
    from factors.importer import InstrumentUniverse
    from market_data import factor_routes
    universe = InstrumentUniverse("synthetic-v1", {"SYNTH": "us_equity"})
    with running(tmp_path, enable_factor_import=True, factor_universe=universe) as app:
        headers = {"X-Factor-CSRF": token(app)}
        _, body = request(app, "/api/factors/import/preview", "POST", valid_payload(universe.version), headers)
        preview_id = json.loads(body)["preview_id"]
        original_clock = factor_routes.datetime
        class Clock:
            @staticmethod
            def now(tz):
                return original_clock.now(tz) + timedelta(minutes=15)
        monkeypatch.setattr(factor_routes, "datetime", Clock)
        status, body = request(app, "/api/factors/import/commit", "POST", {
            "preview_id": preview_id, "request_id": "expired"}, headers)
        assert status == 410 and json.loads(body)["error"] == "PREVIEW_EXPIRED"
        with FactorStore(tmp_path / "factors.sqlite") as store:
            assert store.revision() == 0


def test_factor_token_redacted_from_legacy_get_errors(tmp_path, monkeypatch):
    from market_data import server
    def rejected(market, fetcher):
        raise ValueError("invalid market " + market)
    monkeypatch.setattr(server.providers, "get_catalog", rejected)
    with running(tmp_path, enable_factor_import=True) as app:
        session_token = token(app)
        status, body = request(app, "/api/catalog?market=" + session_token)
        assert status == 400
        assert session_token.encode() not in body


def test_factor_routes_reject_absolute_request_targets(tmp_path):
    with running(tmp_path, enable_factor_import=True) as app:
        headers = {"X-Factor-CSRF": token(app)}
        status, body = request(app, "http://example.test/api/factors/import/preview", "POST", {}, headers)
        assert status == 400 and json.loads(body)["error"] == "INVALID_REQUEST_TARGET"
        status, body = request(app, "http://example.test/api/factors/session")
        assert status == 400 and json.loads(body)["error"] == "INVALID_REQUEST_TARGET"


@pytest.mark.parametrize("method,route", [("POST", "/api/factors/import/preview"),
                                                ("POST", "/api/factors/import/commit"),
                                                ("POST", "/unrelated"),
                                                ("GET", "/api/factors/session"),
                                                ("GET", "/unrelated")])
def test_malformed_bracket_target_has_safe_http_rejection(tmp_path, capsys, method, route):
    from http.client import RemoteDisconnected
    from contextlib import closing
    import sqlite3
    with running(tmp_path, enable_factor_import=True) as app:
        session_token = token(app)
        target = f"http://[{session_token}]{route}"
        disconnected = False
        try:
            status, body = request(app, target, method, {}, {"X-Factor-CSRF": session_token})
        except RemoteDisconnected:
            disconnected = True
            status, body = None, b""
    captured = capsys.readouterr()
    leaked = session_token in captured.err + captured.out or session_token.encode() in body
    assert leaked is False
    assert disconnected is False
    assert status == 400 and json.loads(body)["error"] == "INVALID_REQUEST_TARGET"
    with closing(sqlite3.connect(tmp_path / "factors.sqlite")) as database:
        payloads = list(database.iterdump())
    persisted = any(session_token in row for row in payloads)
    assert persisted is False


def test_factor_catalog_and_missing_current_observations_are_read_only(tmp_path):
    with running(tmp_path) as app:
        status, body = request(app, '/api/factors')
        payload = json.loads(body)
        assert status == 200 and len(payload['factors']) == 16
        assert len([x for x in payload['factors'] if x['binding_status'] == 'UNIMPLEMENTED']) == 7
        status, body = request(app, '/api/factors/observations?factor_id=minute.vwap60&version=1.0.0')
        payload = json.loads(body)
        assert status == 200 and payload['cards'] == [] and payload['message'] == '无本次观测'
        assert request(app, '/factors')[0] == 200
    assert not (tmp_path / 'factors.sqlite').exists()


@pytest.mark.parametrize('query', ['path=/etc/passwd', 'factor_id=minute.vwap60&version=1.0.0&sql=select',
                                   'factor_id=minute.vwap60&factor_id=other&version=1.0.0',
                                   'factor_id=minute.vwap60', 'factor_id=minute.vwap60&version='])
def test_factor_read_query_whitelist(tmp_path, query):
    with running(tmp_path) as app:
        assert request(app, '/api/factors/observations?' + query)[0] == 400
        assert request(app, '/api/factors?' + query)[0] == 400


def test_metadata_cannot_render_html(tmp_path):
    from factors.importer import InstrumentUniverse
    universe = InstrumentUniverse('synthetic-v1', {'SYNTH': 'us_equity'})
    with running(tmp_path, enable_factor_import=True, factor_universe=universe) as app:
        payload = valid_payload(universe.version)
        payload['definition_json']['definition']['name'] = '<img src=x onerror="alert(1)">'
        _, body = request(app, '/api/factors/import/preview', 'POST', payload, {'X-Factor-CSRF': token(app)})
        _, body = request(app, '/api/factors/import/commit', 'POST', {
            'preview_id': json.loads(body)['preview_id'], 'request_id': 'html-text'}, {'X-Factor-CSRF': token(app)})
        assert json.loads(body)['ok']
        status, body = request(app, '/api/factors')
        assert status == 200
        catalog = json.loads(body)
        entry = next(item for item in catalog['factors'] if item['definition']['ref']['factor_id'] == 'external_value')
        assert entry['definition']['name'] == '<img src=x onerror="alert(1)">'
        assert entry['lifecycle_state'] == 'CANDIDATE'


def test_external_cannot_shadow_immutable_builtin_definition(tmp_path):
    from factors.models import FactorRef
    from test_factor_models import definition
    ref = FactorRef(factor_id='minute.vwap60', version='1.0.0')
    with FactorStore(tmp_path / 'factors.sqlite') as store:
        with pytest.raises(ValueError, match='DEFINITION_VERSION_CONFLICT'):
            store.register_definition(definition(ref=ref, name='fake built-in'), expected_revision=0, request_id='shadow')
        assert store.revision() == 0


def test_supplied_reports_stale_evidence_and_pit_query_modes(tmp_path):
    from test_factor_bindings import reports, IDS
    from test_factor_models import T
    with running(tmp_path, factor_reports=reports()) as app:
        _, body = request(app, '/api/factors/observations?factor_id=minute.vwap60&version=1.0.0')
        card = json.loads(body)['cards'][0]
        assert card['observation']['value'] == IDS['minute.vwap60']
        assert 'EXPIRED_QUALITY' in card['block_reasons']
        assert card['eligible_for_production'] is False
        _, body = request(app, '/api/factors/observations?factor_id=minute.vwap60&version=1.0.0&mode=live')
        assert json.loads(body)['cards'] == []
        assert request(app, '/api/factors/observations?factor_id=minute.vwap60&version=1.0.0&mode=strict_replay')[0] == 400
        _, body = request(app, '/api/factors/observations?factor_id=minute.vwap60&version=1.0.0&as_of=2026-10-06T11:00:00Z')
        assert json.loads(body)['cards'] == []
    assert not (tmp_path / 'factors.sqlite').exists()


def test_external_preview_rejects_reserved_catalog_ids_before_any_save(tmp_path):
    from factors.importer import InstrumentUniverse
    from factors import registry
    from factors.models import FactorRef
    from test_factor_import import csv_bytes
    universe = InstrumentUniverse('synthetic-v1', {'SYNTH':'us_equity'})
    with running(tmp_path, enable_factor_import=True, factor_universe=universe) as app:
        payload = valid_payload(universe.version)
        original = registry.get(FactorRef(factor_id='minute.vwap60', version='1.0.0'))
        payload['definition_json']['definition'] = original.model_dump(mode='json')
        payload['csv_text'] = csv_bytes({'factor_id':'minute.vwap60','factor_version':'1.0.0','unit':original.unit}).decode()
        _, body = request(app, '/api/factors/import/preview', 'POST', payload, {'X-Factor-CSRF':token(app)})
        assert 'DEFINITION_VERSION_CONFLICT' in {item['code'] for item in json.loads(body)['errors']}
        with FactorStore(tmp_path / 'factors.sqlite') as store:
            assert store.revision() == 0 and not store.definitions()


def test_reserved_definition_conflict_cannot_be_silently_displayed_as_builtin(tmp_path):
    from test_factor_models import definition
    import sqlite3
    from contextlib import closing
    from factors.models import FactorRef
    with running(tmp_path, enable_factor_import=True) as app:
        # Simulate an existing B1-era conflicting record without using product APIs.
        forged = definition(ref=FactorRef(factor_id='minute.vwap60',version='1.0.0'),name='forged')
        with closing(sqlite3.connect(tmp_path/'factors.sqlite')) as db:
            db.execute('INSERT INTO definitions VALUES (?,?,?,?)', ('minute.vwap60','1.0.0',forged.model_dump_json(),'a'*64));db.commit()
        assert request(app, '/api/factors')[0] == 409


def test_current_data_status_is_unavailable_without_a_visible_value(tmp_path):
    with running(tmp_path) as app:
        _, body = request(app, '/api/factors')
        assert all(item['DATA_STATUS'] == 'UNAVAILABLE' for item in json.loads(body)['factors'])


def test_external_similar_prefix_is_not_a_locked_component(tmp_path):
    from factors.importer import InstrumentUniverse
    from test_factor_import import csv_bytes
    universe=InstrumentUniverse('synthetic-v1',{'SYNTH':'us_equity'})
    with running(tmp_path,enable_factor_import=True,factor_universe=universe) as app:
        payload=valid_payload(universe.version)
        payload['definition_json']['definition']['ref']['factor_id']='locked.external_research'
        payload['csv_text']=csv_bytes({'factor_id':'locked.external_research'}).decode()
        _,body=request(app,'/api/factors/import/preview','POST',payload,{'X-Factor-CSRF':token(app)})
        _,body=request(app,'/api/factors/import/commit','POST',{'preview_id':json.loads(body)['preview_id'],'request_id':'prefix'}, {'X-Factor-CSRF':token(app)})
        assert json.loads(body)['ok']
        _,body=request(app,'/api/factors')
        catalog=json.loads(body)['factors']
        assert len([item for item in catalog if item['binding_status']=='UNIMPLEMENTED'])==7
        assert next(item for item in catalog if item['definition']['ref']['factor_id']=='locked.external_research')['binding_status']=='EXTERNAL_VALUE'


def test_history_reads_without_import_enable_and_never_write(tmp_path):
    from test_factor_models import definition
    from test_factor_trials import trial
    from factors.trials import record_trial
    from test_factor_lifecycle import event, approved
    path = tmp_path / 'factors.sqlite'
    with FactorStore(path) as store:
        store.register_definition(definition(), expected_revision=0, request_id='candidate')
        assert approved(store, event(store, 'retired')).accepted
        record_trial(trial(status='FAILED', error_reason='<script>failure</script>'), store=store)
        revision = store.revision()
    with running(tmp_path) as app:
        query = '?factor_id=external_value&version=1'
        status, body = request(app, '/api/factors/lifecycle'+query)
        lifecycle = json.loads(body)
        assert status == 200 and lifecycle['lifecycle_state'] == 'retired'
        assert lifecycle['events'][0]['to_state'] == 'retired'
        status, body = request(app, '/api/factors/trials'+query)
        payload = json.loads(body)
        assert status == 200 and payload['trials'][0]['record']['status'] == 'FAILED'
        assert payload['trials'][0]['record']['report_kind'] == 'factor'
        assert payload['trials'][0]['evidence']['promotable'] is False
        assert request(app, '/api/factors/lifecycle'+query, 'POST', {'approval':True})[0] == 405
        assert request(app, '/api/factors/trials'+query, 'POST', {'approval':True})[0] == 405
        _, body = request(app, '/api/factors')
        row = next(x for x in json.loads(body)['factors'] if x['definition']['ref']['factor_id']=='external_value')
        assert row['lifecycle_state'] == 'retired' and row['eligible_for_production'] is False
    with FactorStore(path) as store:
        assert store.revision() == revision


@pytest.mark.parametrize('route', ['/api/factors/lifecycle', '/api/factors/trials'])
@pytest.mark.parametrize('query', ['', '?factor_id=minute.vwap60', '?factor_id=minute.vwap60&version=1.0.0&sql=select',
    '?factor_id=minute.vwap60&factor_id=other&version=1.0.0', '?factor_id=minute.vwap60&version=1.0.0&mode=live',
    '?factor_id=minute.vwap60&version=1.0.0&as_of=2026-10-06T12:00:00'])
def test_history_query_discipline(tmp_path, route, query):
    with running(tmp_path) as app:
        assert request(app, route+query)[0] == 400
    assert not (tmp_path/'factors.sqlite').exists()


def test_empty_history_is_honest_and_read_only(tmp_path):
    with running(tmp_path) as app:
        query = '?factor_id=minute.vwap60&version=1.0.0'
        status, body = request(app, '/api/factors/lifecycle'+query)
        payload = json.loads(body)
        assert status == 200 and payload['events'] == []
        assert payload['lifecycle_state'] == 'LEGACY_UNVALIDATED'
        status, body = request(app, '/api/factors/trials'+query)
        payload = json.loads(body)
        assert status == 200 and payload['trials'] == [] and payload['message'] == '未运行 / No recorded trials'
    assert not (tmp_path/'factors.sqlite').exists()


def test_read_old_schema_does_not_initialize_governance(tmp_path):
    from contextlib import closing
    import sqlite3
    path = tmp_path/'factors.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript('CREATE TABLE metadata(singleton INTEGER PRIMARY KEY, revision INTEGER); INSERT INTO metadata VALUES(1,0); CREATE TABLE definitions(factor_id TEXT, version TEXT, payload TEXT, audit_hash TEXT);')
    before = path.read_bytes()
    with running(tmp_path) as app:
        status, body = request(app, '/api/factors/lifecycle?factor_id=minute.vwap60&version=1.0.0')
        payload = json.loads(body)
        assert status == 200 and payload['history_initialized'] is False and payload['events'] == []
        assert payload['lifecycle_state'] == 'LEGACY_UNVALIDATED'
        status, body = request(app, '/api/factors/trials?factor_id=minute.vwap60&version=1.0.0')
        assert status == 200 and json.loads(body)['trials'] == []
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'lifecycle_%'").fetchall() == []


def test_virtual_component_local_history_state_is_not_hidden_by_catalog(tmp_path):
    from factors.lifecycle import LifecycleEvent, local_operation, record_transition
    from factors.models import FactorRef
    from test_factor_models import T
    ref = FactorRef(factor_id='locked.amihud', version='1.0.0')
    with FactorStore(tmp_path/'factors.sqlite') as store:
        event = LifecycleEvent(event_id='component-retire',factor_ref=ref,from_state='candidate',
            to_state='retired',at=T,actor='local reviewer',reason='Retain unimplemented component definition',
            evidence_ids=[],expected_revision=0)
        with local_operation(store, actor_label=event.actor, event=event, now=T,
                             approval_id='component-approval',approval_reason='Explicit local retirement') as context:
            assert record_transition(event, store=store, approvals={'context':context}).accepted
        assert store.definitions() == []
    with running(tmp_path) as app:
        _, body = request(app, '/api/factors')
        entry = next(x for x in json.loads(body)['factors'] if x['definition']['ref']==ref.model_dump(mode='json'))
        assert entry['lifecycle_state'] == 'retired' and entry['binding_status'] == 'UNIMPLEMENTED'
