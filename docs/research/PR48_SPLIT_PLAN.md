# PR #48 split plan (proposal only — no history rewritten, nothing merged)

Generated 2026-10-11 from `git log` / `git diff` / `gh pr view` against `origin/main` = b18e62b.

## Facts
* PR #48 (`research/new-factors-20261011` → **main**) was created from `fix/scan-security-20261011` (PR #47, **OPEN**, head 53e1ccb). Because #47 is not merged, #48's diff against main contains #47's two commits as well as the research commits (`gh pr view 48` lists 8 commits, files = union of the two sets below; `git diff origin/main 53e1ccb` = 39 files).
* Merge-base of main and 53e1ccb: b18e62b (current main tip) — #47 is a clean 2-commit stack on main.

## Inherited from #47 (NOT research; do not review in #48)
| commit | subject |
|---|---|
| 900c102 | Fix degraded scans and harden trusted publication and deployment |
| 53e1ccb | Simplify README to requested one-line description |

Files (`git diff --name-only origin/main 53e1ccb`, 39): `.github/workflows/deploy-self-hosted-market-watch.yml`, `.github/workflows/market-watch.yml`, `.gitignore`, `README.md`, `deploy/{healthcheck.py,repair-current.sh,runtime_health.py,update-production.sh,validate_release.py,watchdog.py}`, `docs/continuous-market-watch.md`, `docs/security-repair-20261010.md`, `market_data/{cli,github_alerts,github_fallback_dispatch,scan_health,scan_status,slack_alerts,smoke_check,transport,us_watch}.py`, `pyproject.toml` (pytest pin `>=9.0.3,<10`), `uv.lock`, and 15 `tests/test_*.py` files for market_data/deploy (alert_validity_integration, deploy_scripts, github_alerts, github_fallback_dispatch, provider_boundaries, quote_validity, release_validation, repair_regressions, review_regressions, scan_health, slack_alerts, slack_forwarding_workflow, smoke_transition_keys, update_transaction, us_watch, watch_session_guard).

## Research-only (belongs in #48)
| commit | meaning |
|---|---|
| 0bb7599 | docs: ranked new factor candidates (NOT_VALIDATED) |
| 627675c | factor_candidates reference impls + first tests; also adds `factor_candidates` to `pyproject.toml` wheel packages |
| 11d1c7d | contracts: candidate factor contracts |
| 0a2ae58 | tests: strengthened tests (fail on old code) |
| 0eee886 | PIT version selection, tz-aware known_at, filing basis, MAX prior-month primary, docs/contracts/sources |
| 80a7a8e | evidence: tests after fix + verify recipe |

Files (`git diff --name-only 53e1ccb HEAD`, 13): `contracts/factors/candidates_20261011.json`, `docs/research/NEW_FACTOR_CANDIDATES_20261011.md`, `docs/research/evidence/pr48_{sources.md,tests_before.txt,tests_after.txt,verify_after.txt}`, `factor_candidates/{__init__,accruals,common,gross_profitability,max_lottery}.py`, `pyproject.toml` (one line: adds `factor_candidates` to wheel packages), `tests/test_factor_candidates.py`. (This plan file is added after the list was taken.)

Overlap between the two sets: only `pyproject.toml` (different lines: #47 changes the pytest pin, research changes `[tool.hatch.build.targets.wheel] packages`) — no textual conflict.

## Proposed split (options; none executed on shared branches)
**Option A (recommended): research-only branch from main.** Cherry-pick the six research commits onto `origin/main` as a NEW branch. Done as a proposal: `research/new-factors-20261011-main-only` (local worktree; pushed only if verify passes — see report below). It applies without conflicts. Open it as the real PR only after reviewer agrees; then close/retarget #48 to avoid reviewing #47 twice. #48's branch and history are untouched, so nothing shared is rewritten.

**Option B: wait for #47 to merge.** Once #47 is merged to main, GitHub will show #48's diff as research-only (its first two commits become part of main; if #47 is squash-merged, the 2 inherited commits will still appear in #48 until #48 is rebased — rebase `research/new-factors-20261011` onto the new main **only with the owner's agreement**, because that rewrites #48's branch history).

**Option C: retarget #48 to `fix/scan-security-20261011`.** `gh pr edit 48 --base fix/scan-security-20261011` shows research-only diff immediately, but ties merge order to #47 (merge #47 first; GitHub then retargets to main automatically). No history change.

Not recommended: force-pushing or rebasing `fix/scan-security-20261011` or `research/new-factors-20261011` in place.

## Checks before any split goes live
1. `make verify` recipe on the split branch (the 6 recipe lines). 2. Confirm `uv.lock` unchanged by the research commits (it is: lock changes are inherited from #47 only). 3. `git diff origin/main..<branch> --stat` contains none of the 39 inherited files.

## Proposal branch result (2026-10-11)
`research/new-factors-20261011-main-only` = `origin/main` (b18e62b) + 6 cherry-picks (9ac993b, d912bf3, 09aebdf, 09b7fc8, 8c894bd, 20eb521). Cherry-picks applied with no conflicts. Verify recipe (6 lines, run in order, a fresh `uv sync --frozen`): all exit 0; pytest 1469 passed (includes the 127 factor_candidates tests); legacy unittest 84 OK; time_machine fixture OK. Branch is local only (not pushed, no PR opened). To publish it as a proposal: `git push origin research/new-factors-20261011-main-only`. Note its `docs/research/evidence/pr48_verify_after.txt` records the verify run done on the #48 branch, not on this branch.
