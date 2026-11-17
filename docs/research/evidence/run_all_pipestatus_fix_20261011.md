# run_all.sh pipeline status fix (audit 15)
Before (script at ddddf12): only `PIPESTATUS[0]` was read, so a failing `tee` (log write) was ignored.
Test evidence: `run_all_pipestatus_tests_before.txt` (3 failed, 5 passed: run.log-as-directory case exited 0).
Fix (see commit "fix (audit 15)"): `st=("${PIPESTATUS[@]}")` immediately after the pipeline; producer_rc and tee_rc both
checked (missing element = failure); failing side printed to stderr; copy failures also fail; final banner written to stderr
even if `tee -a run.log` fails.
After: `run_all_pipestatus_tests_after.txt` (8 passed), `run_all_pipestatus_verify_recipe_after.txt` (VERIFY_OK, pytest 1618 passed,
legacy 84), `run_all_pipestatus_e2e_rerun.txt` + `real_data_sample_20261011/reproduce_run_audit15/` (end-to-end exit 0).
Note: the e2e run rewrote crosscheck_repo_code_results.json git_head only (factor_candidates tree unchanged); that change was reverted.
Historical price CSVs in Git history are untouched pending license / deletion-requirement check; #48 and other refs hold copies too.
