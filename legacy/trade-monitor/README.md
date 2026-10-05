# Preserved v0.1.1 stock screener and frozen daily event research

This directory preserves the previously delivered code, including full-row selection, keyboard focus, request-race protection, corrupt-response rejection and numeric CSV fixes. It does not contain market datasets or generated HTML with embedded prices.

From this directory, install `requirements.txt` in an isolated Python environment. Then run:

```bash
python -m unittest discover -s tests -q
python -m stock_screener serve --raw /absolute/path/to/authorized/ibkr
python -m stock_screener screen --raw /absolute/path/to/authorized/ibkr --as-of 2026-10-04T04:56:50Z
```

For the new portfolio/teaching research use the repository-root `research_lab` CLI and `docs/research-guide.zh-CN.md`.

Old `research/` scripts are preserved for provenance. They historically use a local `research/raw` directory; provide authorized snapshots there to rerun the frozen event study. No data download, broker order or reminder is performed by these modules. The original event study and the new finite-cash portfolio have different sample/portfolio definitions; their returns are not interchangeable.
