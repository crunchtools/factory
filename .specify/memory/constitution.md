# factory Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-03-08
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.0.0
> **Profile:** Container Image

CrunchTools fleet watchdog — monitors GHA workflow status, version sync, artifact sync, and constitution compliance across all CrunchTools repos. Sends results to Zabbix via trapper protocol.

---

## License

AGPL-3.0-or-later

## Versioning

Follow Semantic Versioning 2.0.0. MAJOR/MINOR/PATCH.

## Base Image

`quay.io/hummingbird/python:3` — lightweight Python runtime. No systemd; uses entrypoint loop for periodic execution.

## Registry

Published to `quay.io/crunchtools/factory`.

## Containerfile Conventions

- Uses `Containerfile` (not Dockerfile)
- Required LABELs: `org.opencontainers.image.source`, `org.opencontainers.image.description`, `org.opencontainers.image.licenses`, `maintainer`
- Installs `gh` CLI binary via Python tarfile (Hummingbird lacks dnf/tar)
- Runs as non-root user (UID 1001)
- No RHSM registration needed (Hummingbird base, not UBI)

## Packages Installed

- gh CLI (GitHub CLI, installed from upstream binary release)
- Python 3 stdlib only (no pip dependencies)

## Runtime

- Entrypoint: `/usr/local/bin/entrypoint.sh` (sleep loop, default 900s interval)
- Main script: `/usr/local/bin/fleet-watchdog` (Python)
- Constitution validator: `/usr/local/lib/validate-constitution.py`
- Environment variables: `GH_TOKEN` (required), `ZABBIX_SERVER`, `ZABBIX_PORT`, `WATCHDOG_INTERVAL`

## Testing

- **Build test**: CI builds the Containerfile on every push to main
- **Smoke test**: Run container with `GH_TOKEN`, verify check output on stdout
- **Security scan**: Recommended (not yet implemented)

## Quality Gates

1. Build — CI builds the Containerfile successfully
2. Constitution validation — `validate-constitution.py` passes
3. Weekly rebuild — cron job picks up base image updates every Monday 6 AM UTC
