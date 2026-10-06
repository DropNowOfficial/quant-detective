"""Real, temporary SQLite contracts and as-of revision tests."""
from contextlib import closing
from datetime import timedelta
import sqlite3

import pytest

from factors.models import FactorRef
from factors.store import FactorStore
from test_factor_models import T, definition, manifest, observation


@pytest.fixture
def store(tmp_path):
    with FactorStore(tmp_path / "factors.sqlite") as result:
        yield result


def commit(store, *, expected_revision=0, request_id="request-1", **changes):
    return store.commit_dataset(manifest(), [observation()], expected_revision=expected_revision,
                                request_id=request_id, definitions=[definition()], **changes)


def test_future_revision_does_not_change_past_asof(store):
    first = commit(store)
    first_day_values = [x.value for x in store.observations(definition().ref, as_of=T, mode="strict_replay")]
    assert first_day_values == [10.0]
    later = T + timedelta(days=1)
    revised = observation(value=12.0, source_published_at=later, provider_available_at=later,
                           ingested_at=later, computed_at=later, effective_available_at=later,
                           input_hash="c" * 64)
    store.commit_dataset(manifest(version=2, imported_at=later, file_sha256="d" * 64), [revised],
                         expected_revision=first.store_revision, request_id="request-2")
    assert [x.value for x in store.observations(definition().ref, as_of=T, mode="strict_replay")] == first_day_values
    assert [x.value for x in store.observations(definition().ref, as_of=later, mode="strict_replay")] == [12.0]


def test_reconstructed_not_eligible_for_strict_pit_query(store):
    later = T + timedelta(days=10)
    item = observation(pit_grade="RECONSTRUCTED", ingested_at=later, computed_at=later,
                       effective_available_at=T, availability_basis="unverified historical claim")
    store.commit_dataset(manifest(imported_at=later), [item], expected_revision=0,
                         request_id="history", definitions=[definition()])
    with pytest.raises(ValueError, match="^PIT_EVIDENCE_REQUIRED$"):
        store.observations(definition().ref, as_of=T, mode="strict_replay")
    assert [x.value for x in store.observations(definition().ref, as_of=T, mode="exploratory")] == [10.0]
    assert store.observations(definition().ref, as_of=later, mode="live") == []


def test_future_reconstructed_rows_do_not_invalidate_past_strict_query(store):
    commit(store)
    later = T + timedelta(days=1)
    item = observation(observed_at=later, source_published_at=later, provider_available_at=later,
                       ingested_at=later, computed_at=later, effective_available_at=later,
                       pit_grade="RECONSTRUCTED", availability_basis="historical claim")
    store.commit_dataset(manifest(version=2, imported_at=later), [item], expected_revision=1, request_id="future")
    assert [x.value for x in store.observations(definition().ref, as_of=T, mode="strict_replay")] == [10.0]


def test_manifest_and_rows_commit_atomically(store):
    bad = observation(ref=FactorRef(factor_id="unknown", version="1"))
    with pytest.raises(ValueError, match="UNKNOWN_DEFINITION"):
        store.commit_dataset(manifest(row_count=2), [observation(), bad], expected_revision=0,
                             request_id="bad", definitions=[definition()])
    assert store.revision() == 0
    assert store.definitions() == []
    assert store.observations(definition().ref, as_of=T, mode="exploratory") == []
    assert commit(store, request_id="bad").store_revision == 1


def test_same_request_id_replays_result(store):
    original = commit(store)
    replayed = commit(store, expected_revision=0)
    assert replayed.dataset_id == original.dataset_id
    assert replayed.replayed is True
    assert replayed.store_revision == original.store_revision == store.revision() == 1
    assert replayed.inserted_rows == original.inserted_rows == 1
    assert len(store.observations(definition().ref, as_of=T, mode="live")) == 1


def test_exact_content_duplicate_with_new_request_id_is_idempotent(store):
    original = commit(store)
    replayed = commit(store, request_id="different-request", expected_revision=0)
    assert replayed.replayed is True
    assert replayed.dataset_id == original.dataset_id
    assert store.revision() == 1


def test_import_clock_does_not_change_content_duplicate(store):
    original = commit(store)
    replayed = store.commit_dataset(manifest(imported_at=T + timedelta(days=1)), [observation()],
                                     expected_revision=0, request_id="later", definitions=[definition()])
    assert replayed.replayed is True
    assert replayed.dataset_id == original.dataset_id
    assert store.revision() == 1


def test_request_id_conflict_never_overwrites(store):
    commit(store)
    with pytest.raises(ValueError, match="^REQUEST_ID_CONFLICT$"):
        store.commit_dataset(manifest(version=2), [observation(value=12.0)], expected_revision=1,
                             request_id="request-1")
    assert [x.value for x in store.observations(definition().ref, as_of=T, mode="live")] == [10.0]
    assert store.revision() == 1


def test_revision_conflict_never_partially_commits(store):
    commit(store)
    with pytest.raises(ValueError, match="^REVISION_CONFLICT$"):
        store.commit_dataset(manifest(version=2), [observation(value=12.0)], expected_revision=0,
                             request_id="new")
    assert store.revision() == 1


def test_dataset_version_conflict_needs_new_version(store):
    commit(store)
    with pytest.raises(ValueError, match="^DATASET_VERSION_CONFLICT$"):
        store.commit_dataset(manifest(), [observation(value=12.0)], expected_revision=1, request_id="new")
    assert store.revision() == 1
    assert [x.value for x in store.observations(definition().ref, as_of=T, mode="live")] == [10.0]


def test_definition_change_creates_new_version(store):
    original = commit(store)
    old_definition = store.definitions()[0]
    with pytest.raises(ValueError, match="^DEFINITION_VERSION_CONFLICT$"):
        store.register_definition(definition(formula_text="different"), expected_revision=1, request_id="def-bad")
    new_def = definition(ref=FactorRef(factor_id="external_value", version="2"), formula_text="different")
    store.register_definition(new_def, expected_revision=1, request_id="def-good")
    assert store.definitions() == [old_definition, new_def]
    assert original.inserted_rows == 1
    assert store.observations(old_definition.ref, as_of=T, mode="live")[0].input_hash == "a" * 64


def test_registration_is_idempotent_and_persists_on_reopen(tmp_path):
    path = tmp_path / "factors.sqlite"
    with FactorStore(path) as first:
        assert first.register_definition(definition(), expected_revision=0, request_id="definition") == 1
        assert first.register_definition(definition(), expected_revision=0, request_id="same") == 1
    with FactorStore(path) as reopened:
        assert reopened.revision() == 1
        assert reopened.definitions() == [definition()]
        result = reopened.commit_dataset(manifest(), [observation()], expected_revision=1, request_id="dataset")
        assert result.store_revision == 2
    with FactorStore(path) as reopened:
        assert reopened.observations(definition().ref, as_of=T, mode="live")[0].quality == observation().quality


def test_two_connections_enforce_optimistic_revision(tmp_path):
    path = tmp_path / "factors.sqlite"
    with FactorStore(path) as first, FactorStore(path) as second:
        commit(first)
        with pytest.raises(ValueError, match="^REVISION_CONFLICT$"):
            second.commit_dataset(manifest(version=2), [observation(value=12.0)],
                                  expected_revision=0, request_id="second")
        assert second.revision() == 1


@pytest.mark.parametrize("bad", [True, -1, "0"])
def test_revision_type_is_strict(store, bad):
    with pytest.raises(ValueError, match="^INVALID_EXPECTED_REVISION$"):
        commit(store, expected_revision=bad)
    assert store.revision() == 0


def test_row_count_duplicate_identity_source_and_import_time_are_checked(store):
    for data, rows, code in [
        (manifest(row_count=2), [observation()], "ROW_COUNT_MISMATCH"),
        (manifest(row_count=2), [observation(), observation(value=12.0)], "DUPLICATE_OBSERVATION"),
        (manifest(source_ref="other"), [observation()], "SOURCE_MISMATCH"),
        (manifest(imported_at=T - timedelta(seconds=1)), [observation()], "IMPORT_BEFORE_INGESTION"),
    ]:
        with pytest.raises(ValueError, match=f"^{code}$"):
            store.commit_dataset(data, rows, expected_revision=0, request_id=code, definitions=[definition()])
        assert store.revision() == 0
    assert store.definitions() == []


def test_commit_is_rolled_back_on_storage_failure(store):
    # A real SQLite constraint failure occurs after attempted definition/manifest writes.
    with closing(sqlite3.connect(store.path)) as database:
        database.execute("CREATE TRIGGER reject_rows BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        commit(store)
    assert store.revision() == 0
    assert store.definitions() == []
    with closing(sqlite3.connect(store.path)) as database:
        assert database.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
        database.execute("DROP TRIGGER reject_rows")
    assert commit(store).store_revision == 1


def test_query_requires_supported_mode_and_aware_utc_asof(store):
    for mode in ["validated", "", True]:
        with pytest.raises(ValueError, match="^INVALID_QUERY_MODE$"):
            store.observations(definition().ref, as_of=T, mode=mode)
    with pytest.raises(ValueError, match="^UTC_REQUIRED$"):
        store.observations(definition().ref, as_of=T.replace(tzinfo=None), mode="live")
    assert store.observations(definition().ref, as_of=T, mode="live") == []


def test_resources_are_explicitly_closed(tmp_path):
    store = FactorStore(tmp_path / "factors.sqlite")
    store.close()
    store.close()
    with pytest.raises(ValueError, match="^STORE_CLOSED$"):
        store.revision()


def test_operational_rerun_is_idempotent_and_preserves_original_availability(store):
    original = commit(store)
    later = T + timedelta(days=1)
    rerun = observation(ingested_at=later, computed_at=later, effective_available_at=later)
    result = store.commit_dataset(manifest(dataset_id="new-operational-id", imported_at=later), [rerun],
                                  expected_revision=0, request_id="rerun", definitions=[definition()])
    assert result.replayed is True
    assert result.dataset_id == original.dataset_id
    assert store.revision() == 1
    assert store.observations(definition().ref, as_of=T, mode="strict_replay")[0].computed_at == T


def test_changed_source_availability_claim_is_conflicting_content(store):
    commit(store)
    later = T + timedelta(seconds=1)
    changed = observation(provider_available_at=later, ingested_at=later, computed_at=later,
                          effective_available_at=later)
    with pytest.raises(ValueError, match="^DATASET_VERSION_CONFLICT$"):
        store.commit_dataset(manifest(imported_at=later), [changed], expected_revision=1, request_id="changed-claim")
    assert store.revision() == 1


def test_canonical_row_order_is_idempotent(store):
    one = observation()
    two = observation(instrument_id="US:SYNTH2", input_hash="c" * 64)
    data = manifest(row_count=2)
    original = store.commit_dataset(data, [one, two], expected_revision=0, request_id="ordered",
                                    definitions=[definition()])
    replay = store.commit_dataset(data, [two, one], expected_revision=0, request_id="reversed")
    assert replay.dataset_id == original.dataset_id
    assert replay.replayed is True
    assert store.revision() == 1


def test_later_actual_forward_revision_can_supersede_reconstructed_claim(store):
    later = T + timedelta(days=1)
    historical = observation(pit_grade="RECONSTRUCTED", ingested_at=later, computed_at=later,
                             availability_basis="historical claim")
    store.commit_dataset(manifest(imported_at=later), [historical], expected_revision=0,
                         request_id="history", definitions=[definition()])
    with pytest.raises(ValueError, match="^PIT_EVIDENCE_REQUIRED$"):
        store.observations(definition().ref, as_of=T, mode="strict_replay")
    forward = observation(ingested_at=later, computed_at=later, effective_available_at=later)
    store.commit_dataset(manifest(version=2, imported_at=later), [forward], expected_revision=1,
                         request_id="actual")
    assert [x.pit_grade for x in store.observations(definition().ref, as_of=later, mode="strict_replay")] == ["FORWARD_OBSERVED"]


def test_delayed_older_source_does_not_override_newer_source_revision(store):
    commit(store)
    tomorrow = T + timedelta(days=1)
    later = T + timedelta(days=2)
    revised = observation(value=12.0, source_published_at=tomorrow, provider_available_at=tomorrow,
                          ingested_at=tomorrow, computed_at=tomorrow, effective_available_at=tomorrow,
                          input_hash="c" * 64)
    store.commit_dataset(manifest(version=2, imported_at=tomorrow, file_sha256="c" * 64), [revised],
                         expected_revision=1, request_id="revised")
    old = observation(ingested_at=later, computed_at=later, effective_available_at=later)
    # A new universe/file audit identity does not make old source evidence a new revision.
    store.commit_dataset(manifest(version=3, imported_at=later, file_sha256="d" * 64,
                                  universe_version="synthetic-universe-2"), [old],
                         expected_revision=2, request_id="old-source")
    assert [x.value for x in store.observations(definition().ref, as_of=later, mode="strict_replay")] == [12.0]


def test_failed_replay_does_not_add_request_alias(store):
    commit(store)
    with pytest.raises(ValueError, match="^REQUEST_ID_CONFLICT$"):
        store.register_definition(definition(), expected_revision=1, request_id="request-1")
    assert store.revision() == 1


def test_sql_evidence_is_append_only_and_audit_hashes_stay_fixed(store):
    commit(store)
    with closing(sqlite3.connect(store.path)) as database:
        old = database.execute("SELECT audit_hash FROM datasets").fetchone()[0]
        row_hash = database.execute("SELECT audit_hash FROM observations").fetchone()[0]
        for table in ("datasets", "definitions", "observations", "requests"):
            with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_RECORD"):
                database.execute(f"DELETE FROM {table}")
            with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_RECORD"):
                column = "request_id" if table == "requests" else "audit_hash"
                database.execute(f"UPDATE {table} SET {column}={column}")
        database.rollback()
    commit(store, request_id="duplicate")
    with closing(sqlite3.connect(store.path)) as database:
        assert database.execute("SELECT audit_hash FROM datasets").fetchone()[0] == old
        assert database.execute("SELECT audit_hash FROM observations").fetchone()[0] == row_hash


def test_mutable_returned_containers_cannot_change_stored_definition(store):
    original = definition()
    commit(store)
    fetched = store.definitions()[0]
    fetched.min_history["bars"] = 999
    fetched.input_fields.append("injected")
    assert store.definitions() == [original]


def test_commit_revalidates_bypassed_model_construction(store):
    bad = observation().model_copy(update={"value": True})
    with pytest.raises(ValueError):
        store.commit_dataset(manifest(), [bad], expected_revision=0, request_id="bad",
                             definitions=[definition()])
    assert store.revision() == 0


@pytest.mark.parametrize("mode", [[], {}, None, 1])
def test_invalid_query_mode_returns_stable_code(store, mode):
    with pytest.raises(ValueError, match="^INVALID_QUERY_MODE$"):
        store.observations(definition().ref, as_of=T, mode=mode)


def test_live_and_strict_hide_every_future_actual_dependency(store):
    later = T + timedelta(seconds=1)
    item = observation(dependency_available_ats=[later], effective_available_at=later)
    store.commit_dataset(manifest(), [item], expected_revision=0, request_id="dependent", definitions=[definition()])
    for mode in ["live", "strict_replay", "exploratory"]:
        assert store.observations(definition().ref, as_of=T, mode=mode) == []
        assert len(store.observations(definition().ref, as_of=later, mode=mode)) == 1


def test_definition_request_replay_preserves_result_after_later_commit(store):
    assert store.register_definition(definition(), expected_revision=0, request_id="catalog") == 1
    store.commit_dataset(manifest(), [observation()], expected_revision=1, request_id="dataset")
    assert store.revision() == 2
    assert store.register_definition(definition(), expected_revision=0, request_id="catalog") == 1
    assert store.revision() == 2


def test_all_optional_definitions_are_atomic_on_identity_conflict(store):
    commit(store)
    new_def = definition(ref=FactorRef(factor_id="new_factor", version="1"))
    with pytest.raises(ValueError, match="^DATASET_VERSION_CONFLICT$"):
        store.commit_dataset(manifest(), [observation(value=99.0)], expected_revision=1,
                             request_id="bad", definitions=[new_def])
    assert store.definitions() == [definition()]
    assert store.revision() == 1


def test_byte_different_file_with_identical_canonical_content_replays_first_manifest(store):
    original = commit(store)
    replay = store.commit_dataset(manifest(file_sha256="f" * 64), [observation()],
                                  expected_revision=0, request_id="different-bytes")
    assert replay.replayed is True
    assert replay.dataset_id == original.dataset_id
    assert store.revision() == 1
    with closing(sqlite3.connect(store.path)) as database:
        from factors.models import DatasetManifest
        saved = DatasetManifest.model_validate_json(database.execute("SELECT payload FROM datasets").fetchone()[0])
        assert saved.file_sha256 == "b" * 64


def test_dataset_manifest_roundtrip_replay_and_read_isolation(store):
    original = commit(store)
    assert store.dataset_manifest(original.dataset_id, original.version) == manifest()
    replay = store.commit_dataset(manifest(dataset_id="different-run-id", file_sha256="f" * 64),
                                  [observation()], expected_revision=0, request_id="formatted")
    retained = store.dataset_manifest(replay.dataset_id, replay.version)
    assert retained == manifest()
    assert retained.file_sha256 == "b" * 64
    retained.coverage_gaps.append("cannot mutate stored metadata")
    assert store.dataset_manifest(original.dataset_id, original.version) == manifest()
    assert store.dataset_manifest("different-run-id", 1) is None
    assert store.dataset_manifest(original.dataset_id, 2) is None


@pytest.mark.parametrize("identity", [True, None, 1, "", " "])
def test_dataset_manifest_identity_is_strict(store, identity):
    with pytest.raises(ValueError, match="^INVALID_DATASET_ID$"):
        store.dataset_manifest(identity, 1)


@pytest.mark.parametrize("version", [True, None, 0, -1, "1"])
def test_dataset_manifest_version_is_strict(store, version):
    with pytest.raises(ValueError, match="^INVALID_DATASET_VERSION$"):
        store.dataset_manifest("synthetic-dataset", version)


def test_dataset_manifest_is_persistent_and_requires_open_store(tmp_path):
    path = tmp_path / "factors.sqlite"
    with FactorStore(path) as first:
        commit(first)
    with FactorStore(path) as second:
        assert second.dataset_manifest("synthetic-dataset", 1) == manifest()
    with pytest.raises(ValueError, match="^STORE_CLOSED$"):
        second.dataset_manifest("synthetic-dataset", 1)
