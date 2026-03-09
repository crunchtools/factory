# factory Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-03-09
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

`registry.access.redhat.com/ubi10/ubi-init:latest` — systemd-based for timer-driven periodic execution.

## Registry

Published to `quay.io/crunchtools/factory`.

## Containerfile Conventions

- Uses `Containerfile` (not Dockerfile)
- Required LABELs: `org.opencontainers.image.source`, `org.opencontainers.image.description`, `org.opencontainers.image.licenses`, `maintainer`
- `dnf install -y --nodocs` followed by `dnf clean all`
- systemd timer enabled: fleet-watchdog.timer (15-minute OnCalendar)
- systemd services masked: systemd-remount-fs, systemd-update-done, systemd-udev-trigger
- `STOPSIGNAL SIGRTMIN+3` for proper systemd shutdown
- `ENTRYPOINT ["/sbin/init"]`

## Packages Installed

- python3 (from UBI repos)
- gh CLI (from upstream GitHub RPM repo)

## Runtime

- Init: `/sbin/init` (systemd)
- Timer: `fleet-watchdog.timer` (fires every 15 minutes)
- Service: `fleet-watchdog.service` (Type=oneshot)
- Main script: `/usr/local/bin/fleet-watchdog` (Python, stdlib only)
- Constitution validator: `/usr/local/lib/validate-constitution.py`
- Environment variables: `GH_TOKEN` (required), `ZABBIX_SERVER`, `ZABBIX_PORT`

## Testing

- **Build test**: CI builds the Containerfile on every push to main
- **Smoke test**: Run container with `GH_TOKEN`, verify timer fires and check output in journal
- **Security scan**: Recommended (not yet implemented)

## Quality Gates

1. Build — CI builds the Containerfile successfully
2. Constitution validation — `validate-constitution.py` passes
3. Weekly rebuild — cron job picks up base image updates every Monday 6 AM UTC
