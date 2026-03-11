# ubi10-httpd Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-03-10
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.0.0
> **Profile:** Container Image

UBI 10 Apache httpd layer. Inherits troubleshooting tools and systemd hardening from ubi10-core. Foundation for all web-serving CrunchTools images.

---

## License

AGPL-3.0-or-later

## Versioning

Follow Semantic Versioning 2.0.0. MAJOR/MINOR/PATCH.

## Base Image

`quay.io/crunchtools/ubi10-core:latest` — inherits troubleshooting tools (iputils, bind-utils, net-tools, less), cron, procps-ng, diffutils, and systemd hardening.

## Registry

Published to `quay.io/crunchtools/ubi10-httpd`.

## RHSM Registration

Not required. httpd is available in UBI repos.

## Containerfile Conventions

- Uses `Containerfile` (not Dockerfile)
- Required LABELs: `maintainer`, `description`
- `dnf install -y` followed by `dnf clean all`
- No RHSM registration needed
- systemd services enabled: httpd
- Inherits masked services from ubi10-core: systemd-remount-fs, systemd-update-done, systemd-udev-trigger
- Inherits `STOPSIGNAL SIGRTMIN+3` and `ENTRYPOINT ["/sbin/init"]` from ubi10-core

## Packages Installed

httpd

Inherited from ubi10-core: iputils, bind-utils, net-tools, less, cronie, procps-ng, diffutils

## Testing

- **Build test**: CI builds the image on every push to main/master
- **Smoke tests**: httpd active, serves content on port 80, inherited package integrity (7 core packages), inherited masked services (3)
- **Security scan**: Recommended (not yet implemented)

## Quality Gates

1. Build — CI builds the Containerfile successfully
2. Test — smoke tests pass (httpd up, serves content, inherited packages present, services masked)
3. Push — image published only after tests pass
4. Weekly rebuild — cron job picks up base image updates every Monday 4:15 AM UTC

## Downstream Images

ubi10-httpd-php, ubi10-httpd-perl, proxy (direct children). Changes cascade via repository_dispatch.
