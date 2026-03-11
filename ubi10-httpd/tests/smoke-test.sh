#!/bin/bash
# smoke-test.sh — smoke tests for ubi10-httpd container image
# Run inside a running container started with --systemd=always
# Exit 0 = all pass, Exit 1 = one or more failures

set -uo pipefail

FAILURES=0
TESTS=0

pass() {
    TESTS=$((TESTS + 1))
    echo "  PASS: $1"
}

fail() {
    TESTS=$((TESTS + 1))
    FAILURES=$((FAILURES + 1))
    echo "  FAIL: $1"
}

# ---------- Service Health ----------
echo "=== Service Health ==="

if systemctl is-active httpd >/dev/null 2>&1; then
    pass "httpd is active"
else
    fail "httpd is not active"
fi

# ---------- Functional Tests ----------
echo "=== Functional Tests ==="

# HTTP response from Apache default page
RESPONSE_FILE=$(mktemp)
HTTP_OK=false
for i in $(seq 1 10); do
    if php -r "echo @file_get_contents('http://localhost/') ?: '';" > "$RESPONSE_FILE" 2>/dev/null; then
        if [ -s "$RESPONSE_FILE" ]; then
            HTTP_OK=true
            break
        fi
    fi
    # Fallback: use curl if available, otherwise try wget or python
    if command -v curl >/dev/null 2>&1; then
        curl -sf http://localhost/ > "$RESPONSE_FILE" 2>/dev/null && HTTP_OK=true && break
    fi
    # Last resort: check via netstat that httpd is listening
    if netstat -tlnp 2>/dev/null | grep -q ":80 "; then
        # Port is open, try a raw connection
        (echo -e "GET / HTTP/1.0\r\nHost: localhost\r\n\r\n" | timeout 5 bash -c 'exec 3<>/dev/tcp/localhost/80; cat >&3; cat <&3' > "$RESPONSE_FILE" 2>/dev/null) || true
        if [ -s "$RESPONSE_FILE" ]; then
            HTTP_OK=true
            break
        fi
    fi
    sleep 1
done
if $HTTP_OK; then
    pass "httpd serves content on port 80"
else
    fail "httpd did not serve content on port 80"
fi
rm -f "$RESPONSE_FILE"

# ---------- Package Integrity ----------
echo "=== Package Integrity ==="

if rpm -q httpd >/dev/null 2>&1; then
    pass "package: httpd"
else
    fail "package missing: httpd"
fi

# ---------- Inherited from ubi10-core ----------
echo "=== Inherited (ubi10-core) ==="

# Verify core packages still present
CORE_PACKAGES=(iputils bind-utils net-tools less cronie procps-ng diffutils)
for pkg in "${CORE_PACKAGES[@]}"; do
    if rpm -q "$pkg" >/dev/null 2>&1; then
        pass "inherited package: $pkg"
    else
        fail "inherited package missing: $pkg"
    fi
done

# Verify masked services
for svc in systemd-remount-fs systemd-update-done systemd-udev-trigger; do
    if systemctl is-enabled "$svc" 2>/dev/null | grep -q "masked"; then
        pass "inherited mask: $svc"
    else
        fail "inherited mask missing: $svc"
    fi
done

# ---------- Summary ----------
echo ""
echo "=== Results: $((TESTS - FAILURES))/$TESTS passed ==="

if [ "$FAILURES" -gt 0 ]; then
    echo "$FAILURES test(s) failed"
    exit 1
fi

echo "All tests passed"
exit 0
