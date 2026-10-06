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
