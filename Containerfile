FROM quay.io/hummingbird/python:3

LABEL org.opencontainers.image.source="https://github.com/crunchtools/factory"
LABEL org.opencontainers.image.description="CrunchTools fleet watchdog"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"
LABEL maintainer="Scott McCarty <smccarty@redhat.com>"

USER root

# Install gh CLI binary — Hummingbird lacks dnf/tar, so use Python for download+extract
RUN python3 -c "\
import urllib.request, json, platform, tarfile, shutil, os; \
arch = 'amd64' if platform.machine() == 'x86_64' else 'arm64'; \
data = json.loads(urllib.request.urlopen('https://api.github.com/repos/cli/cli/releases/latest').read()); \
url = [a['browser_download_url'] for a in data['assets'] if f'linux_{arch}.tar.gz' in a['name']][0]; \
urllib.request.urlretrieve(url, '/tmp/gh.tar.gz'); \
t = tarfile.open('/tmp/gh.tar.gz'); \
members = [m for m in t.getmembers() if m.name.endswith('/bin/gh')]; \
t.extract(members[0], '/tmp'); \
shutil.copy2('/tmp/' + members[0].name, '/usr/local/bin/gh'); \
os.chmod('/usr/local/bin/gh', 0o755); \
t.close(); \
" && rm -rf /tmp/gh*

COPY fleet-watchdog.py /usr/local/bin/fleet-watchdog
COPY validate-constitution.py /usr/local/lib/validate-constitution.py

RUN chmod +x /usr/local/bin/fleet-watchdog

# No systemd in Hummingbird — use simple entrypoint with sleep loop
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER 1001

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
