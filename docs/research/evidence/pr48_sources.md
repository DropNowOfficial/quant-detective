# PR #48 definition sources (what was and was not readable)

Retrieved 2026-10-11 (America/New_York) by Workstream A. Files were downloaded to the box
(`/workspace/src/`), NOT committed (copyrighted; AGENTS.md rule 6 spirit). Hashes below let a reviewer re-fetch and compare.

## Sloan (1996), The Accounting Review 71(3), 289-315 — FULL TEXT READ
* URL: https://www.cuhk.edu.hk/acy2/workshop/June2009Wasley/1996TAR).pdf  (JSTOR scan hosted by CUHK)
* sha256: 229b144840ad6ab30eebf299399e39e59ce2cab7c8f1aae018d9ca2a3946d2f6 (733,503 bytes); header reads "The Accounting Review, Vol. 71, No. 3. (Jul., 1996), pp. 289-315."
* Exact text read (accrual definition section):
  > "the accrual component of earnings is computed using information from the balance sheet and income statement, as is common in the earnings management literature (Dechow et al. 1995):
  > Accruals = (ΔCA - ΔCash) - (ΔCL - ΔSTD - ΔTP) - Dep
  > where ΔCA = change in current assets (Compustat item 4), ΔCash = change in cash/cash equivalents (item 1), ΔCL = change in current liabilities (item 5), ΔSTD = change in debt included in current liabilities (item 34), ΔTP = change in income taxes payable (item 71), and Dep = depreciation and amortization expense (item 14)."
* Denominator, exact text:
  > "The measure of firm size employed is total assets, measured as the average of the beginning and end of year book value of total assets (Compustat data item 6)."
  > Earnings = Income from Continuing Operations / Average Total Assets; Accrual Component = Accruals / Average Total Assets.
* Conclusions for code: denominator = average of beginning and end-of-year total assets (matches code); D&A is **depreciation and amortization**, subtracted (code field renamed `depreciation_amortization`). Sloan's own accruals are annual. Status: **VERIFIED**. Not verified: mapping of items 34/71 to XBRL/IFRS tags.

## Bali, Cakici & Whitelaw (2011), JFE 99(2), 427-446 — PUBLISHED TEXT NOT READ; working-paper version read
* The published JFE article (doi:10.1016/j.jfineco.2010.08.014) is paywalled; its full text was **not** fetched.
* Read instead: NYU Working Paper FIN-08-025 dated "August 2008", https://archive.nyu.edu/bitstream/2451/27871/2/wpa08025.pdf, sha256 2f0a7e958d6e72d4df9ffda6744d3f9f1618b52e6659113e50b2714bbc110d44 (205,959 bytes). Wording could differ from the published version.
* Exact text read:
  > "we sort stocks by their maximum daily return during the previous month" (Introduction)
  > "Portfolio 1 (low MAX) is the portfolio of stocks with the lowest maximum daily returns during the past month, and portfolio 10 (high MAX) ... during the previous month." (Section B)
  > Appendix: "MAX is the maximum daily return within a month: MAX_{i,t} = max(R_{i,d}), d = 1,...,D_t ... D_t is the number of trading days in month t."
* No minimum-observations rule was found in the text; `min_obs = 15` is a project choice.
* Conclusion: primary definition = max daily return over the previous calendar month (`prior_calendar_month`); fixed 21 trading days is NOT the paper's definition and is kept only as `fixed_21_trading_days`. Status: checked against working paper; published text UNVERIFIED.

## Novy-Marx (2013), JFE 108(1) — NOT READ
* Only the standard gross-profit-over-total-assets definition (user-confirmed) is used. Full text not fetched; revenue-minus-COGS as the gross-profit construction is UNVERIFIED against the paper.
