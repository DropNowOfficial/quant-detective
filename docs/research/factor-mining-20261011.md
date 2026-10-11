# Offline factor mining: first falsifiable primitives

Status: implementation research, not validated alpha. This branch starts from main `b18e62b`; it is independent of the operational repair PR #47 and external factor PR #48. No watcher, trading rule, live threshold, data ingestion, broker call or alert channel is changed.

## Small first slice

Use pure offline functions in `research_lab/factor_mining/` and synthetic tests. Reuse only the old offline framework's small contract ideas: UTC-aware event/availability/receipt separation, finite scalar values, explicit missingness and evidence lineage. Do not import its registry, news adapters, backtest engine or private datasets. The existing locked factor lifecycle remains authoritative: calculation validity is separate from `NEEDS BACKTEST`. Existing GP/A, Sloan and MAX work on another branch is outside this slice. A future adapter may map observations to a common contract after comparing semantics; no branch is declared canonical here.

Each value identifies its definition, units, entity, decision time, data vintage, source and license/usage identifier. Input evidence must be available by the decision timestamp. A current retrospective download is not a historical point-in-time vintage. Missing or future evidence returns an explicit blocked/missing result; it never becomes zero. Synthetic data is labeled synthetic and proves mechanics only.

## Liquidity: custom Amihud 20/60-session adaptations

For each declared completed session, compute absolute corporate-action-corrected decimal total return divided by raw traded close times raw share volume. Use raw USD dollar volume, not adjusted close times raw volume. Arithmetic mean over valid days is expressed in inverse USD; zero return with positive volume is a real zero. Nonpositive volume, invalid numbers, unknown action conventions, future/unlicensed evidence and missing sessions are not filled. Require at least 80% valid sessions of the explicitly supplied 20- or 60-session lattice; report count and coverage. The 80% rule and 20/60 windows are predeclared research choices, not literature-optimal constants. Signal timing is after the completed close; any eventual portfolio experiment must execute no earlier than the next tradable session.

Required ablation before an economic claim: reciprocal dollar volume alone; volatility, size, momentum and industry controls. Test price/volume split invariance, stale/zero-volume days, delisting histories, point-in-time membership and transaction costs. Higher raw values mean less liquidity, not an automatic buy/sell direction.

## Firm-specific component: unambiguous custom residual momentum

The first executable candidate is explicitly named daily one-step-ahead market/sector residual momentum. For day d, fit stock return on an intercept, market return and sector-minus-market return using the preceding 126 complete sessions ending d−1. Coefficients and benchmark selection are frozen at 00:00 UTC on the target session’s UTC date, a conservative pre-US-session boundary; a revision received later that day cannot enter that day’s fit. Compute the day-d forecast error using those coefficients. Score the last 20 forecast errors as their sum divided by sample standard deviation. Require all fitting/signal observations, aligned total-return inputs, a recorded sector benchmark/membership vintage and full-rank regressors. Zero residual variance or unavailable evidence produces no score. No contemporaneous factor file or synthetic price substitution is assumed.

This custom definition is not Jensen alpha and is not the paper's residual-momentum replication. The regression intercept is alpha; forecast error subtracts it. In the Blitz/Huij/Martens monthly paper baseline, the 36-month regression includes an intercept, but formation-period beta adjustment deliberately does not subtract estimated alpha. Its 11-month formation series therefore differs from this custom forecast-error series. Never sum a complete in-sample OLS residual window and call its approximately-zero mean alpha.

## Effective dimension: risk diagnostics only

For a fixed, versioned universe and a complete trailing window ending strictly before the decision timestamp, standardize return columns within the window and construct sample correlation. Eigenvalue shares produce entropy dimension, participation dimension, normalized dimensions and top-one share. Reject zero variance, changing/missing membership, future data, nonfinite matrices, material non-PSD inputs and window length no greater than universe size. Do not silently drop names or fill zeros. If SVD is used, covariance shares use squared singular values.

Identity correlation gives dimension N; perfectly identical series give dimension 1. Participation dimension is a transformation of mean squared off-diagonal correlation, so it is not independent evidence of alpha. Shrinkage is a separately named future method, not mixed into the transparent sample baseline. No profitable direction or collapse threshold is asserted.

## Data-gated hypotheses, not fabricated factors

- Analyst surprise and forecast revision need timestamped historical consensus with matching fiscal period, EPS basis, currency/unit and pre-event denominator. Otherwise return `BLOCKED_MISSING_PIT_CONSENSUS`. SEC seasonal EPS changes are a different proxy and must not inherit the surprise label. Actual-fact versions must follow period completion. Selection filters availability by decision time, then orders the eligible publications by their event/revision time; late receipt of an old filing cannot undo a newer restatement. Equal-publication conflicting versions fail closed.
- Executed repurchase cash, executed shares, authorization, capex cash and guidance revision remain separate event types. An authorization is not execution. YTD differencing requires comparable, then-available versions. Stated intent requires a public evidence span; unknown/mixed is allowed. No inferred insider motives or unstable buyback/capex ratio.
- Exposure connectivity needs versioned, directed, typed point-in-time edges with source, units, weight, effective interval, known time and verification time. Missing edges return `BLOCKED_MISSING_PIT_EDGES`; a correlation network or sector clique is not a supply-chain graph.

## Acceptance and rejection before economic use

These are calculation primitives, not a backtest claim. Before any factor is promoted, preregister universe/availability/formation/execution rules, training windows, holdout periods and rejection criteria. Fit normalizations and hyperparameters on training data only; preserve chronological out-of-sample folds and an untouched final holdout. Include delisted instruments and known-at membership, handle overlapping-label leakage, report turnover/net costs/spreads/capacity, and compare to simple placebo/baseline factors. Report redundancy/correlation and incremental ablations, not only standalone Sharpe. Multiple-testing adjustments and failed trials must remain visible. No historical PIT panel or licensed consensus/edge dataset is supplied by this implementation, so no alpha, capacity or deployability conclusion can pass this gate yet.

## Integration boundary

The research module has no import path into `market_data.us_watch` and does not alter operational freshness checks. PR #47's security fixes must be reviewed separately; this branch neither copies nor weakens them. The current main pytest lock is unchanged by this research patch; its dependency remediation belongs to that repair. Future common-contract integration must retain source licenses, first-seen/vintage timing, exact definitions and unavailable states.

See `factor-mining-sources-20261011.md` for primary references and source-use boundaries.

## Concrete live-shadow integration interface

`shadow.shadow_snapshot(observations, generated_at=...)` is the versioned JSON-ready output boundary. It preserves every observation's definition, value-or-null, timing, source/vintage/license metadata and calculation status. It never marks a factor eligible for official signals and explicitly says operational freshness is not verified. This is an interface, not a production integration.

A future read-only live adapter supplies `LiquidityDay` / `ReturnDay` records and an independent completed-session lattice, with caller-certified corporate-action conventions and latest known times for every combined input row. Residual rows include the exact market and sector series identities; benchmark membership is fixed before formation. Dimension input additionally carries a declared expected lattice and frozen-universe evidence. The adapter must reject missing entitlement, unverifiable actions, stale live inputs or ambiguous identities before shadow evaluation. It must retain current/revised receipt times rather than claim historical PIT availability.

A separate read-only holdings adapter could join these outputs by verified instrument identity for risk review; no account or holding data is included here. Initially write only a distinct shadow artifact/log, without importing this module into the official watcher or its Issue/Slack publisher. Reading actual market data and performing live shadow evaluation are valid next steps once those adapters and source/freshness checks are verified. Promoting a candidate to official signals needs the separate out-of-sample/economic gates above; placing orders is not part of this interface.
