"""Importer uses only synthetic bytes, explicit clocks and temporary SQLite."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import timedelta
import io
import json
from threading import Barrier

import pytest

from factors.store import FactorStore
from test_factor_models import T, definition


FIELDS = ["instrument_id", "observed_at", "source_published_at", "provider_available_at",
          "value", "unit", "factor_id", "factor_version", "missing_reason", "effective_available_at"]
MAPPING = {field: field for field in FIELDS}


def metadata(**changes):
    return {"definition": definition(market="us_equity").model_dump(mode="json"), "manifest": {
        "dataset_id": "synthetic-import", "version": 1, "source_ref": "synthetic-source",
        "universe_version": "synthetic-v1", "adjustment": "unadjusted", "provider": "synthetic",
        "permission_basis": "synthetic test fixture", "source_timezone": "UTC",
        "corporate_action_basis": "not applicable", "history_version": "synthetic-history-v1",
        "coverage_gaps": [], **changes}}


def csv_bytes(*rows, newline="\n"):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator=newline)
    writer.writeheader()
    for row in rows or ({},):
        writer.writerow({"instrument_id": "SYNTH", "observed_at": T.isoformat(),
                         "source_published_at": T.isoformat(), "provider_available_at": T.isoformat(),
                         "value": "10", "unit": "ratio", "factor_id": "external_value",
                         "factor_version": "1", "missing_reason": "", "effective_available_at": "", **row})
    return output.getvalue().encode()


@pytest.fixture
def importer():
    import importlib.util
    assert importlib.util.find_spec("factors.importer") is not None, "factor importer is not implemented"
    from factors import importer as module
    return module


@pytest.fixture
def universe(importer):
    return importer.InstrumentUniverse("synthetic-v1", {"SYNTH": "us_equity", "SYNTH2": "us_equity"})


@pytest.fixture
def store(tmp_path):
    with FactorStore(tmp_path / "factors.sqlite") as result:
        yield result


def preview(importer, store, universe, data=None, *, now=T, meta=None, mapping=None):
    return importer.preview_import(data if data is not None else csv_bytes(), meta or metadata(),
                                   mapping if mapping is not None else MAPPING,
                                   store=store, now=now, universe=universe)


def test_preview_lists_row_errors_without_writes(importer, store, universe):
    before = store.revision()
    result = preview(importer, store, universe, csv_bytes(
        {"instrument_id": "UNKNOWN"}, {"unit": "USD"}, {"observed_at": "2026-10-06T12:00:00"},
        {"factor_version": "2"}, {"value": "NaN"}, {}, {"value": "11"}))
    assert store.revision() == before
    assert store.definitions() == []
    errors = {(error.row, error.code) for error in result.errors}
    assert {(2, "UNKNOWN_INSTRUMENT"), (3, "UNIT_MISMATCH"), (4, "TIMEZONE_REQUIRED"),
            (5, "FACTOR_VERSION_MISMATCH"), (6, "INVALID_FACTOR_VALUE"),
            (8, "DUPLICATE_OBSERVATION")} <= errors
    with pytest.raises(ValueError, match="^PREVIEW_INVALID$"):
        importer.commit_import(result.preview_id, request_id="invalid", store=store, now=T)
    assert store.revision() == 0


def test_commit_is_same_preview_content(importer, store, universe):
    document = metadata()
    result = preview(importer, store, universe, now=T + timedelta(days=2), meta=document)
    assert not result.errors
    assert result.expected_revision == 0
    assert "PIT_EVIDENCE_REQUIRED" in result.warnings
    document["definition"]["unit"] = "mutated"
    result.sample_rows[0]["value"] = 999
    committed = importer.commit_import(result.preview_id, request_id="confirmed", store=store,
                                       now=T + timedelta(days=2, seconds=1))
    assert committed.store_revision == 1
    assert len(store.definitions()) == 1
    rows = store.observations(definition().ref, as_of=T, mode="exploratory")
    assert [row.value for row in rows] == [10.0]
    assert rows[0].pit_grade == "RECONSTRUCTED"
    assert rows[0].ingested_at == rows[0].computed_at == T + timedelta(days=2)
    assert rows[0].effective_available_at == T
    assert rows[0].quality.confirmation_ok is False
    assert store.observations(definition().ref, as_of=T + timedelta(days=2), mode="live") == []
    with pytest.raises(ValueError, match="^PIT_EVIDENCE_REQUIRED$"):
        store.observations(definition().ref, as_of=T, mode="strict_replay")


def test_preview_revision_conflict_returns_409(importer, store, universe):
    result = preview(importer, store, universe)
    store.register_definition(definition(ref=definition().ref.model_copy(update={"factor_id": "other"})),
                              expected_revision=0, request_id="other")
    with pytest.raises(ValueError, match="^REVISION_CONFLICT$"):
        importer.commit_import(result.preview_id, request_id="stale", store=store, now=T)
    assert store.revision() == 1
    assert store.dataset_manifest("synthetic-import", 1) is None
    assert len(store.definitions()) == 1


def test_concurrent_duplicate_import_is_single_commit(importer, store, universe):
    previews = [preview(importer, store, universe) for _ in range(2)]
    barrier = Barrier(2)
    def confirm(index):
        with FactorStore(store.path) as connection:
            barrier.wait()
            return importer.commit_import(previews[index].preview_id, request_id=f"click-{index}",
                                          store=connection, now=T)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, range(2)))
    assert sum(not result.replayed for result in results) == 1
    assert store.revision() == 1
    assert len(store.observations(definition().ref, as_of=T, mode="exploratory")) == 1
    assert {(result.dataset_id, result.version) for result in results} == {("synthetic-import", 1)}


def test_expired_preview_rejected(importer, store, universe):
    result = preview(importer, store, universe)
    assert result.expires_at == T + timedelta(minutes=15)
    with pytest.raises(ValueError, match="^PREVIEW_EXPIRED$"):
        importer.commit_import(result.preview_id, request_id="late", store=store, now=result.expires_at)
    assert store.revision() == 0


def test_preview_binding_and_clock_require_same_store(importer, store, universe, tmp_path):
    result = preview(importer, store, universe)
    with FactorStore(tmp_path / "other.sqlite") as other:
        with pytest.raises(ValueError, match="^PREVIEW_STORE_MISMATCH$"):
            importer.commit_import(result.preview_id, request_id="wrong", store=other, now=T)
    with pytest.raises(ValueError, match="^INVALID_IMPORT_CLOCK$"):
        importer.commit_import(result.preview_id, request_id="clock", store=store, now=T - timedelta(seconds=1))


def test_formatting_repeat_keeps_original_audit_manifest(importer, store, universe):
    first = preview(importer, store, universe)
    committed = importer.commit_import(first.preview_id, request_id="first", store=store, now=T)
    later = T + timedelta(minutes=1)
    second = preview(importer, store, universe, csv_bytes({"value": "10.0"}, newline="\r\n"),
                     now=later, meta=metadata(dataset_id="another-operational-id", version=2))
    assert first.content_hash == second.content_hash
    assert first.proposed_manifest.file_sha256 != second.proposed_manifest.file_sha256
    duplicate = importer.commit_import(second.preview_id, request_id="second", store=store, now=later)
    assert duplicate.replayed and duplicate.dataset_id == committed.dataset_id
    retained = store.dataset_manifest(duplicate.dataset_id, duplicate.version)
    assert retained.file_sha256 == first.proposed_manifest.file_sha256
    assert retained.imported_at == T
    assert store.revision() == 1


def test_source_provider_claim_changes_bind_input_and_content_hashes(importer, store, universe):
    first = preview(importer, store, universe, now=T + timedelta(minutes=1))
    changed = preview(importer, store, universe, csv_bytes({"provider_available_at": (
        T + timedelta(seconds=1)).isoformat()}), now=T + timedelta(minutes=1))
    assert first.content_hash != changed.content_hash
    assert first.sample_rows[0]["input_hash"] != changed.sample_rows[0]["input_hash"]


def test_highest_same_publication_conflict_stays_ambiguous(importer, store, universe):
    for index, value in enumerate(("10", "11")):
        result = preview(importer, store, universe, csv_bytes({"value": value}), meta=metadata(version=index + 1))
        importer.commit_import(result.preview_id, request_id=f"revision-{index}", store=store, now=T)
    with pytest.raises(ValueError, match="^SOURCE_REVISION_AMBIGUOUS$"):
        store.observations(definition().ref, as_of=T, mode="exploratory")


@pytest.mark.parametrize("data,code", [
    (b"\xff", "INVALID_UTF8"), (b"https://example.test/file.csv", "INVALID_CSV_HEADER"),
    (b"../../etc/passwd", "INVALID_CSV_HEADER"),
])
def test_bytes_only_no_paths_or_fetches(importer, store, universe, data, code):
    result = preview(importer, store, universe, data)
    assert code in {error.code for error in result.errors}
    assert store.revision() == 0


def test_units_identity_time_and_missing_contracts(importer, store, universe):
    result = preview(importer, store, universe, csv_bytes(
        {"factor_id": "other"}, {"source_published_at": (T - timedelta(seconds=1)).isoformat()},
        {"provider_available_at": (T + timedelta(seconds=1)).isoformat()},
        {"value": "", "missing_reason": ""}))
    assert {error.code for error in result.errors} >= {
        "FACTOR_ID_MISMATCH", "IMPOSSIBLE_TIME_ORDER", "FUTURE_SOURCE_TIME", "MISSING_REASON_REQUIRED"}
    good = preview(importer, store, universe, csv_bytes({"value": "", "missing_reason": "NO_SOURCE_VALUE"}))
    assert not good.errors
    assert good.sample_rows[0]["value"] is None
    assert good.sample_rows[0]["quality"]["state"] == "UNAVAILABLE"


def test_offsets_normalized_and_future_claim_not_visible(importer, store, universe):
    result = preview(importer, store, universe, csv_bytes({
        "observed_at": "2026-10-06T20:00:00+08:00", "effective_available_at": (T + timedelta(days=1)).isoformat()}))
    assert not result.errors
    importer.commit_import(result.preview_id, request_id="future", store=store, now=T)
    assert store.observations(definition().ref, as_of=T, mode="exploratory") == []
    assert len(store.observations(definition().ref, as_of=T + timedelta(days=1), mode="exploratory")) == 1


def test_default_catalog_exact_symbol_membership_and_immutable_copy(importer, store):
    from market_data.us_watch import DEFAULT_SYMBOLS
    universe = importer.DEFAULT_UNIVERSE
    assert universe.instruments == {symbol: "us_equity" for symbol in (*DEFAULT_SYMBOLS, "QQQ")}
    assert universe.version.startswith("us-watch-v1-")
    with pytest.raises(TypeError):
        universe.instruments["UNKNOWN"] = "us_equity"
    result = preview(importer, store, universe, csv_bytes({"instrument_id": "aapl"}),
                     meta=metadata(universe_version=universe.version))
    assert "UNKNOWN_INSTRUMENT" in {error.code for error in result.errors}


@pytest.mark.parametrize("mutate", [
    lambda doc: doc["definition"].update(status="VALIDATED"),
    lambda doc: doc.update(path="../../etc/passwd"),
    lambda doc: doc["manifest"].update(pit_grade="VINTAGE_VERIFIED"),
    lambda doc: doc["manifest"].update(universe_version="forged"),
    lambda doc: doc["definition"].update(calculator_key="__import__('os')"),
])
def test_definition_provenance_and_trust_fields_are_controlled(importer, store, universe, mutate):
    document = metadata()
    mutate(document)
    result = preview(importer, store, universe, meta=document)
    assert result.errors
    assert store.revision() == 0


def test_mapping_headers_and_resource_limits(importer, store, universe):
    incomplete = {key: value for key, value in MAPPING.items() if key != "unit"}
    assert preview(importer, store, universe, mapping=incomplete).errors
    bad_mapping = {**MAPPING, "pit_grade": "grade"}
    assert preview(importer, store, universe, mapping=bad_mapping).errors
    bad_meta = metadata()
    bad_meta["definition"]["formula_text"] = "x" * (64 * 1024)
    assert "DEFINITION_TOO_LARGE" in {error.code for error in preview(importer, store, universe, meta=bad_meta).errors}
    oversized = csv_bytes(*({"instrument_id": "UNKNOWN"} for _ in range(20001)))
    result = preview(importer, store, universe, oversized)
    assert "TOO_MANY_ROWS" in {error.code for error in result.errors}
    assert store.revision() == 0


def test_preview_cache_is_bounded_and_sample_is_bounded(importer, store, universe, monkeypatch):
    monkeypatch.setattr(importer, "MAX_PREVIEWS", 1)
    # Explicit now clears older retained previews, then a live second preview cannot evict the first.
    now = T + timedelta(days=90)
    first = preview(importer, store, universe, now=now)
    assert len(first.sample_rows) <= 10
    with pytest.raises(ValueError, match="^PREVIEW_CAPACITY$"):
        preview(importer, store, universe, now=now)
    importer.commit_import(first.preview_id, request_id="one", store=store, now=now)


def test_preview_checks_existing_definition_version_without_write(importer, store, universe):
    store.register_definition(definition(market="us_equity", unit="USD"), expected_revision=0, request_id="existing")
    result = preview(importer, store, universe)
    assert "DEFINITION_VERSION_CONFLICT" in {error.code for error in result.errors}
    assert store.revision() == 1


def test_manifest_is_strict_and_oversized_csv_rejected(importer, store, universe):
    for changes in ({"version": True}, {"source_timezone": "fake/zone"}, {"permission_basis": ""}):
        assert preview(importer, store, universe, meta=metadata(**changes)).errors
    result = preview(importer, store, universe, b"x" * (5 * 1024 * 1024 + 1))
    assert "CSV_TOO_LARGE" in {error.code for error in result.errors}


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"])
def test_timestamp_conversion_overflow_is_row_error(importer, store, universe, timestamp):
    result = preview(importer, store, universe, csv_bytes({"observed_at": timestamp}))
    assert (2, "INVALID_TIMESTAMP") in {(error.row, error.code) for error in result.errors}
    assert store.revision() == 0


def test_source_adjustment_history_are_canonical_input_dependencies(importer, store, universe):
    first = preview(importer, store, universe)
    for changes in ({"adjustment": "split-adjusted"}, {"history_version": "another-source-vintage"},
                    {"corporate_action_basis": "different adjustment evidence"}):
        changed = preview(importer, store, universe, meta=metadata(**changes))
        assert first.content_hash != changed.content_hash
        assert first.sample_rows[0]["input_hash"] != changed.sample_rows[0]["input_hash"]
