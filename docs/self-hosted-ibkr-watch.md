# Self-hosted IBKR market watcher

This is the production path for Quant Detective market monitoring. GitHub-hosted
public-data scanning remains the fallback.

## Architecture

The production process is a host service, not a five-day GitHub Actions job.

- **2-second fast path:** local IBKR Client Portal Gateway snapshots.
- **60-second structural path:** completed-RTH daily state, 5-minute VWAP,
  same-time RVOL and news from the existing public structural watcher.
- **Persistent state:** `/var/lib/quant-detective/state.json`.
- **Append-only transitions:** `/var/lib/quant-detective/events.jsonl`.
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
