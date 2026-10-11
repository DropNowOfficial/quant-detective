# Which code version produced which result (audit item 7)

Branch `research/new-factors-20261011-main-only`, draft PR #49. All SHAs are on this branch unless noted.
`git rev-parse HEAD:factor_candidates` is the content hash of the implementation directory; equal hashes mean identical code.

| Commit | Meaning | `factor_candidates` tree |
|---|---|---|
| `e14b256` | Real-data sample **v1** added (SEC 3×2, MAX prices). Its scripts (`extract.py`, `recompute.py`, `max_prices.py`) were **standalone, stdlib-only, written before the audit fixes and not importing repo code**. | (code of `8c894bd`, pre-audit) |
| `e793cba` | Branch head at the time of the user audit (pre-fix). | `9eeedaa1a02312063adf93af066b607dec4e6508` |
| `389245e` | Audit tests/probes added, **failing** (31 failed + 1 collection error). | unchanged (`9eeedaa…`) |
| `061bac3` | Code fixes: MAX explicit `calendar_tz`, Sloan cash + short-term investments, JSON-Schema contract gate. | `6ea006a98602cf0c026b562679eb95afa6798fc8` |
| `4c688c5` | Sample v2 (acceptance times, ST investments, AAPL tax note source, `fetch_all.py`/`verify_inputs.py`/`run_all.sh`). | `6ea006a…` |
| `556f20b` | MAX baseline regenerated from the ORIGINAL raw price files with the fixed code. **Code commit used for the end-to-end reproduction run** (`reproduce_run_20261011/run.log` records `git_head=556f20b…`, `factor_candidates_tree=6ea006a…`). | `6ea006a…` |
| later commits | Evidence/docs/PR only; they do not touch `factor_candidates/` (verified at the final head, see the PR description and the final report: `git diff 556f20b..HEAD -- factor_candidates` is empty). | `6ea006a…` |

## Result → producing version

| Result / report | Produced by | Status at the final head |
|---|---|---|
| v1 GP/A + Sloan numbers (`REPORT.md` @ e14b256; `results.json` @ e14b256 kept as `results_previous_cash_only_e14b256.json`) | v1 standalone `extract.py`/`recompute.py` @ e14b256, **cash-only ΔCash, raw acceptance field** | Superseded. GP/A values unchanged by the fixes. Sloan values changed (ST investments). |
| v1 acceptance times (`data_points.csv` @ e14b256) | v1 `extract.py`, trusted the submissions field | **Wrong for Apple (+4 h)**; superseded by v2. |
| v1 MAX values (`max_results.json` @ e14b256) | v1 `max_prices.py`: standalone code with exchange-timezone (ET) dates; **not** the buggy repo `max_lottery` | Re-computed with the fixed repo code; values identical (AAPL 0.035612, JNJ 0.020087, ADBE 0.053007; fixed-21 variants unchanged). |
| v2 `data_points.csv`, `results.json`, `recompute_output.txt`, `REPORT.md` | v2 scripts @ `4c688c5` (input data: 2026-10-11 downloads, hash-checked) | Current. Rebuilt byte-identically by `run_all.sh` @ `556f20b` (RESULTS_IDENTICAL_TO_COMMITTED). |
| `max_results.json`, `max_output.txt`, `daily_*.csv` | `max_prices.py` v2 with fixed repo code on the ORIGINAL raw files, run at `4c688c5`+clean tree (committed in `556f20b`). `_code_version.factor_or_sample_dirty: true` in that file is an artifact of the redirected `max_output.txt` being open during the run, not of code changes. | Current; reproduced from fresh downloads with exact equality. |
| `crosscheck_repo_code_results.json` | `crosscheck_repo_code.py` @ `556f20b` — repo `gross_profitability`/`sloan_accruals` vs standalone, agree to 1e-12 | Current. |
| `reproduce_run_20261011/` (`run.log`, `retrieval_log.json`, `max_results_fresh.json`) | `run_all.sh` @ `556f20b` | Current. `git_dirty_paths=1` in the log header is the `run.log` created by `tee` itself (the tree was clean, `git status` = 0 lines, immediately before). |
| Pre-audit test evidence `pr48_tests_after.txt`, `pr48_verify_after.txt` | PR #48 head (not this branch's code) | Historical only; not evidence for this branch. |
| `audit20261011_tests_before.txt` | `389245e` (tests only; code = e793cba code) | Failing-before evidence. |
| `audit20261011_tests_after.txt`, `audit20261011_verify_after.txt` | `556f20b` code | Passing-after evidence for the code. CI on the final head is reported separately (never reuse an older run). |

## Re-run on the final head
The sample was re-run end to end on the fixed code (`run_all.sh` @ `556f20b`; code tree identical at the final head). To re-run on any later head: `bash docs/research/real_data_sample_20261011/run_all.sh` — step 6 and `max_prices.py` print the head and `factor_candidates` tree hash they ran on.
