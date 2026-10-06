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
