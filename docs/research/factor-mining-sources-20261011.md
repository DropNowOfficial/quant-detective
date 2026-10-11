# Primary references and source-use boundaries

Only original implementation code, formulas in our own notation, citations and synthetic test fixtures belong in this branch. No paper PDFs, private account records, analyst-consensus records or vendor market datasets are redistributed. A public URL does not grant a data redistribution license; actual data use needs a separately recorded source/license entitlement and vintage.

| Topic | Primary source | Scope of use |
| --- | --- | --- |
| Amihud illiquidity | Amihud (2002), [Journal of Financial Markets](https://www.sciencedirect.com/science/article/pii/S1386418101000246) | Daily absolute-return/dollar-volume formula; 20/60-day windows are custom adaptations |
| Volume explanation / placebo | Lou and Shu, [author manuscript](https://www.icmagroup.org/assets/documents/Regulatory/Secondary-markets/Bond-Market-Liquidity-Library/Lou-X_Shu-T---2016---Price-Impact-or-Trading-Volume-Why-is-the-Amihud-2002-Illiquidity-Measure-Priced-290118.pdf) | Motivates reciprocal-volume and other controls; not proof our implementation earns alpha |
| Residual momentum | Blitz, Huij and Martens (2011), [author paper](https://repub.eur.nl/pub/22252/ResidualMomentum-2011.pdf) | Distinguish paper's formation-period beta adjustment from custom forecast errors; estimated alpha exclusion is discussed on p.12 |
| Effective rank | Roy and Vetterli, [EPFL manuscript](https://infoscience.epfl.ch/bitstreams/2907ab8a-23f5-481d-bb07-1d56a3f3511f/download) | Entropy effective-rank concept; covariance dimensions use eigenvalue shares |
| Absorption/systemic fragility | Kritzman et al., [author paper](https://web.mit.edu/finlunch/Fall10/PCASystemicRisk.pdf) | Risk concentration context; fragility is not necessarily depreciation |
| Fundamental economic links | Cohen and Frazzini (2008), [author paper](https://pages.stern.nyu.edu/~afrazzin/pdf/Economic%20Links%20and%20Predictable%20Returns%20-%20Cohen%20and%20Frazzini.pdf) | Real relationship edges differ from statistical correlation; does not supply a current PIT graph |
| SEC API vintages | [SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Companyfacts require context/version/availability handling; frames' latest-filed values are not an automatic historical PIT panel |
| Repurchase disclosure | [SEC pre-amendment Item 703](https://www.sec.gov/files/corpfin/pre-amendment-item-703.pdf), [SEC rule-vacatur announcement](https://www.sec.gov/newsroom/whats-new/further-announcement-regarding-share-repurchase-disclosure-modernization-rule) | Keep authorization, periodic execution disclosure and publication time distinct |

Reused design provenance: the previously reviewed offline framework's `research_lab/factor_api/model.py` (SHA256 `4b22b693948c5d1143d4e9dc81bae2e3ce01c3e692a464326ebfc3307e699675`). Only its compact timing/validity contract principles are retained; its broader framework and data are not copied into this branch.
