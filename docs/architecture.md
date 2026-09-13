# Architecture (v0)

```
                 ┌─────────────────────────┐
                 │   Multi-agent protocol  │
                 │  Atlas / Ledger / Vector│
                 │  Skeptic (falsifiers)   │
                 └───────────┬─────────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │         Validation Kernel (gate)      │
         └───────────────────┬───────────────────┘
                             │
     ┌──────────────┬────────┴────────┬────────────────┐
     │              │                 │                │
┌────▼────┐  ┌──────▼──────┐  ┌───────▼──────┐  ┌─────▼─────┐
│Evidence │  │Expectations │  │  Bottleneck  │  │Time Machine│
│Contracts│  │Engine+Reg.  │  │Intelligence  │  │    Lab     │
└────┬────┘  └──────┬──────┘  └───────┬──────┘  └─────┬─────┘
     │              │                 │                │
     └──────────────┴────────┬────────┴────────────────┘
                             │
                    ┌────────▼────────┐
                    │ Failure Museum  │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │ ADAPT (optional, under gate)│
              │ OpenBB providers │ Qlib PIT │
              └─────────────────────────────┘
```

Primary evidence always flows Ledger → Evidence Contracts. OpenBB/Qlib never outrank Kernel/`known_at`.
