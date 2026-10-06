"""Bounded external-value imports: preview in memory, then one atomic commit.

CSV is data, never a filename, URL or executable. External availability claims
remain RECONSTRUCTED and exploratory; no historical verifier is implemented.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import io
import math
from pathlib import Path
import secrets
from threading import RLock
from types import MappingProxyType
from typing import Mapping

from pydantic import ValidationError

from market_data.quality import QualityResult
from market_data.us_watch import DEFAULT_SYMBOLS
from .models import CommitResult, DatasetManifest, FactorDefinition, FactorObservation, require_utc
from .registry import canonical_json, fingerprint, source_content
from .store import FactorStore

MAX_REQUEST_BYTES = 5 * 1024 * 1024
MAX_DEFINITION_BYTES = 64 * 1024
MAX_ROWS = 20000
PREVIEW_TTL = timedelta(minutes=15)
MAX_PREVIEWS = 64
MAX_CACHE_BYTES = 64 * 1024 * 1024
SAMPLE_ROWS = 10
AVAILABILITY_BASIS = "unverified external historical availability claim; exploratory only"
REQUIRED_FIELDS = frozenset({"instrument_id", "observed_at", "source_published_at",
                             "provider_available_at", "value", "unit", "factor_id", "factor_version"})
OPTIONAL_FIELDS = frozenset({"missing_reason", "effective_available_at"})
MANIFEST_FIELDS = frozenset({"dataset_id", "version", "source_ref", "universe_version", "adjustment",
                           "provider", "permission_basis", "source_timezone", "corporate_action_basis",
                           "history_version", "coverage_gaps"})


@dataclass(frozen=True)
class InstrumentUniverse:
    """An application-supplied catalog snapshot, never populated by the upload."""
    version: str
    instruments: Mapping[str, str]

    def __post_init__(self):
        if (not isinstance(self.version, str) or not self.version.strip()
                or not isinstance(self.instruments, Mapping)
                or any(not isinstance(key, str) or not key.strip()
                       or not isinstance(value, str) or not value.strip()
                       for key, value in self.instruments.items())):
            raise ValueError("INVALID_INSTRUMENT_UNIVERSE")
        object.__setattr__(self, "instruments", MappingProxyType(dict(self.instruments)))

    def payload(self):
        return {"version": self.version, "instruments": [
            {"instrument_id": key, "market": value} for key, value in sorted(self.instruments.items())]}


_DEFAULT_INSTRUMENTS = {symbol: "us_equity" for symbol in (*DEFAULT_SYMBOLS, "QQQ")}
DEFAULT_UNIVERSE = InstrumentUniverse("us-watch-v1-" + fingerprint(_DEFAULT_INSTRUMENTS)[:12],
                                      _DEFAULT_INSTRUMENTS)


@dataclass(frozen=True)
class ImportIssue:
    row: int | None
    code: str
    field: str | None = None


@dataclass(frozen=True)
class ImportPreview:
    preview_id: str
    content_hash: str | None
    expected_revision: int
    expires_at: datetime
    errors: list[ImportIssue]
    warnings: list[str]
    sample_rows: list[dict]
    proposed_manifest: DatasetManifest | None

    def payload(self):
        return {"preview_id": self.preview_id, "content_hash": self.content_hash,
                "expected_revision": self.expected_revision, "expires_at": self.expires_at.isoformat(),
                "errors": [{"row": item.row, "code": item.code, "field": item.field} for item in self.errors],
                "warnings": list(self.warnings), "sample_rows": self.sample_rows,
                "proposed_manifest": self.proposed_manifest.model_dump(mode="json")
                if self.proposed_manifest else None}


@dataclass(frozen=True)
class _CachedPreview:
    store_key: tuple
    created_at: datetime
    expires_at: datetime
    expected_revision: int
    definition_json: str | None
    manifest_json: str | None
    rows_json: tuple[str, ...]
    invalid: bool
    bytes_used: int


_PREVIEWS: dict[str, _CachedPreview] = {}
_CACHE_LOCK = RLock()


def _clock(now):
    require_utc(now)
    if now < datetime(1970, 1, 1, tzinfo=UTC):
        raise ValueError("INVALID_IMPORT_CLOCK")


def _store_key(store):
    if str(store.path) == ":memory:":
        return (":memory:", id(store))
    path = store.path.resolve()
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino)


def _cache(preview_id, entry, now):
    with _CACHE_LOCK:
        for key in [key for key, item in _PREVIEWS.items() if now >= item.expires_at]:
            del _PREVIEWS[key]
        if (len(_PREVIEWS) >= MAX_PREVIEWS
                or sum(item.bytes_used for item in _PREVIEWS.values()) + entry.bytes_used > MAX_CACHE_BYTES):
            raise ValueError("PREVIEW_CAPACITY")
        _PREVIEWS[preview_id] = entry


def discard_store_previews(path: Path):
    """Release this application's cache at server shutdown; no evidence writes."""
    resolved = str(Path(path).resolve())
    with _CACHE_LOCK:
        for key in [key for key, entry in _PREVIEWS.items() if entry.store_key[0] == resolved]:
            del _PREVIEWS[key]


def _timestamp(text):
    try:
        value = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        raise ValueError("INVALID_TIMESTAMP") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError("INVALID_TIMESTAMP") from None


def _content_hash(manifest, definition, observations):
    metadata = manifest.model_dump(mode="json")
    for key in ("dataset_id", "version", "imported_at", "file_sha256"):
        metadata.pop(key)
    # source_ref is identical in every parsed row. Removing that constant from
    # sort keys preserves full canonical JSON order without repeating potentially
    # 64 KiB provenance in every key. Hash one full row at a time, never one
    # materialized all-row JSON tree/string; bytes remain identical to B1.
    def sort_key(item):
        projected = source_content(item)
        projected.pop("source_ref")
        return canonical_json(projected)
    digest = hashlib.sha256()
    digest.update(b'{"definitions":[')
    digest.update(canonical_json(definition.model_dump(mode="json")).encode())
    digest.update(b'],"manifest":')
    digest.update(canonical_json(metadata).encode())
    digest.update(b',"rows":[')
    for index, item in enumerate(sorted(observations, key=sort_key)):
        if index:
            digest.update(b",")
        digest.update(canonical_json(source_content(item)).encode())
    digest.update(b"]}")
    return digest.hexdigest()


def preview_import(csv_bytes: bytes, definition_json: dict, mapping: dict[str, str], *,
                   store: FactorStore, now: datetime,
                   universe: InstrumentUniverse = DEFAULT_UNIVERSE) -> ImportPreview:
    """Validate all bounded rows without changing catalog, revision or evidence."""
    _clock(now)
    # Serialize builders so the existing shared byte budget covers retained
    # previews plus the one in-flight normalized output. No new/lower cap.
    with _CACHE_LOCK:
        for key in [key for key, item in _PREVIEWS.items() if now >= item.expires_at]:
            del _PREVIEWS[key]
        if len(_PREVIEWS) >= MAX_PREVIEWS:
            raise ValueError("PREVIEW_CAPACITY")
        retained_bytes = sum(item.bytes_used for item in _PREVIEWS.values())
        if not isinstance(universe, InstrumentUniverse):
            raise ValueError("INVALID_INSTRUMENT_UNIVERSE")
        expected_revision = store.revision()
        errors: list[ImportIssue] = []
        observations = []
        proposed_definition = proposed_manifest = None

        def error(code, row=None, field=None):
            errors.append(ImportIssue(row, code, field))

        if not isinstance(csv_bytes, bytes):
            error("CSV_BYTES_REQUIRED")
        elif len(csv_bytes) > MAX_REQUEST_BYTES:
            error("CSV_TOO_LARGE")
        try:
            serialized = canonical_json(definition_json)
            if len(serialized.encode()) > MAX_DEFINITION_BYTES:
                error("DEFINITION_TOO_LARGE")
            elif not isinstance(definition_json, dict) or set(definition_json) != {"definition", "manifest"}:
                error("INVALID_DEFINITION_DOCUMENT")
            elif (not isinstance(definition_json["manifest"], dict)
                  or set(definition_json["manifest"]) != MANIFEST_FIELDS):
                error("INVALID_MANIFEST_FIELDS")
            else:
                proposed_definition = FactorDefinition.model_validate_json(canonical_json(definition_json["definition"]))
                metadata = definition_json["manifest"]
                proposed_manifest = DatasetManifest.model_validate_json(canonical_json({
                    **metadata, "file_sha256": hashlib.sha256(csv_bytes).hexdigest(),
                    "imported_at": now.isoformat(), "availability_basis": AVAILABILITY_BASIS, "row_count": 0}))
                if proposed_manifest.universe_version != universe.version:
                    error("UNIVERSE_VERSION_MISMATCH")
                for existing in store.definitions():
                    if existing.ref == proposed_definition.ref and existing != proposed_definition:
                        error("DEFINITION_VERSION_CONFLICT")
                        break
        except (ValueError, TypeError, OverflowError, RecursionError):
            error("INVALID_DEFINITION_DOCUMENT")

        if (not isinstance(mapping, dict) or not REQUIRED_FIELDS <= set(mapping)
                or set(mapping) - (REQUIRED_FIELDS | OPTIONAL_FIELDS)
                or any(not isinstance(value, str) or not value.strip() for value in mapping.values())
                or len(set(mapping.values())) != len(mapping)):
            error("INVALID_MAPPING")

        definition_blob = proposed_definition.model_dump_json() if proposed_definition else None
        manifest_blob = proposed_manifest.model_dump_json() if proposed_manifest else None
        # Reserve the largest permitted row-count encoding before row building.
        budget_manifest = (proposed_manifest.model_copy(update={"row_count": MAX_ROWS}).model_dump_json()
                           if proposed_manifest else "")
        metadata_bytes = sum(len(blob.encode()) for blob in (definition_blob or "", budget_manifest)) + 512
        bytes_used = metadata_bytes
        row_blobs = []
        if retained_bytes + bytes_used > MAX_CACHE_BYTES:
            raise ValueError("PREVIEW_CAPACITY")

        if not errors:
            try:
                text = csv_bytes.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                error("INVALID_UTF8")
            else:
                if "\x00" in text:
                    error("INVALID_CSV")
                else:
                    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
                    try:
                        header = next(reader, [])
                        if (not header or len(set(header)) != len(header)
                                or set(header) != set(mapping.values())):
                            error("INVALID_CSV_HEADER")
                        else:
                            seen = set()
                            for count, cells in enumerate(reader, start=1):
                                row_number = reader.line_num
                                if count > MAX_ROWS:
                                    error("TOO_MANY_ROWS", row_number)
                                    break
                                if len(cells) != len(header):
                                    error("INVALID_CSV_ROW", row_number)
                                    continue
                                row = {key: cells[header.index(column)].strip() for key, column in mapping.items()}
                                item = _parse_row(row, row_number, proposed_definition, proposed_manifest, universe, now, error)
                                if item is not None:
                                    identity = (item.instrument_id, item.observed_at)
                                    if identity in seen:
                                        error("DUPLICATE_OBSERVATION", row_number)
                                    blob = item.model_dump_json()
                                    row_bytes = len(blob.encode())
                                    if retained_bytes + bytes_used + row_bytes > MAX_CACHE_BYTES:
                                        raise ValueError("PREVIEW_CAPACITY")
                                    bytes_used += row_bytes
                                    row_blobs.append(blob)
                                    seen.add(identity)
                                    observations.append(item)
                            if not observations and not errors:
                                error("EMPTY_CSV")
                    except csv.Error:
                        error("INVALID_CSV", reader.line_num)

        if proposed_manifest:
            proposed_manifest = proposed_manifest.model_copy(update={"row_count": len(observations)})
        content_hash = (_content_hash(proposed_manifest, proposed_definition, observations)
                        if not errors else None)
        preview_id = secrets.token_urlsafe(24)
        expires_at = now + PREVIEW_TTL
        manifest_blob = proposed_manifest.model_dump_json() if proposed_manifest else None
        row_blobs = tuple(row_blobs) if not errors else ()
        bytes_used = sum(len(blob.encode()) for blob in (*row_blobs, definition_blob or "", manifest_blob or "")) + 512
        entry = _CachedPreview(_store_key(store), now, expires_at, expected_revision, definition_blob,
                               manifest_blob, row_blobs, bool(errors), bytes_used)
        _cache(preview_id, entry, now)
        return ImportPreview(preview_id, content_hash, expected_revision, expires_at, errors,
                             ["PIT_EVIDENCE_REQUIRED", "EXTERNAL_VALUES_UNVERIFIED"],
                             [item.model_dump(mode="json") for item in observations[:SAMPLE_ROWS]], proposed_manifest)


def _parse_row(row, row_number, definition, manifest, universe, now, error):
    """Return one strictly constructed observation or row-scoped issues."""
    invalid = False

    def reject(code, field=None):
        nonlocal invalid
        invalid = True
        error(code, row_number, field)

    instrument = row["instrument_id"]
    if instrument not in universe.instruments:
        reject("UNKNOWN_INSTRUMENT", "instrument_id")
    elif universe.instruments[instrument] != definition.market:
        reject("INSTRUMENT_MARKET_MISMATCH", "instrument_id")
    for field, expected, code in (("unit", definition.unit, "UNIT_MISMATCH"),
                                   ("factor_id", definition.ref.factor_id, "FACTOR_ID_MISMATCH"),
                                   ("factor_version", definition.ref.version, "FACTOR_VERSION_MISMATCH")):
        if row[field] != expected:
            reject(code, field)
    times = {}
    for field in ("observed_at", "source_published_at", "provider_available_at"):
        try:
            times[field] = _timestamp(row[field])
        except ValueError as exc:
            reject(str(exc), field)
    if len(times) == 3:
        if not times["observed_at"] <= times["source_published_at"] <= times["provider_available_at"]:
            reject("IMPOSSIBLE_TIME_ORDER")
        if times["provider_available_at"] > now:
            reject("FUTURE_SOURCE_TIME", "provider_available_at")
        effective = max(times["source_published_at"], times["provider_available_at"])
        if row.get("effective_available_at"):
            try:
                effective = _timestamp(row["effective_available_at"])
                if effective < max(times.values()):
                    reject("AVAILABILITY_BEFORE_DEPENDENCY", "effective_available_at")
            except ValueError as exc:
                reject(str(exc), "effective_available_at")
    value = None
    missing_reason = row.get("missing_reason") or None
    if row["value"]:
        try:
            value = float(row["value"])
            if not math.isfinite(value):
                raise ValueError
            if value == 0:
                value = 0.0
        except ValueError:
            reject("INVALID_FACTOR_VALUE", "value")
        if missing_reason:
            reject("VALUE_WITH_MISSING_REASON", "missing_reason")
    elif not missing_reason:
        reject("MISSING_REASON_REQUIRED", "missing_reason")
    if invalid:
        return None
    input_identity = {"ref": definition.ref.model_dump(mode="json"), "instrument_id": instrument,
                      "unit": definition.unit, **{key: value.isoformat() for key, value in times.items()},
                      "effective_available_at": effective.isoformat(), "value": value,
                      "missing_reason": missing_reason, "source_ref": manifest.source_ref,
                      "provider": manifest.provider, "adjustment": manifest.adjustment,
                      "history_version": manifest.history_version,
                      "corporate_action_basis": manifest.corporate_action_basis,
                      "availability_basis": AVAILABILITY_BASIS}
    input_hash = fingerprint(input_identity)
    reasons = ("PIT_EVIDENCE_REQUIRED", "EXTERNAL_VALUES_UNVERIFIED")
    if missing_reason:
        reasons += ("MISSING_SOURCE_VALUE",)
    quality = QualityResult("UNAVAILABLE", reasons, False, False, int(now.timestamp() * 1000),
                            None, None, None, 1)
    try:
        return FactorObservation(ref=definition.ref, instrument_id=instrument, **times,
                                 ingested_at=now, computed_at=now, effective_available_at=effective,
                                 value=value, missing_reason=missing_reason, pit_grade="RECONSTRUCTED",
                                 source_ref=manifest.source_ref, input_hash=input_hash, quality=quality,
                                 availability_basis=AVAILABILITY_BASIS,
                                 input_refs=[f"external:{input_hash}"], dependency_available_ats=[])
    except ValidationError:
        reject("INVALID_OBSERVATION")
        return None


def commit_import(preview_id: str, *, request_id: str, store: FactorStore, now: datetime) -> CommitResult:
    """Commit only the private validated snapshot; stale changed state fails closed."""
    _clock(now)
    if not isinstance(preview_id, str) or not preview_id:
        raise ValueError("PREVIEW_NOT_FOUND")
    with _CACHE_LOCK:
        entry = _PREVIEWS.get(preview_id)
    if entry is None:
        raise ValueError("PREVIEW_NOT_FOUND")
    if entry.store_key != _store_key(store):
        raise ValueError("PREVIEW_STORE_MISMATCH")
    if now < entry.created_at:
        raise ValueError("INVALID_IMPORT_CLOCK")
    if now >= entry.expires_at:
        raise ValueError("PREVIEW_EXPIRED")
    if entry.invalid:
        raise ValueError("PREVIEW_INVALID")
    definition = FactorDefinition.model_validate_json(entry.definition_json)
    manifest = DatasetManifest.model_validate_json(entry.manifest_json).model_copy(update={"imported_at": now})
    rows = [FactorObservation.model_validate_json(blob) for blob in entry.rows_json]
    return store.commit_dataset(manifest, rows, expected_revision=entry.expected_revision,
                                request_id=request_id, definitions=[definition])
