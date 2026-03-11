FROM quay.io/crunchtools/ubi10-core:latest

LABEL org.opencontainers.image.source="https://github.com/crunchtools/factory"
LABEL org.opencontainers.image.description="CrunchTools factory watchdog"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"
LABEL maintainer="Scott McCarty <smccarty@redhat.com>"

# Install Python and gh CLI (both in UBI repos + upstream gh repo)
RUN dnf install -y --nodocs python3 && \
    dnf clean all

RUN dnf install -y --nodocs 'dnf-command(config-manager)' && \
    dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo && \
    dnf install -y --nodocs gh && \
    dnf clean all

COPY factory-watchdog.py /usr/local/bin/factory-watchdog
COPY factory-dashboard.py /usr/local/bin/factory-dashboard
COPY validate-constitution.py /usr/local/lib/validate-constitution.py
COPY factory-watchdog.service /etc/systemd/system/factory-watchdog.service
COPY factory-watchdog.timer /etc/systemd/system/factory-watchdog.timer
COPY factory-dashboard.service /etc/systemd/system/factory-dashboard.service

RUN chmod +x /usr/local/bin/factory-watchdog /usr/local/bin/factory-dashboard && \
    systemctl enable factory-watchdog.timer factory-dashboard.service
