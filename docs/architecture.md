# Architecture (v0)

This is the preserved research-OS architecture, not a map of all current runtime processes. For the browser, research, VPS and GitHub entrypoints and their lifecycle boundaries, start with the [system map（中文）](system-map.zh-CN.md).

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
