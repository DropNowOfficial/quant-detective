# Quant Detective — agent operating rules

Durable rules for humans and agents. **Repo text beats chat memory.**

## Roles

| Role | Owns | Does not |
|------|------|----------|
| **LEDGER** | Factual financial inputs and source quality (U.S. + A-shares). Every important number carries `source` + `known_at` (and period/currency when relevant). | Guessing missing values; inventing prices or filings |
| **VECTOR** | Deterministic math, models, comparisons, backtests on Ledger inputs. Invariants live in code. | Certifying its own outputs as investment truth; inventing inputs |
| **SKEPTIC** | Material falsification: bad/stale data, leakage, inconsistent market assumptions, ignored evidence. May **reject**. | Alternative theses invented only to disagree |
| **ATLAS** | Final research conclusion; integrates only **after** validation. Distinguishes company quality from investment attractiveness. | Inventing missing data; trading actions; merging unvalidated Vector work |

## Hard rules

1. **Repo > chat memory** — locked contracts, ADRs, and this file override informal chat.
2. **source + known_at** — required on factual financial inputs; no anonymous numbers.
3. **missing ≠ zero** — absent data stays absent; never coerce to 0 or a placeholder “estimate.”
4. **Deterministic math/invariants live in code** — not in free-form prose alone.
5. **Vector cannot certify itself** — Atlas integrates only after validation; Skeptic may reject.
6. **No raw market datasets in Git** — manifests/hashes only; caches stay out of the repo.
7. **No trading actions** — research system only; never place, route, or automate trades.
8. **REQUIRED / REPORTED / EXPECTED stay separate** — screens (e.g. 10% hurdle) are not forecasts.
9. **Time Machine anti-leakage** — PREDICT → LOCK → REVEAL → SCORE; no look-ahead.

## Reproducibility labels

- **LOCAL_REPRODUCIBLE** — clean local `uv sync --frozen` + `make verify` passes.
- **REMOTE_REPRODUCIBLE** — fresh GitHub-hosted runner runs `make verify` and passes (via `.github/workflows/verify.yml`).
- Do not claim **PUBLIC_REPRODUCIBLE** unless that bar is separately met and labeled.

`make verify` is the single verification entrypoint. Do not duplicate its logic in CI YAML.
