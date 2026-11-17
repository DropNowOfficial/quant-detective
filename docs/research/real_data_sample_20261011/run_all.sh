#!/usr/bin/env bash
# End-to-end reproduction of the real-data sample. Raw downloads go to UNTRACKED _raw/ and _out/.
# Usage: bash run_all.sh [LOGDIR]      (run from anywhere; needs python3>=3.11 and, for the repo-code
# cross-check, `uv` with the repo environment: uv run python ...)
set -uo pipefail
# Exit status policy (audit 2026-10-11): EVERY step runs (so the log is complete) but any failing step
# sets rc!=0 and the script exits non-zero even if later steps/copies succeed. The pipeline status is
# captured IMMEDIATELY after the pipeline, before any other command can overwrite PIPESTATUS.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
LOG="${1:-$HERE/reproduce_run_$(date -u +%Y%m%d)}"
mkdir -p "$LOG" "$HERE/_out"
cd "$HERE"
rc=0
step() { echo; echo "=== $1 ==="; }
{
  echo "start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) host_tz_now=$(date +%Z)"
  echo "git_head=$(git -C "$REPO" rev-parse HEAD) factor_candidates_tree=$(git -C "$REPO" rev-parse HEAD:factor_candidates)"
  echo "git_dirty_paths=$(git -C "$REPO" status --porcelain -- factor_candidates docs/research/real_data_sample_20261011 | wc -l)"
  step "1 fetch_all (download + SHA256 vs committed manifests)"
  python3 fetch_all.py --out _raw || rc=$?
  step "2 extract (rebuild data_points.csv from fresh raw; must equal committed file)"
  python3 extract.py --raw _raw --out _out/data_points.csv || rc=$?
  if cmp -s _out/data_points.csv data_points.csv; then echo "DATA_POINTS_IDENTICAL_TO_COMMITTED"; else echo "DATA_POINTS_DIFFER_FROM_COMMITTED"; diff <(cut -d, -f1-12 _out/data_points.csv) <(cut -d, -f1-12 data_points.csv) | head; rc=1; fi
  step "3 recompute GP/A + Sloan from rebuilt data points; must equal committed results.json"
  python3 recompute.py _out/data_points.csv _out/results.json _out/used_inputs.json > _out/recompute_output.txt || rc=$?
  if cmp -s _out/results.json results.json; then echo "RESULTS_IDENTICAL_TO_COMMITTED"; else echo "RESULTS_DIFFER_FROM_COMMITTED"; rc=1; fi
  if cmp -s _out/used_inputs.json used_inputs.json; then echo "USED_INPUTS_IDENTICAL_TO_COMMITTED"; else echo "USED_INPUTS_DIFFER_FROM_COMMITTED"; rc=1; fi
  step "4 MAX with the fixed (explicit calendar_tz) repo implementation on fresh prices"
  (cd "$REPO" && uv run python "$HERE/max_prices.py" --raw "$HERE/_raw" --out-dir "$HERE/_out" > "$HERE/_out/max_output.txt") || rc=$?
  tail -3 _out/max_output.txt
  step "4b daily price CSVs regenerated from fresh prices vs price_manifest.json (informational; verify_inputs decides)"
  python3 price_manifest.py --check _out || echo "(daily CSV bytes differ from manifest: see step 5 for the explicit tolerance decision)"
  step "5 verify_inputs (values vs fresh downloads; tolerance fallback if hashes differ)"
  python3 verify_inputs.py --raw _raw || rc=$?
  step "6 cross-check recompute against repo factor_candidates code at this HEAD"
  (cd "$REPO" && uv run python "$HERE/crosscheck_repo_code.py") || rc=$?
  echo; echo "end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=$rc"
  [ "$rc" -eq 0 ]
} 2>&1 | tee "$LOG/run.log"
# Capture the FULL PIPESTATUS array immediately: it is overwritten by the very next command.
st=("${PIPESTATUS[@]}")
producer_rc=${st[0]:-1}
tee_rc=${st[1]:-1}     # missing element => treat as failure
final=0
if [ "$producer_rc" -ne 0 ]; then final=1; echo "RUN_ALL_PIPELINE_FAIL side=producer (steps block) rc=$producer_rc" >&2; fi
if [ "$tee_rc" -ne 0 ]; then final=1; echo "RUN_ALL_PIPELINE_FAIL side=tee (log writer: could not write $LOG/run.log) rc=$tee_rc" >&2; fi
for pair in "_raw/retrieval_log.json:$LOG/retrieval_log.json" "_out/max_results.json:$LOG/max_results_fresh.json"; do
  src=${pair%%:*}; dst=${pair#*:}
  if [ -e "$src" ]; then cp "$src" "$dst" || { final=1; echo "RUN_ALL_COPY_FAIL $src -> $dst" >&2; }; fi
done
if [ "$final" -ne 0 ]; then
  # tee -a may itself fail (e.g. run.log is a directory); the stderr line is the guaranteed record
  echo "RUN_ALL_FAILED exit=$final (producer_rc=$producer_rc tee_rc=$tee_rc)" | tee -a "$LOG/run.log" >&2 || echo "RUN_ALL_FAILED exit=$final (producer_rc=$producer_rc tee_rc=$tee_rc)" >&2
fi
exit "$final"
