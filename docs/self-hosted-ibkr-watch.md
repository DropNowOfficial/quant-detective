# Self-hosted IBKR market watcher

[System map / 中文入口](system-map.zh-CN.md)

This is the intended persistent host-service path for Quant Detective market monitoring. GitHub-hosted public-data scanning is a separate fallback. This document describes repository configuration, not proof that any particular host has installed or is running it.

## Architecture

The production process is a host service, not a five-day GitHub Actions job.

- **2-second fast path:** local IBKR Client Portal Gateway snapshots.
- **60-second structural path:** completed-RTH daily state, 5-minute VWAP,
  same-time RVOL and news from the existing public structural watcher.
- **Persistent state:** `/var/lib/quant-detective/state.json`.
- **Append-only transitions:** `/var/lib/quant-detective/events.jsonl`.
- **Delivery boundary:** the current `ibkr-watch` daemon writes those files and console logs. It does not call the GitHub publisher or prove that a person received a notification. The hosted `watch --github-alerts` path performs its own scan.
- **Scope:** the example environment contains a 41-symbol core watchlist. The current daemon also includes [IBKR U.S. major-market Top-N discovery](../market_data/market_discovery.py); by default up to 24 discovery candidates are considered for stock-type filtering and subsequent structural analysis. It explicitly reports `discovery_exhaustive=false`, not exhaustive full-market coverage. Actual discovery availability and scope must be checked on the host.
- **Explicit degradation:** if IBKR authentication/session/quotes fail, mode is
  `DEGRADED_PUBLIC_ONLY`. The service never relabels public Yahoo data as IBKR.

No order API is implemented in `ibkr_cpg.py`.

## Important IBKR limitation

Client Portal Gateway must run on the same machine as the API client and needs an
authenticated brokerage session. It is suitable for a dedicated desktop/server
where that local authenticated gateway is maintained. It is not a zero-touch
cloud authentication mechanism.

For a fully unattended VPS, the next provider adapter should use an IB Gateway /
TWS API session on that VPS. The daemon interface is provider-neutral so the
fast-path adapter can be replaced without changing the structural rules.

## Host layout

- code releases: `/opt/quant-detective/releases/<sha>`
- active symlink: `/opt/quant-detective/current`
- config: `/etc/quant-detective/market-watch.env`
- state/events: `/var/lib/quant-detective/`
- service: `quant-detective-live.service`

## First install

On the Linux host:

```bash
sudo mkdir -p /opt/quant-detective/current
# put a checked-out repository at /opt/quant-detective/current for the first install
sudo deploy/install-systemd.sh /opt/quant-detective/current
```

Review `/etc/quant-detective/market-watch.env`. Do not put IBKR credentials in
GitHub or in this repository.

Start/authenticate the local Client Portal Gateway, then:

```bash
sudo systemctl restart quant-detective-live
sudo systemctl status quant-detective-live --no-pager
python /opt/quant-detective/current/deploy/healthcheck.py
```

## Self-hosted GitHub runner

Register a Linux x64 self-hosted runner with label:

`quant-detective`

The runner is a deploy agent only. The workflow installs a release, moves the
`current` symlink, restarts systemd and performs a health check.

The runner user needs narrowly scoped passwordless sudo for:

- `install`, `rsync`, `chown`, `ln`
- `systemctl daemon-reload`
- `systemctl restart/status quant-detective-live.service`
- running `uv sync` as the `quantdetective` service user

Do not grant unrestricted passwordless root.

## Fast-path state semantics

IBKR snapshots can promote discovery to `LEADER_WATCH` or
`LEADER_HOT_NO_CHASE` immediately. They do not create an entry signal by
themselves.

`ENTRY_CONFIRMED` remains dependent on the slower completed-bar structural
state. A live IBKR snapshot can keep that state valid only when price still
holds the completed structural VWAP/MA5 geometry.

This preserves the rule: faster price data improves detection latency; it does
not weaken the entry gate.


## Automatic VPS deployment

For the current DigitalOcean host, the recommended deployment path does not
require a GitHub self-hosted runner.

One-time install:

```bash
curl -fsSL https://raw.githubusercontent.com/DropNowOfficial/quant-detective/main/deploy/install-autoupdate.sh | bash
```

This installs a root-owned systemd timer. The current [timer unit](../deploy/systemd/quant-detective-update.timer) uses `OnBootSec=30s`, `OnUnitInactiveSec=60s`, `AccuracySec=5s` and `RandomizedDelaySec=5s`. The 60-second interval starts after the previous update service becomes inactive; it is neither a strict wall-clock cadence nor proof of the installed host configuration. It checks the exact SHA of `main`. If the SHA changed, it:

1. clones the new commit into a separate immutable release directory;
2. creates its own virtual environment;
3. installs the package;
4. runs the IBKR/watcher regression tests;
5. switches `/opt/quant-detective/current` atomically;
6. restarts the watcher;
7. waits for a fresh non-BOOTSTRAPPING health state;
8. rolls the symlink back to the prior release if health verification fails.

The updater never needs a global Git `safe.directory=*` exception. Existing
release repositories are queried as the `quantdetective` owner instead of
running Git as root against another user's repository.

## Verify the installed host separately

Use read-only inspection before treating repository changes as deployed:

```bash
readlink -f /opt/quant-detective/current
systemctl status quant-detective-live.service --no-pager
systemctl cat quant-detective-update.timer
systemctl list-timers --all --no-pager
python /opt/quant-detective/current/deploy/healthcheck.py
```

Compare the active release SHA with the intended commit; inspect the effective unit including overrides. Check state freshness, `mode`, `ibkr_error`, structural-data age and per-symbol errors. Also inspect `discovery_current`, `discovery_scope`, `discovery_error` and candidate counts: discovery merged into code does not prove a live authenticated scanner is running. `DEGRADED_PUBLIC_ONLY` means public fallback, not authenticated IBKR quotes. The repository also contains watchdog service/timer definitions; verify that the actual host has loaded them rather than assuming a merge enabled them.

A passing local health check is not an end-to-end notification test. Check GitHub run/heartbeat and any receiving channel separately. Do not restart, redeploy, change timers or install a gateway merely to inspect status.
