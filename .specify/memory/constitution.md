# factory Constitution

> **Version:** 1.1.0
> **Ratified:** 2026-03-09
> **Amended:** 2026-10-02
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.18.0
> **Profile:** Container Image

This file holds what is specific to factory. The fleet rules and the
Container Image profile apply at the inherited version and are checked against
this repo's files by `constitution.yml`. They are not restated here.

## Image Purpose

CrunchTools fleet watchdog. It auto-discovers every repo in the GitHub org
that carries a constitution and checks GHA workflow status, version and
artifact sync, constitution validation, changelog, the Gourmand gate, GitHub
Releases and open issues/PRs. Live service monitoring is Nagios's job;
factory does not duplicate it. Published to `quay.io/crunchtools/factory`.

## Status File Contract

Results are written to `/data/factory-status.json`. That file is both what
`factory-dashboard` renders and what Nagios alerts on: the host bind-mounts
the same directory and `check_factory_status.sh` reads its summary block
directly. If its schema changes, the Nagios check changes with it.

## Parent Image and Packages

- **Parent:** `quay.io/crunchtools/ubi10-core:latest` (systemd init), so this
  image rebuilds when the parent does.
- **Packages:** `python3` from UBI; `gh` from the upstream GitHub CLI RPM
  repository. The watchdog and dashboard are stdlib-only Python, no pip
  dependencies.
- **Vendored validator:** `validate-constitution.py` is copied to
  `/usr/local/lib/` and owned upstream in crunchtools/constitution; it is
  excluded from Gourmand.

## Services

| Unit | Kind | Role |
|------|------|------|
| `factory-watchdog.timer` | timer, every 15 minutes, persistent | fires the watchdog |
| `factory-watchdog.service` | oneshot | `/usr/local/bin/factory-watchdog` |
| `factory-dashboard.service` | simple, restart on failure | HTTP status page on port 8095 |

## Environment

`deploy/factory.env.example` documents the shape:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GH_TOKEN` | required | GitHub token with read access to org repos and packages |
| `GITHUB_ORG` | `crunchtools` | org to auto-discover repos from |
| `STATUS_FILE` | `/data/factory-status.json` | status file path |
| `DASHBOARD_PORT` | `8095` | dashboard listen port |

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-03-09 | Initial constitution |
| 1.1.0 | 2026-10-02 | Manifest under constitution v1.18.0: fleet and profile restatement removed; parent image corrected to `ubi10-core`; status file contract, units and environment kept |
