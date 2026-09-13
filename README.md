# Quant Detective

Research operating system for falsifiable equity investigation (US + China A-shares).

**Not a trading bot.** No BUY/SELL. No technical-analysis default graph.

## Owned modules (BUILD)

- Evidence Contracts
- Validation Kernel
- Time Machine Lab
- Expectations Engine (model registry)
- Bottleneck Intelligence
- Failure Museum
- Multi-agent falsification protocol

## Upstream stance

See `docs/adr/0001-dependency-reuse-map.md`.

Reviewed upstream commits (labels only, **not installed**):
`connectors/upstream_review_pins.json`

Runtime dependencies: `pyproject.toml` + `uv.lock` only.

## Bootstrap / verify

```bash
uv sync --frozen
make verify
```

Clean-room (fresh clone + fresh env):

```bash
make clean-room-check
```

## Connectors / keys still required

See `connectors/REQUIRED_KEYS.md`. Live keys are **not** required for `make verify`.
