"""Shell-level test: run_all.sh must exit non-zero if ANY step failed, even when later steps
(file copies, other verifications) succeed. Uses a temp copy of run_all.sh with stubbed steps.
Audit 2026-10-11 (finding: false success after verification failure)."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "docs/research/real_data_sample_20261011/run_all.sh"

STUB_OK = "import sys\nsys.exit(0)\n"


def build(tmp_path, failing=None):
    """Create tmp/docs/research/real_data_sample_20261011 with stub step scripts; return (script, logdir)."""
    here = tmp_path / "docs/research/real_data_sample_20261011"
    here.mkdir(parents=True)
    shutil.copy(SRC, here / "run_all.sh")
    (here / "data_points.csv").write_text("a,b\n1,2\n")
    (here / "results.json").write_text("[]\n")
    (here / "used_inputs.json").write_text("[]\n")
    stub = {
        # steps that produce files so the later copies / cmp succeed
        "fetch_all.py": "import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index('--out')+1]);p.mkdir(exist_ok=True)\n(p/'retrieval_log.json').write_text('{}')\n",
        "extract.py": "import pathlib,sys\npathlib.Path(sys.argv[sys.argv.index('--out')+1]).write_text(open('data_points.csv').read())\n",
        "recompute.py": "import shutil,sys\nshutil.copy('results.json', sys.argv[2])\nshutil.copy('used_inputs.json', sys.argv[3]) if len(sys.argv)>3 else None\n",
        "max_prices.py": "import pathlib,sys\nd=pathlib.Path(sys.argv[sys.argv.index('--out-dir')+1]);(d/'max_results.json').write_text('{}')\n",
        "verify_inputs.py": STUB_OK,
        "crosscheck_repo_code.py": STUB_OK,
    }
    if failing:
        stub[failing] = "import sys\nprint('stub step failing')\nsys.exit(1)\n"
    for name, body in stub.items():
        (here / name).write_text(body)
    # fake `uv run python X ...` -> python3 X ...
    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text('#!/usr/bin/env bash\nshift; shift; exec python3 "$@"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return here / "run_all.sh", tmp_path / "log", bindir


def run(tmp_path, failing=None):
    script, log, bindir = build(tmp_path, failing)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    p = subprocess.run(["bash", str(script), str(log)], capture_output=True, text=True, env=env, timeout=120)
    return p, log


def test_all_steps_ok_exit_zero(tmp_path):
    p, log = run(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (log / "retrieval_log.json").exists() and (log / "max_results_fresh.json").exists()


def test_verify_fails_but_later_copy_succeeds_overall_nonzero(tmp_path):
    p, log = run(tmp_path, failing="verify_inputs.py")
    assert (log / "retrieval_log.json").exists(), "copy step did run and succeed after the failing verify step"
    assert p.returncode != 0, "FALSE SUCCESS: verify_inputs failed but run_all.sh exited 0\n" + p.stdout


def test_crosscheck_fails_overall_nonzero(tmp_path):
    p, _ = run(tmp_path, failing="crosscheck_repo_code.py")
    assert p.returncode != 0


def test_first_step_fails_overall_nonzero_and_later_steps_still_logged(tmp_path):
    p, log = run(tmp_path, failing="fetch_all.py")
    assert p.returncode != 0
    assert "verify_inputs" in (log / "run.log").read_text()
