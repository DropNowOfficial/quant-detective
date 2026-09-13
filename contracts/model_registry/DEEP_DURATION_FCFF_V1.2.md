# Model Registry — DEEP_DURATION_FCFF_V1.2

**Status:** SIGNED_BY_SKEPTIC / NOT VALIDATED as forecast  
**Purpose:** Duration stress only — years of elevated tester economics embedded in today's EV. Not fair value / target price / buy-sell.

## Formula
`g_NOPAT = RR × iROIC + g_efficiency`  
`FCFF_t = NOPAT_t × (1 − RR)` for t=1..H  
Terminal after H: `g_term=3%`, `ROIC_term=r=10%` ⇒ `RR_term=30%`  
`EV = Σ FCFF_t/(1+r)^t + TV/(1+r)^H`

## Inputs
| Item | Source |
|---|---|
| EV Method A ≈ ¥22.66T | Ledger |
| FY26 OP ¥846B, CapEx guide ¥50B | Ledger |
| ETR ~25.9% → NOPAT ≈ ¥627B | **Labeled** from guide NI/PBT (not Ledger statutory) |
| r, g_term, ROIC_term | Labeled stress |

## Assumptions
- Path A: `RR = CapEx_guide/NOPAT ≈ 8.0%` — **PROXY only, NOT verified RR** (excludes ΔNWC; R&D guide ¥110B separate; maintenance vs growth unknown; partner capacity not on Advantest CapEx)
- Path B: `g_eff=0`, `RR=g/iROIC` for labeled iROIC
- Never: fix RR=10% and free-solve g without stating iROIC or g_eff

## Invariants
1. Every g publishes `(RR, iROIC, g_efficiency)` satisfying the identity  
2. Nonzero g_efficiency must name margin/utilization/mix  
3. RR>100% ⇒ financing flag  
4. Tax label vs Ledger tag  
5. No target price language

## Known limitations
Teradyne/own-ramp/margin reversion/customer concentration/test-intensity not endogenous; CapEx≠ΔIC; terminal cliff discrete.

## Validation
SIGNED_BY_SKEPTIC for framework/identity only. NOT VALIDATED as operating forecast. Cite only with full (g, RR, iROIC, g_eff) + PROXY/stress labels.

## Kill criteria
- Identity break (legacy V1.1 style)
- Dated supply catch-up before H without fade
- Scarcity margins assumed flat while own+TER ramp clears shortage

## Worked Path A (RR=`CapEx_guide/NOPAT`≈8.0% — **PROXY, not verified RR**)
| H | g | RR | iROIC if g_eff=0 | g_eff @ iROIC 50% | g_eff @ iROIC 100% | g_eff @ iROIC 150% |
|---|---|---|---|---|---|---|
| 2 | 93.0% | 8.0% | 1166% | 89.0% | 85.0% | 81.0% |
| 5 | 33.9% | 8.0% | 425% | 29.9% | 25.9% | 21.9% |
| 8 | 22.2% | 8.0% | 279% | 18.3% | 14.3% | 10.3% |

**Reading:** At funded CapEx RR, matching today's EV still requires either **implausibly high iROIC** (if all growth is reinvestment-funded) or **large explicit efficiency/margin growth** (g_eff). That is the honest duration tension — not a 22–93% CAGR with silent 10% RR.

## Legacy
V1.1 Advantest surface **KILLED** — RR≈10% + free g implied unstated iROIC 220–930%.
