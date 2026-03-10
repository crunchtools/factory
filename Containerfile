FROM registry.access.redhat.com/ubi10/ubi-init:latest

LABEL org.opencontainers.image.source="https://github.com/crunchtools/factory"
LABEL org.opencontainers.image.description="CrunchTools factory watchdog"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"
LABEL maintainer="Scott McCarty <smccarty@redhat.com>"

# Install Python and gh CLI
RUN dnf install -y --nodocs python3 && \
    dnf clean all

# gh CLI from upstream RPM repo
RUN dnf install -y --nodocs 'dnf-command(config-manager)' && \
    dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo && \
    dnf install -y --nodocs gh && \
    dnf clean all

COPY factory-watchdog.py /usr/local/bin/factory-watchdog
COPY validate-constitution.py /usr/local/lib/validate-constitution.py
COPY factory-watchdog.service /etc/systemd/system/factory-watchdog.service
COPY factory-watchdog.timer /etc/systemd/system/factory-watchdog.timer

RUN chmod +x /usr/local/bin/factory-watchdog && \
    systemctl enable factory-watchdog.timer

# Mask unnecessary systemd units for container use
RUN systemctl mask systemd-remount-fs.service systemd-update-done.service \
    systemd-udev-trigger.service

STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/sbin/init"]
