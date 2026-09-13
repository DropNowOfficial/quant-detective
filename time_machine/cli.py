
"""CLI: qd-tm predict|lock|reveal|score|run-fixture"""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from time_machine.engine import TimeMachineRun

def git_head(repo: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    except Exception:
        return "UNKNOWN"

def main(argv=None):
    p = argparse.ArgumentParser(prog="qd-tm")
    p.add_argument("command", choices=["init", "predict", "lock", "reveal", "score", "run-fixture"])
    p.add_argument("--workdir", default="time_machine/runs/dev_syn001")
    args = p.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    run = TimeMachineRun(root / args.workdir, code_commit=git_head(root))
    if args.command == "init":
        run.init_from_fixture(); print("initialized", run.run_dir)
    elif args.command == "predict":
        print(json.dumps(run.predict(), indent=2))
    elif args.command == "lock":
        print(run.lock())
    elif args.command == "reveal":
        print(json.dumps(run.reveal(), indent=2))
    elif args.command == "score":
        print(json.dumps(run.score(), indent=2))
    elif args.command == "run-fixture":
        run.init_from_fixture()
        run.predict()
        h = run.lock()
        # prove sealed
        from time_machine.seal import assert_sealed_unreadable
        assert_sealed_unreadable(run.sealed_dir)
        run.reveal()
        s = run.score()
        print(json.dumps({"lock_id": h, "score": s}, indent=2))
        print("TM_FIXTURE_HASH=" + h)

if __name__ == "__main__":
    main()
