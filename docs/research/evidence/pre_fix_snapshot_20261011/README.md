# Pre-fix snapshot (before-state of the verification pipeline)
Taken from commit e259a4e (PR #49 head before the audit-14 fixes). Contains run_all.sh, verify_inputs.py,
reproduce_run_20261011/run.log and the tracked contents of real_data_sample_20261011 (copied under real_data_sample/).
The three price CSVs (daily_*.csv) are NOT copied (AGENTS.md rule 6); only their SHA256/line counts are listed in
price_csv_sha256_at_e259a4e.txt. The CSVs remain retrievable from git history at e259a4e:
  git show e259a4e:docs/research/real_data_sample_20261011/daily_AAPL.csv
Known defects at this state: run_all.sh exit code taken from PIPESTATUS after a later cp (false success);
later_restatement not time-filtered; stale counts (148/160/120/112); price CSVs tracked.
