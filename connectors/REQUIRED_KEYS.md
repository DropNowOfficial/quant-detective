# External connectors / API keys still required

| Connector | Purpose | Required for |
|---|---|---|
| GitHub (Cursor SCM) | Repo hosting / CI / CloudAgent | First remote commit |
| SEC EDGAR (public) | US primary filings | Evidence packs |
| cninfo / SSE / SZSE | A-share primary disclosures | Evidence packs |
| OpenBB (optional) | Secondary market data / MCP | connectors/openbb |
| OpenBB provider keys (varies) | Vendor feeds behind OpenBB | As enabled |
| Ken French / public factor CSV | US residual layer | Already used in sprint1 |
| China local factor/risk feed | CN residual layer | **Not available yet** |
| FactSet / Bloomberg (optional) | Consensus timestamps / dispersion | Assimilation tests |
| Exchange halt/limit flag feed | A-share Amihud diagnostics | Module A completeness |
| LLM provider (optional) | Agent orchestration only | Not for factual evidence |

Never treat social/LLM agreement as evidence.
