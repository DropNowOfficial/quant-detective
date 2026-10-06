"""Atomic, append-only SQLite factor evidence and deterministic as-of reads.

No API overwrites/deletes definitions, datasets or observation evidence. A dataset
commit may atomically introduce definitions, supporting read-only import previews.
See docs/factors-store.md for provenance, replay and stable ValueError codes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import CommitResult, DatasetManifest, FactorDefinition, FactorObservation, FactorRef, require_utc
from .registry import canonical_json, definition_fingerprint, fingerprint, source_content

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    revision INTEGER NOT NULL CHECK(revision >= 0)
);
INSERT OR IGNORE INTO metadata VALUES (1, 0);
CREATE TABLE IF NOT EXISTS definitions (
    factor_id TEXT NOT NULL,
    version TEXT NOT NULL,
    payload TEXT NOT NULL,
    audit_hash TEXT NOT NULL,
    PRIMARY KEY(factor_id, version)
);
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    audit_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    PRIMARY KEY(dataset_id, version)
);
CREATE TABLE IF NOT EXISTS observations (
    dataset_id TEXT NOT NULL,
    dataset_version INTEGER NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    observed_us INTEGER NOT NULL,
    effective_us INTEGER NOT NULL,
    provider_us INTEGER NOT NULL,
    store_revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    audit_hash TEXT NOT NULL,
    PRIMARY KEY(dataset_id, dataset_version, factor_id, factor_version, instrument_id, observed_us),
    FOREIGN KEY(dataset_id, dataset_version) REFERENCES datasets(dataset_id, version),
    FOREIGN KEY(factor_id, factor_version) REFERENCES definitions(factor_id, version)
);
CREATE INDEX IF NOT EXISTS observations_asof
ON observations(factor_id, factor_version, effective_us);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result TEXT NOT NULL
);
"""
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _micros(value: datetime) -> int:
    delta = value - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _ref_key(ref: FactorRef) -> tuple[str, str]:
    return ref.factor_id, ref.version


def _snapshot(value, model, error):
    if not isinstance(value, model):
        raise ValueError(error)
    # Revalidation prevents mutable nested containers / model_construct from
    # bypassing the strict boundary, and isolates committed data from callers.
    return model.model_validate({name: getattr(value, name) for name in model.model_fields})


class FactorStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._closed = False
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(_SCHEMA)
            # Even accidental internal SQL cannot rewrite evidence records.
            for table in ("definitions", "datasets", "observations", "requests"):
                for action in ("UPDATE", "DELETE"):
                    self._db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                        f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_RECORD'); END"
                    )
        except BaseException:
            self._db.close()
            self._closed = True
            raise

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._db.close()
            self._closed = True

    def _ensure_open(self):
        if self._closed:
            raise ValueError("STORE_CLOSED")

    @contextmanager
    def _transaction(self):
        self._ensure_open()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def revision(self) -> int:
        self._ensure_open()
        return self._db.execute("SELECT revision FROM metadata WHERE singleton=1").fetchone()[0]

    def definitions(self) -> list[FactorDefinition]:
        self._ensure_open()
        return [FactorDefinition.model_validate_json(row[0]) for row in self._db.execute(
            "SELECT payload FROM definitions ORDER BY factor_id, version")]

    def dataset_manifest(self, dataset_id: str, version: int) -> DatasetManifest | None:
        """Read the retained original manifest, including on canonical replay."""
        self._ensure_open()
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise ValueError("INVALID_DATASET_ID")
        if type(version) is not int or version < 1:
            raise ValueError("INVALID_DATASET_VERSION")
        row = self._db.execute("SELECT payload FROM datasets WHERE dataset_id=? AND version=?",
                               (dataset_id, version)).fetchone()
        return DatasetManifest.model_validate_json(row[0]) if row else None

    @staticmethod
    def _check_request(expected_revision, request_id):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("INVALID_EXPECTED_REVISION")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("INVALID_REQUEST_ID")

    def _request(self, request_id, operation, content_hash):
        row = self._db.execute(
            "SELECT operation, content_hash, result FROM requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != operation or row[1] != content_hash:
            raise ValueError("REQUEST_ID_CONFLICT")
        return row[2]

    def _remember(self, request_id, operation, content_hash, result):
        self._db.execute("INSERT INTO requests VALUES (?, ?, ?, ?)",
                         (request_id, operation, content_hash, result))

    def _check_revision(self, expected_revision):
        if expected_revision != self.revision():
            raise ValueError("REVISION_CONFLICT")

    def _next_revision(self):
        revision = self.revision() + 1
        self._db.execute("UPDATE metadata SET revision=? WHERE singleton=1", (revision,))
        return revision

    def _existing_definition(self, ref):
        row = self._db.execute("SELECT payload FROM definitions WHERE factor_id=? AND version=?",
                               _ref_key(ref)).fetchone()
        return FactorDefinition.model_validate_json(row[0]) if row else None

    def _check_definition(self, definition):
        existing = self._existing_definition(definition.ref)
        if existing is not None and definition_fingerprint(existing) != definition_fingerprint(definition):
            raise ValueError("DEFINITION_VERSION_CONFLICT")
        return existing

    def _insert_definition(self, definition):
        payload = definition.model_dump(mode="json")
        self._db.execute("INSERT INTO definitions VALUES (?, ?, ?, ?)",
                         (*_ref_key(definition.ref), canonical_json(payload), fingerprint(payload)))

    def register_definition(self, definition: FactorDefinition, *, expected_revision: int,
                            request_id: str) -> int:
        """Append a standalone catalog entry; return the unchanged/new revision.

        Imports introducing definitions should use commit_dataset(definitions=...)
        instead, so catalog and dataset validation cannot partially commit.
        """
        self._ensure_open()
        self._check_request(expected_revision, request_id)
        definition = _snapshot(definition, FactorDefinition, "INVALID_DEFINITION")
        content_hash = definition_fingerprint(definition)
        with self._transaction():
            replay = self._request(request_id, "definition", content_hash)
            if replay is not None:
                return json.loads(replay)
            existing = self._check_definition(definition)
            if existing is not None:
                # Return the current unchanged revision for a catalog duplicate;
                # each request's exact result then becomes replayable.
                revision = self.revision()
            else:
                self._check_revision(expected_revision)
                self._insert_definition(definition)
                revision = self._next_revision()
            self._remember(request_id, "definition", content_hash, canonical_json(revision))
            return revision

    def commit_dataset(self, manifest: DatasetManifest, observations: Iterable[FactorObservation], *,
                       expected_revision: int, request_id: str,
                       definitions: Iterable[FactorDefinition] = ()) -> CommitResult:
        """Commit manifest, rows, optional new definitions and request atomically.

        Exact normalized source repeats replay the first immutable commit, even
        with fresh operational UUIDs/times and an otherwise stale revision.
        """
        self._ensure_open()
        self._check_request(expected_revision, request_id)
        manifest = _snapshot(manifest, DatasetManifest, "INVALID_MANIFEST")
        rows = [_snapshot(row, FactorObservation, "INVALID_OBSERVATION") for row in observations]
        supplied = [_snapshot(item, FactorDefinition, "INVALID_DEFINITION") for item in definitions]
        if manifest.row_count != len(rows):
            raise ValueError("ROW_COUNT_MISMATCH")
        identities = set()
        for row in rows:
            identity = (*_ref_key(row.ref), row.instrument_id, row.observed_at)
            if identity in identities:
                raise ValueError("DUPLICATE_OBSERVATION")
            identities.add(identity)
            if row.source_ref != manifest.source_ref:
                raise ValueError("SOURCE_MISMATCH")
            if row.ingested_at > manifest.imported_at:
                raise ValueError("IMPORT_BEFORE_INGESTION")
        proposed = {}
        for item in supplied:
            key = _ref_key(item.ref)
            if key in proposed and proposed[key] != item:
                raise ValueError("DEFINITION_VERSION_CONFLICT")
            proposed[key] = item

        with self._transaction():
            resolved = dict(proposed)
            new_definitions = []
            for item in proposed.values():
                if self._check_definition(item) is None:
                    new_definitions.append(item)
            for row in rows:
                key = _ref_key(row.ref)
                if key not in resolved:
                    resolved[key] = self._existing_definition(row.ref)
                if resolved[key] is None:
                    raise ValueError("UNKNOWN_DEFINITION")
            manifest_content = manifest.model_dump(mode="json")
            for key in ("dataset_id", "version", "imported_at", "file_sha256"):
                manifest_content.pop(key)
            content_hash = fingerprint({
                "manifest": manifest_content,
                "definitions": [resolved[key].model_dump(mode="json") for key in sorted(resolved)],
                "rows": sorted((source_content(row) for row in rows), key=canonical_json),
            })
            replay = self._request(request_id, "dataset", content_hash)
            if replay is not None:
                return CommitResult.model_validate_json(replay).model_copy(update={"replayed": True})
            identity = self._db.execute("SELECT content_hash FROM datasets WHERE dataset_id=? AND version=?",
                                         (manifest.dataset_id, manifest.version)).fetchone()
            if identity is not None and identity[0] != content_hash:
                raise ValueError("DATASET_VERSION_CONFLICT")
            duplicate = self._db.execute("SELECT result FROM datasets WHERE content_hash=?", (content_hash,)).fetchone()
            if duplicate is not None:
                self._remember(request_id, "dataset", content_hash, duplicate[0])
                return CommitResult.model_validate_json(duplicate[0]).model_copy(update={"replayed": True})
            self._check_revision(expected_revision)
            for item in new_definitions:
                self._insert_definition(item)
            revision = self._next_revision()
            result = CommitResult(dataset_id=manifest.dataset_id, version=manifest.version,
                                  inserted_rows=len(rows), replayed=False, store_revision=revision)
            result_json = result.model_dump_json()
            payload = manifest.model_dump(mode="json")
            self._db.execute("INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                             (manifest.dataset_id, manifest.version, canonical_json(payload),
                              content_hash, fingerprint({"manifest": payload, "rows": sorted(
                                  (row.model_dump(mode="json") for row in rows), key=canonical_json)}), result_json))
            for row in rows:
                payload = row.model_dump(mode="json")
                self._db.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (manifest.dataset_id, manifest.version, *_ref_key(row.ref), row.instrument_id,
                                  _micros(row.observed_at), _micros(row.effective_available_at),
                                  _micros(row.provider_available_at), revision, canonical_json(payload), fingerprint(payload)))
            self._remember(request_id, "dataset", content_hash, result_json)
            return result

    def observations(self, ref: FactorRef, *, as_of: datetime, mode: str) -> list[FactorObservation]:
        """Latest available revision per instrument/event, never a future revision.

        live excludes reconstructed history. strict_replay fails closed if any
        reconstructed row would be selected; exploratory retains its claims.
        """
        self._ensure_open()
        if not isinstance(mode, str) or mode not in {"live", "strict_replay", "exploratory"}:
            raise ValueError("INVALID_QUERY_MODE")
        require_utc(as_of)
        ref = _snapshot(ref, FactorRef, "INVALID_FACTOR_REF")
        selected = {}
        ambiguous = set()
        # SQL delivery order is only a deterministic tie-break, never source
        # revision chronology. Publication vintage is compared below.
        for (payload,) in self._db.execute(
                "SELECT payload FROM observations "
                "WHERE factor_id=? AND factor_version=? AND effective_us<=? "
                "ORDER BY provider_us, effective_us, store_revision, dataset_id, dataset_version",
                (*_ref_key(ref), _micros(as_of))):
            row = FactorObservation.model_validate_json(payload)
            if row.pit_grade == "RECONSTRUCTED" and mode == "live":
                continue
            key = row.instrument_id, row.observed_at
            previous = selected.get(key)
            if previous is None or row.source_published_at > previous.source_published_at:
                selected[key] = row
                ambiguous.discard(key)
            elif row.source_published_at == previous.source_published_at:
                identity = (row.value, row.missing_reason, row.input_hash, row.input_refs)
                previous_identity = (previous.value, previous.missing_reason,
                                     previous.input_hash, previous.input_refs)
                if identity != previous_identity:
                    ambiguous.add(key)
                selected[key] = row
        if ambiguous:
            raise ValueError("SOURCE_REVISION_AMBIGUOUS")
        if mode == "strict_replay" and any(row.pit_grade == "RECONSTRUCTED" for row in selected.values()):
            raise ValueError("PIT_EVIDENCE_REQUIRED")
        return sorted(selected.values(), key=lambda row: (row.observed_at, row.instrument_id))
