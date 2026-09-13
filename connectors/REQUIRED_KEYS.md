# Required connectors / API keys (runtime)

Reviewed upstream SHAs live in `upstream_review_pins.json` and are **not** installed.

Runtime deps are only `pyproject.toml` + `uv.lock`.

Still required for live research (not for `make verify` synthetic fixture):

| Connector | Purpose | Status |
|---|---|---|
| GitHub SCM | remote push + Actions | pending connect |
| SEC EDGAR / company filings | U.S. primary evidence | not wired |
| Exchange / company disclosures | A-share primary evidence | not wired |
| Market data (Yahoo etc.) | prices with source/timestamp | optional for research |
| OpenBB (optional ADAPT) | provider/MCP layer | not in lockfile |
| Qlib (optional ADAPT) | PIT/backtest engine | not in lockfile |

`make verify` must pass with **zero** of the above keys.
