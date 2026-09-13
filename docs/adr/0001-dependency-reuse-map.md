# ADR-0001: Dependency / Reuse Map for Quant Detective

**Status:** Proposed (aligns with Skeptic repo ADR gate)  
**Date:** 2026-09-13  
**Context:** Convert the research prototype into a repository-backed system. Inspect TradingAgents, Qlib, and OpenBB without blindly merging.

## Decision summary

| Upstream | Stance | Notes |
|---|---|---|
| TauricResearch/TradingAgents | **ADAPT** orchestration; **REJECT** investment logic | Do not vendor whole monorepo |
| microsoft/qlib | **ADAPT** PIT/backtest/risk engine under our contracts | Does not replace Time Machine / Kernel |
| OpenBB-finance/OpenBB | **ADAPT** provider / API / MCP layer | Not primary-evidence truth |
| Quant Detective | **BUILD** core research OS | See BUILD list below |

Hard reject: forking TradingAgents whole and “turning off” BUY/SELL later — logic leaks.

---

## TradingAgents evaluation

| Capability | Verdict | Action |
|---|---|---|
| Agent orchestration (LangGraph) | Useful engineering | **ADAPT** — isolate graph runner; no TA/Trader nodes |
| Structured outputs | Useful | **ADAPT** — JSON schemas for Evidence / Forecast / Falsifier |
| Checkpoint / resume | Useful | **ADAPT** — for long DEEP / Time Machine runs |
| Persistent decision logs | Useful pattern | **ADAPT** — as Failure Museum + lock blobs, not PnL reflectors |
| Data-access contracts / provider registry | Useful | **ADAPT** pattern; implement against OpenBB + primary packs |
| Provider abstraction | Useful | **ADAPT** via OpenBB + primary connectors |
| CI / tests | Useful | **REUSE** idea — pytest gates + kernel fixtures |
| Backtesting date fidelity / PIT fixes (v0.3+) | Partial overlap | **ADAPT** lessons; Time Machine owns protocol |
| Technical analyst / sentiment / social | Misaligned | **REJECT** |
| Trader BUY/SELL / SignalProcessor | Misaligned | **REJECT** |
| Reflector learning from PnL | Misaligned | **REJECT** |

## Qlib evaluation

| Capability | Verdict | Action |
|---|---|---|
| Point-in-Time database | Right engineering for revisions | **ADAPT** under `known_at` + Validation Kernel |
| Backtest / portfolio / risk | Useful engine | **ADAPT** for scoring eras; not alpha factory |
| Expression / DataHandlerLP | Leak risk if misconfigured | **ADAPT** only with Kernel PIT gates |
| China A-share data | Not free / not primary | Secondary; Ledger primary stays SEC/cninfo |
| ML alpha zoo / RD-Agent | Out of scope for v0 | **DO NOT ADOPT** now |

## OpenBB evaluation

| Capability | Verdict | Action |
|---|---|---|
| Connect-once provider layer + MCP/API | Fits connectors | **ADAPT** as secondary/market data plane |
| Equity/price/fundamentals convenience | Useful | Tag provider; never silent primary |
| Yahoo-class feeds | Secondary | Allowed with `source` + `known_at`; not Evidence Contract truth |
| Replacing SEC/cninfo / dps_status / 亿·B | No | **REJECT** as primary |

## Quant Detective BUILD (never import)

1. **Evidence Contracts** — pack objects, `known_at`, VERIFIED/PARTIAL/UNKNOWN/FAILED  
2. **Validation Kernel** — pre-Atlas gates (v0 exists)  
3. **Time Machine Lab** — PREDICT→LOCK→REVEAL→SCORE→DIAGNOSE→UPDATE→NEXT + eras  
4. **Expectations Engine** — reverse-DCF / duration surfaces (model registry)  
5. **Bottleneck Intelligence** — mosaic + state + assimilation (no score)  
6. **Failure Museum** — hashed lock failures + taxonomy exhibits  
7. **Multi-agent falsification** — Skeptic locks falsifiers, not forecasts  

## Consequences

- New greenfield repo `quant-detective` (GitHub once connected); optional later subtree of *stripped* TradingAgents modules only after a written strip list.  
- Qlib/OpenBB as optional extras behind connectors; Kernel remains authoritative.  
- No trading, no BUY/SELL, no TA factors in default graph.
