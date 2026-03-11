FROM quay.io/crunchtools/ubi10-core:latest

LABEL maintainer="fatherlinux <scott.mccarty@crunchtools.com>"
LABEL description="UBI 10 Apache httpd layer — inherits troubleshooting tools and systemd hardening from ubi10-core"

# httpd is available in UBI repos — no RHSM needed
RUN dnf install -y \
      httpd \
    && dnf clean all

# Enable httpd
RUN systemctl enable httpd

EXPOSE 80
