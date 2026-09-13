"""Executable PREDICT→LOCK→REVEAL→SCORE + Skeptic falsification bars."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from time_machine.engine import TimeMachineRun
from time_machine.seal import assert_sealed_unreadable, unseal_path


def test_full_cycle_and_truth_isolation(tmp_path: Path):
    run = TimeMachineRun(tmp_path / "tm", code_commit="TESTCOMMIT")
    run.init_from_fixture()
    # truth sealed by filesystem: open/list must fail
    with pytest.raises((PermissionError, OSError)):
        list(run.sealed_dir.iterdir())
    assert_sealed_unreadable(run.sealed_dir)
    run.predict()
    assert_sealed_unreadable(run.sealed_dir)
    h = run.lock()
    assert len(h) == 64
    run.reveal()
    score = run.score()
    assert score["lock_id"] == h
    assert score["domain_scores"]["fundamental"]["point"] == pytest.approx(5.2)
    unseal_path(run.sealed_dir)


def test_post_lock_mutation_invalidates_score(tmp_path: Path):
    run = TimeMachineRun(tmp_path / "tm2", code_commit="TESTCOMMIT")
    run.init_from_fixture()
    run.predict()
    run.lock()
    run.reveal()
    p = run.lock_dir / "evidence_pack.json"
    data = json.loads(p.read_text())
    data["objects"][0]["value"] = 999.0
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="LOCK_INVALIDATED"):
        run.score()
    unseal_path(run.sealed_dir)


def test_code_commit_mutation_invalidates(tmp_path: Path):
    run = TimeMachineRun(tmp_path / "tm3", code_commit="AAA")
    run.init_from_fixture()
    run.predict()
    run.lock()
    run.reveal()
    m = json.loads((run.lock_dir / "experiment_manifest.json").read_text())
    m["code_commit"] = "BBB"
    (run.lock_dir / "experiment_manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="LOCK_INVALIDATED"):
        run.score()
    unseal_path(run.sealed_dir)
