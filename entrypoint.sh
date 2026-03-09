#!/bin/bash
# Fleet watchdog entrypoint — runs checks every 15 minutes.
# Hummingbird images don't include systemd, so we use a simple loop.

set -euo pipefail

INTERVAL=${WATCHDOG_INTERVAL:-900}  # 15 minutes = 900 seconds

echo "Fleet watchdog starting (interval: ${INTERVAL}s)"

# Run immediately on start, then loop
while true; do
    echo ""
    echo "$(date -Iseconds) — Running fleet watchdog checks..."
    /usr/local/bin/fleet-watchdog || echo "WARN: fleet-watchdog exited with $?"
    echo "$(date -Iseconds) — Sleeping ${INTERVAL}s until next run..."
    sleep "$INTERVAL"
done
