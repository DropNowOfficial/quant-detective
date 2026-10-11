# Triage of the "107 failed" before-evidence (PR #48 / #49)

Honest summary: **most of the 107 failures are NOT demonstrations of old-code defects.** 105 of 107 are failures caused
by the new API/field names/timestamp types not existing yet (category c); only 2 are a behavioural "old code did not raise"
failure (a). One of the 105 (`test_accruals_no_lookahead`) also exposes a wrong test fixture (b overlay). `107 failed`
therefore overstates the evidence of defects. Real old-code defects ARE present, but they are shown by direct probes of the
old code through its own (date-based) API, not by the 107 count.

NOT_VALIDATED: this triage says nothing about factor validity.

## Method and commands
1. Old code: detached worktree at `11d1c7d` (old `factor_candidates`, identical to the code under test in
   `pr48_tests_before.txt`); tests-only commit's test file checked out on top:
   `git worktree add /workspace/qd-triage-old 11d1c7d --detach && cd /workspace/qd-triage-old && git checkout 0a2ae58 -- tests/test_factor_candidates.py`
2. `uv run --extra research pytest tests/test_factor_candidates.py -q --tb=short -p no:cacheprovider --junitxml=old.xml`
   -> reproduced **107 failed, 20 passed** (same as the recorded evidence).
3. `pr48_failure_triage_classify.py` parses the junit XML and classifies each failure from its exception:
   * (a) `DID NOT RAISE` or wrong-value AssertionError: the test reached the behaviour and old code behaved wrongly.
   * (c) `TypeError` (`date` vs tz-aware `datetime` comparison/subtraction; `unexpected keyword 'variant'`) or
     `AssertionError ... must be a declared field` (new field names absent): crash before behaviour is exercised.
4. Fixture check (b): final head tests with ONLY `CUR = (D(2025,12,31), T(2026,2,20,21,30))` reverted to
   `T(2026,2,20)` (the pre-`0eee886` fixture), run against final code:
   `uv run --extra research pytest tests/test_factor_candidates.py -q --tb=line -p no:cacheprovider -rf`
   -> **1 failed, 126 passed**; the single flip is `test_accruals_no_lookahead` (`assert -0.02 is None`): the fixture's
   known_at of 00:00Z made the filing visible at 21:29Z the same day while the test asserts it is invisible before 21:30Z,
   i.e. the fixture contradicted the test's own assertion.
5. Defect reality check on old code: `pr48_old_code_probes.py` (output in `pr48_old_code_probes_output.txt`), run in the
   old worktree with the old API.

## Counts (107 failures)
| category | count | what |
|---|---|---|
| (a) real old-code defect reached by the test | 2 | `test_gp_invalid_required_value_rejected[security-]` (empty security accepted), `test_gp_period_end_must_be_a_date_not_datetime` (datetime accepted as period_end) |
| (c) new API/fields/timestamp types did not exist yet | 105 | 90 TypeError date vs tz-aware datetime (old code is date-typed, tests use datetimes); 12 missing new field names (`period_type`, `currency`, `unit_scale`, `statement_ref`, `revision`, `adjustment_basis`); 3 `max_lottery(variant=...)` unexpected keyword |
| total | 107 | 2 + 105 |
| (b) overlay: fixture's own error | 1 of the 105 | `test_accruals_no_lookahead` fails in the old run mechanically (c) and would additionally fail on final code with the 00:00Z fixture; it is a (c) failure that also exposes a fixture error, not a separate failure |

Same 107 by what each test is *about* (intent, from test names; mechanism is still mostly (c)):
required-field/invalid-value validation 37; order/conflict/version selection 13; new fields/variant/semantics 13;
tz-aware/date-vs-datetime timestamps 12; late publication/revision/lookahead 8; basic formula/missing/stale tests rewritten
with new fixtures 22; duplicate trading days 2.

## Old-code defects that are real (direct probes, `pr48_old_code_probes_output.txt`)
| probe | result on old code | defect |
|---|---|---|
| P1 GP/A two records, same period_end & known_at, different values | 0.2 vs 0.5 depending on input order | order-dependence |
| P2 MAX same-day same-known_at conflict | 0.02 vs 0.10 depending on input order | order-dependence |
| P3 MAX with a duplicated trading day in the calendar | 0.01 vs clean 0.05 (window silently shrinks, drops a day) | duplicate days not detected |
| P4 date-only `known_at` | accepted | no time/timezone; same-day lookahead possible |
| P5 empty `security` | accepted | missing required value |
These confirm the defects were real, but the 107 count itself is not evidence of them: most tests fail on API shape, so they
would fail for any implementation not already written to the new signature. The meaningful before/after evidence is the
probes plus the (a) tests, and the after-fix tests (127 pass).

## Caveats
* Classification is by exception text of the old run; a (c) test may also encode a real defect that is masked by the crash.
* The "intent" buckets are keyword-based on test names, approximate.
* The fixture correction (21:30Z) lives in `0eee886`/(`8c894bd` here), not in the tests-only commit; the "tests-only"
  commit message claim that the tests then failed only on old code is therefore only approximately true.
