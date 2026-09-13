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

See `docs/adr/0001-dependency-reuse-map.md`:

- TradingAgents → ADAPT orchestration; REJECT TA/BUY-SELL
- Qlib → ADAPT PIT/backtest under our Kernel
- OpenBB → ADAPT provider/MCP; not primary evidence

## Quick check

```bash
python -m validation_kernel.kernel_v0
python validation_kernel/vector_prepublish_v0.py
```

## Connectors / keys still required

See `connectors/REQUIRED_KEYS.md`.
