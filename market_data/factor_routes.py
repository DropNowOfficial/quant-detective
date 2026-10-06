"""Exactly two opt-in, same-origin, loopback-only factor write routes."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets
import socket
import sqlite3
from urllib.parse import urlsplit

from factors.importer import (DEFAULT_UNIVERSE, InstrumentUniverse, MAX_DEFINITION_BYTES,
                              MAX_REQUEST_BYTES, MAX_ROWS, PREVIEW_TTL, REQUIRED_FIELDS,
                              OPTIONAL_FIELDS, commit_import, discard_store_previews, preview_import)
from factors.store import FactorStore

PREVIEW_PATH = "/api/factors/import/preview"
COMMIT_PATH = "/api/factors/import/commit"
SESSION_PATH = "/api/factors/session"
WRITE_PATHS = frozenset({PREVIEW_PATH, COMMIT_PATH})
CONFLICT_CODES = frozenset({"REVISION_CONFLICT", "REQUEST_ID_CONFLICT", "DEFINITION_VERSION_CONFLICT",
                            "DATASET_VERSION_CONFLICT", "SOURCE_REVISION_AMBIGUOUS"})
BAD_REQUEST_CODES = frozenset({"PREVIEW_NOT_FOUND", "PREVIEW_STORE_MISMATCH", "PREVIEW_INVALID",
                               "INVALID_IMPORT_CLOCK", "INVALID_REQUEST_ID", "UTC_REQUIRED"})


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("INVALID_JSON")
        result[key] = value
    return result


def _not_json_constant(value):
    raise ValueError("INVALID_JSON")


class FactorRoutes:
    def __init__(self, path: Path, universe: InstrumentUniverse = DEFAULT_UNIVERSE):
        if not isinstance(universe, InstrumentUniverse):
            raise ValueError("INVALID_INSTRUMENT_UNIVERSE")
        self.path = Path(path)
        self.universe = universe
        self._csrf_token = secrets.token_urlsafe(32)
        # Establish the controlled DB now, close before request worker threads.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FactorStore(self.path):
            pass

    def close(self):
        discard_store_previews(self.path)
        self._csrf_token = ""

    def redact_error_body(self, body: bytes) -> bytes:
        return body.replace(self._csrf_token.encode(), b"[redacted]") if self._csrf_token else body

    @staticmethod
    def _one_header(handler, name):
        values = handler.headers.get_all(name) or []
        return values[0] if len(values) == 1 else None

    def _guard(self, handler, *, write):
        host = self._one_header(handler, "Host")
        allowed = {f"127.0.0.1:{handler.server.server_port}", f"localhost:{handler.server.server_port}"}
        origin = self._one_header(handler, "Origin")
        if (host not in allowed or (write and origin != "http://" + host)
                or (not write and handler.headers.get_all("Origin") and origin != "http://" + host)):
            handler.send_json({"ok": False, "error": "LOCAL_ORIGIN_REQUIRED"}, 403)
            return False
        if write:
            token = self._one_header(handler, "X-Factor-CSRF")
            if (not token or not token.isascii() or not self._csrf_token
                    or not secrets.compare_digest(token, self._csrf_token)):
                handler.send_json({"ok": False, "error": "CSRF_REQUIRED"}, 403)
                return False
        return True

    def session(self, handler):
        if not self._guard(handler, write=False):
            return
        try:
            parsed = urlsplit(handler.path)
        except ValueError:
            handler.send_json({"ok": False, "error": "INVALID_REQUEST_TARGET"}, 400)
            return
        if parsed.scheme or parsed.netloc:
            handler.send_json({"ok": False, "error": "INVALID_REQUEST_TARGET"}, 400)
            return
        if parsed.query or parsed.fragment:
            handler.send_json({"ok": False, "error": "QUERY_NOT_ALLOWED"}, 400)
            return
        handler.send_json({"ok": True, "csrf_token": self._csrf_token, "universe": self.universe.payload(),
                           "limits": {"request_bytes": MAX_REQUEST_BYTES, "definition_bytes": MAX_DEFINITION_BYTES,
                                      "csv_rows": MAX_ROWS, "preview_seconds": int(PREVIEW_TTL.total_seconds())},
                           "csv_fields": {"required": sorted(REQUIRED_FIELDS), "optional": sorted(OPTIONAL_FIELDS)},
                           "pit_grade": "RECONSTRUCTED"})

    def _body(self, handler):
        content_type = self._one_header(handler, "Content-Type")
        if (not content_type or content_type.lower().strip() not in {
                "application/json", "application/json; charset=utf-8", "application/json;charset=utf-8"}):
            handler.send_json({"ok": False, "error": "JSON_CONTENT_TYPE_REQUIRED"}, 415)
            return None
        length = self._one_header(handler, "Content-Length")
        if handler.headers.get_all("Transfer-Encoding") or length is None or not re.fullmatch(r"[0-9]+", length):
            handler.send_json({"ok": False, "error": "CONTENT_LENGTH_REQUIRED"}, 411)
            return None
        if len(length) > 10 or int(length) > MAX_REQUEST_BYTES:
            handler.send_json({"ok": False, "error": "REQUEST_TOO_LARGE"}, 413)
            return None
        handler.connection.settimeout(5)
        try:
            raw = handler.rfile.read(int(length))
            if len(raw) != int(length):
                raise ValueError("INVALID_JSON")
            payload = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_strict_object,
                                 parse_constant=_not_json_constant)
            if not isinstance(payload, dict):
                raise ValueError("INVALID_JSON")
            return payload
        except (ValueError, RecursionError, OverflowError, socket.timeout):
            handler.send_json({"ok": False, "error": "INVALID_JSON"}, 400)
            return None

    def post(self, handler):
        try:
            parsed = urlsplit(handler.path)
        except ValueError:
            handler.send_json({"ok": False, "error": "INVALID_REQUEST_TARGET"}, 400)
            return
        if not self._guard(handler, write=True):
            return
        if parsed.scheme or parsed.netloc:
            handler.send_json({"ok": False, "error": "INVALID_REQUEST_TARGET"}, 400)
            return
        if parsed.query or parsed.fragment:
            handler.send_json({"ok": False, "error": "QUERY_NOT_ALLOWED"}, 400)
            return
        payload = self._body(handler)
        if payload is None:
            return
        now = datetime.now(UTC)
        try:
            with FactorStore(self.path) as store:
                if parsed.path == PREVIEW_PATH:
                    if (set(payload) != {"csv_text", "definition_json", "mapping"}
                            or not isinstance(payload["csv_text"], str)
                            or not isinstance(payload["definition_json"], dict)
                            or not isinstance(payload["mapping"], dict)):
                        raise ValueError("INVALID_PREVIEW_REQUEST")
                    preview = preview_import(payload["csv_text"].encode("utf-8", errors="strict"),
                                             payload["definition_json"], payload["mapping"], store=store,
                                             now=now, universe=self.universe)
                    handler.send_json({"ok": not preview.errors, **preview.payload()})
                else:
                    if (set(payload) != {"preview_id", "request_id"}
                            or not isinstance(payload["preview_id"], str)
                            or not re.fullmatch(r"[A-Za-z0-9_-]{32}", payload["preview_id"])
                            or not isinstance(payload["request_id"], str)
                            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", payload["request_id"])):
                        raise ValueError("INVALID_COMMIT_REQUEST")
                    result = commit_import(payload["preview_id"], request_id=payload["request_id"],
                                           store=store, now=now)
                    manifest = store.dataset_manifest(result.dataset_id, result.version)
                    handler.send_json({"ok": True, "result": result.model_dump(mode="json"),
                                       "manifest": manifest.model_dump(mode="json")})
        except (ValueError, TypeError, UnicodeError) as exc:
            code = str(exc)
            if code in CONFLICT_CODES:
                status = 409
            elif code == "PREVIEW_EXPIRED":
                status = 410
            elif code == "PREVIEW_CAPACITY":
                status = 503
            else:
                status = 400
                if code not in BAD_REQUEST_CODES | {"INVALID_PREVIEW_REQUEST", "INVALID_COMMIT_REQUEST"}:
                    code = "INVALID_IMPORT_REQUEST"
            handler.send_json({"ok": False, "error": code}, status)
        except sqlite3.Error:
            handler.send_json({"ok": False, "error": "FACTOR_STORE_UNAVAILABLE"}, 503)


READ_PATHS = frozenset({'/api/factors', '/api/factors/observations'})


def read_factors(handler, parsed):
    """Fixed read interfaces; no new scan, arbitrary path, SQL or mutation route."""
    from copy import deepcopy
    from urllib.parse import parse_qs
    from factors import registry
    from factors.bindings import observations_from_report, binding_diagnostics, evidence_card
    from factors.models import FactorRef

    now = datetime.now(UTC)
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = set() if parsed.path == '/api/factors' else {'factor_id', 'version', 'as_of', 'mode'}
    if (parsed.fragment or set(query) - allowed or any(len(values) != 1 or not values[0] for values in query.values())
            or (parsed.path.endswith('/observations') and not {'factor_id', 'version'} <= set(query))):
        handler.send_json({'ok': False, 'error': 'INVALID_FACTOR_QUERY'}, 400)
        return
    try:
        values = {key: items[0] for key, items in query.items()}
        as_of = datetime.fromisoformat(values['as_of']).astimezone(UTC) if 'as_of' in values else now
        if 'as_of' in values and datetime.fromisoformat(values['as_of']).tzinfo is None:
            raise ValueError('UTC_REQUIRED')
        mode = values.get('mode', 'exploratory')
        if mode not in {'live', 'strict_replay', 'exploratory'}:
            raise ValueError('INVALID_QUERY_MODE')
        # Already captured application reports and analyses only; never initiate
        # live.snapshot(), providers, a scan, or file reads chosen by a query.
        reports = deepcopy(handler.server.factor_reports)
        if not reports:
            with handler.server.live.lock:
                rows = [deepcopy(row) for session in handler.server.live.sessions.values()
                        for row in session.analyses.values()]
            reports = {'minute_report': {'rows': rows}}
        bound = observations_from_report(reports, recorded_at=now)
        diagnostics = binding_diagnostics(reports)
        virtual = registry.definitions()
        catalog = {(item.ref.factor_id, item.ref.version): item for item in virtual}
        routes = handler.server.factor_routes
        external = []
        if routes is not None:
            with FactorStore(routes.path) as store:
                for item in store.definitions():
                    registry.check_reserved_definition(item)
                    catalog[(item.ref.factor_id, item.ref.version)] = item
                if parsed.path.endswith('/observations'):
                    ref = FactorRef(factor_id=values['factor_id'], version=values['version'])
                    external = store.observations(ref, as_of=as_of, mode=mode)
        if parsed.path == '/api/factors':
            factors = []
            for item in sorted(catalog.values(), key=lambda entry: (entry.ref.factor_id, entry.ref.version)):
                builtin = item.calculator_key is not None
                locked = any(original.ref == item.ref and original.calculator_key is None for original in virtual)
                factors.append({'definition': item.model_dump(mode='json'),
                                'definition_fingerprint': registry.definition_fingerprint(item),
                                'binding_status': 'BOUND' if builtin else 'UNIMPLEMENTED' if locked else 'EXTERNAL_VALUE',
                                'CONTRACT_STATUS': 'READY' if builtin else 'NOT READY',
                                'DATA_STATUS': 'READY' if any(
                                    observation.ref == item.ref and observation.value is not None
                                    and evidence_card(observation, now=now)['current_observation']
                                    for observation in bound) else 'UNAVAILABLE',
                                'VALIDATION_STATUS': 'NEEDS BACKTEST',
                                'lifecycle_state': 'LEGACY_UNVALIDATED' if builtin else 'CANDIDATE',
                                'eligible_for_production': False})
            handler.send_json({'ok': True, 'factors': factors, 'hypotheses': registry.hypotheses(),
                               'axes': ['CONTRACT_STATUS', 'DATA_STATUS', 'VALIDATION_STATUS'],
                               'diagnostics': diagnostics, 'import_enabled': routes is not None})
        else:
            ref = FactorRef(factor_id=values['factor_id'], version=values['version'])
            if (ref.factor_id, ref.version) not in catalog:
                raise ValueError('UNKNOWN_DEFINITION')
            selected = [item for item in bound if item.ref == ref and item.effective_available_at <= as_of]
            if mode == 'live':
                selected = [item for item in selected if item.pit_grade == 'FORWARD_OBSERVED']
            elif mode == 'strict_replay' and any(item.pit_grade == 'RECONSTRUCTED' for item in selected):
                raise ValueError('PIT_EVIDENCE_REQUIRED')
            cards = [evidence_card(item, now=now) for item in sorted(
                [*selected, *external], key=lambda item: (item.observed_at, item.instrument_id))]
            handler.send_json({'ok': True, 'ref': ref.model_dump(mode='json'), 'mode': mode,
                               'as_of': as_of.isoformat(), 'cards': cards,
                               'message': '无本次观测' if not cards else 'Last source-bound evidence; eligibility unvalidated',
                               'diagnostics': [entry for entry in diagnostics if entry['factor_id'] == ref.factor_id]})
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        # Fixed codes avoid leaking malformed metadata/query/token text.
        code = str(exc)
        safe = CONFLICT_CODES | {'PIT_EVIDENCE_REQUIRED', 'UNKNOWN_DEFINITION', 'UTC_REQUIRED', 'INVALID_QUERY_MODE'}
        if code not in safe:
            code = 'INVALID_FACTOR_EVIDENCE_OR_QUERY'
        handler.send_json({'ok': False, 'error': code}, 409 if code in CONFLICT_CODES else 400)
    except sqlite3.Error:
        handler.send_json({'ok': False, 'error': 'FACTOR_STORE_UNAVAILABLE'}, 503)
