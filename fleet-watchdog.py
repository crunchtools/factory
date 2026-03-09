#!/usr/bin/env python3
"""CrunchTools fleet watchdog — checks repos, sends results to Zabbix trapper.

Monitors 14 repos across 5 dimensions:
  1. GHA workflow status (all repos)
  2. Version sync across pyproject.toml / __init__.py / server.py (MCP repos)
  3. Artifact sync across GitHub release / PyPI / Quay.io / GHCR (MCP repos)
  4. Constitution validation (all repos with constitutions)
  5. Open GitHub issues (all repos)

Results are pushed to Zabbix via the trapper (ZBXD) protocol.

No pip dependencies — stdlib only + gh CLI.
"""

import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ZABBIX_HOST = "factory.crunchtools.com"
ZABBIX_SERVER = os.environ.get("ZABBIX_SERVER", "127.0.0.1")
ZABBIX_PORT = int(os.environ.get("ZABBIX_PORT", "10051"))

# All 14 repos to monitor
REPOS = [
    "mcp-cloudflare",
    "mcp-feed-reader",
    "mcp-gemini",
    "mcp-gitlab",
    "mcp-google-search-console",
    "mcp-mediawiki",
    "mcp-request-tracker",
    "mcp-wordpress",
    "mcp-workboard",
    "constitution",
    "find-mcp-server",
    "openclaw",
    "ubi10-httpd-perl",
    "ubi10-httpd-php",
]

# Subset: MCP server repos (version sync + artifact sync apply)
MCP_REPOS = [
    "mcp-cloudflare",
    "mcp-feed-reader",
    "mcp-gemini",
    "mcp-gitlab",
    "mcp-google-search-console",
    "mcp-mediawiki",
    "mcp-request-tracker",
    "mcp-wordpress",
    "mcp-workboard",
]

# PyPI package names follow the pattern mcp-{name}-crunchtools
# e.g. mcp-cloudflare -> mcp-cloudflare-crunchtools
PYPI_PREFIX = "mcp-"
PYPI_SUFFIX = "-crunchtools"

VALIDATOR_PATH = "/usr/local/lib/validate-constitution.py"


# ---------------------------------------------------------------------------
# GitHub helpers (via gh CLI)
# ---------------------------------------------------------------------------

def gh_api(endpoint: str) -> dict | list | None:
    """Call the GitHub API via gh CLI. Returns parsed JSON or None on error."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def gh_file_content(repo: str, path: str) -> str | None:
    """Fetch a file from GitHub and return its decoded text content."""
    data = gh_api(f"repos/crunchtools/{repo}/contents/{path}")
    if not data or "content" not in data:
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Check 1: GHA Workflow Status
# ---------------------------------------------------------------------------

def check_gha_status(repo: str) -> int:
    """Return 1 if all latest workflow runs on main are green, 0 otherwise."""
    data = gh_api(
        f"repos/crunchtools/{repo}/actions/runs?branch=main&per_page=10"
    )
    if not data or "workflow_runs" not in data:
        return 0

    runs = data["workflow_runs"]
    if not runs:
        # No runs at all — not necessarily a failure, but nothing to check
        return 1

    # Group by workflow_id, take latest run per workflow
    latest_by_workflow: dict[int, dict] = {}
    for run in runs:
        wf_id = run.get("workflow_id")
        if wf_id and wf_id not in latest_by_workflow:
            latest_by_workflow[wf_id] = run

    # Check that all latest runs concluded successfully
    for run in latest_by_workflow.values():
        conclusion = run.get("conclusion")
        if conclusion not in ("success", "skipped"):
            return 0

    return 1


# ---------------------------------------------------------------------------
# Check 2: Version Sync (MCP repos only)
# ---------------------------------------------------------------------------

def extract_version_pyproject(text: str) -> str | None:
    """Extract version from pyproject.toml."""
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def extract_version_init(text: str) -> str | None:
    """Extract __version__ from __init__.py."""
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def extract_version_server(text: str) -> str | None:
    """Extract version from server.py (typically in mcp.name() or similar)."""
    # Try version= in mcp.name() or mcp() call
    match = re.search(r'version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def check_version_sync(repo: str) -> tuple[int, str]:
    """Return (1, version) if all version sources match, (0, details) otherwise."""
    # Module name: mcp-cloudflare -> mcp_cloudflare_crunchtools
    module = repo.replace("-", "_") + "_crunchtools"

    pyproject = gh_file_content(repo, "pyproject.toml")
    init_py = gh_file_content(repo, f"src/{module}/__init__.py")
    server_py = gh_file_content(repo, f"src/{module}/server.py")

    versions: dict[str, str | None] = {}
    if pyproject:
        versions["pyproject.toml"] = extract_version_pyproject(pyproject)
    if init_py:
        versions["__init__.py"] = extract_version_init(init_py)
    if server_py:
        versions["server.py"] = extract_version_server(server_py)

    if not versions:
        return 0, "no version files found"

    found = {k: v for k, v in versions.items() if v is not None}
    if not found:
        return 0, "no versions extracted"

    unique = set(found.values())
    if len(unique) == 1:
        ver = next(iter(unique))
        return 1, ver
    else:
        detail = ", ".join(f"{k}={v}" for k, v in found.items())
        return 0, f"mismatch: {detail}"


# ---------------------------------------------------------------------------
# Check 3: Artifact Sync (MCP repos only)
# ---------------------------------------------------------------------------

SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def is_semver_tag(name: str) -> bool:
    """Return True if name looks like a semver version (with or without v prefix)."""
    return bool(SEMVER_RE.match(name.lstrip("v")))


def get_github_release_version(repo: str) -> str | None:
    """Get the latest version — try GitHub Release first, fall back to git tag."""
    data = gh_api(f"repos/crunchtools/{repo}/releases/latest")
    if data and "tag_name" in data:
        tag = data["tag_name"]
        return tag.lstrip("v") if tag else None

    # No releases — fall back to latest semver git tag
    tags = gh_api(f"repos/crunchtools/{repo}/tags?per_page=10")
    if tags and isinstance(tags, list):
        for tag in tags:
            name = tag.get("name", "")
            if is_semver_tag(name):
                return name.lstrip("v")
    return None


def get_pypi_version(repo: str) -> str | None:
    """Get the latest version from PyPI."""
    package = f"{PYPI_PREFIX}{repo.removeprefix('mcp-')}{PYPI_SUFFIX}"
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("info", {}).get("version")
    except Exception:
        return None


def get_quay_latest_tag(repo: str) -> str | None:
    """Get the latest 3-part semver tag from Quay.io (skip 'latest', 'main', short tags)."""
    url = f"https://quay.io/api/v1/repository/crunchtools/{repo}/tag/?limit=20&onlyActiveTags=true"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            tags = data.get("tags", [])
            # Prefer 3-part semver (X.Y.Z) over short (X.Y)
            for tag in tags:
                name = tag.get("name", "").lstrip("v")
                if re.match(r"^\d+\.\d+\.\d+$", name):
                    return name
            # Fall back to 2-part if no 3-part found
            for tag in tags:
                name = tag.get("name", "").lstrip("v")
                if re.match(r"^\d+\.\d+$", name):
                    return name
            return None
    except Exception:
        return None


def get_ghcr_latest_tag(repo: str) -> str | None:
    """Get the latest 3-part semver tag from GHCR via GitHub API.

    Prefers 3-part (X.Y.Z) over 2-part (X.Y) tags since both may exist
    on the same image and we need to match the full version elsewhere.
    """
    data = gh_api(
        f"orgs/crunchtools/packages/container/{repo}/versions?per_page=10"
    )
    if not data or not isinstance(data, list):
        return None
    for version in data:
        tags = version.get("metadata", {}).get("container", {}).get("tags", [])
        for tag in tags:
            name = tag.lstrip("v")
            if re.match(r"^\d+\.\d+\.\d+$", name):
                return name
    for version in data:
        tags = version.get("metadata", {}).get("container", {}).get("tags", [])
        for tag in tags:
            name = tag.lstrip("v")
            if re.match(r"^\d+\.\d+$", name):
                return name
    return None


def check_artifact_sync(repo: str) -> tuple[int, str]:
    """Return (1, summary) if versions match, (0, details) otherwise.

    Compares GitHub release/tag, PyPI, and Quay.io. GHCR is included only
    if it has a semver tag (many repos only push 'latest'+'main' to GHCR).
    """
    gh_ver = get_github_release_version(repo)
    pypi_ver = get_pypi_version(repo)
    quay_ver = get_quay_latest_tag(repo)
    ghcr_ver = get_ghcr_latest_tag(repo)

    # Core trio: GitHub + PyPI + Quay must all match
    versions: dict[str, str | None] = {
        "github": gh_ver,
        "pypi": pypi_ver,
        "quay": quay_ver,
    }
    # Include GHCR only if it has a real semver tag
    if ghcr_ver:
        versions["ghcr"] = ghcr_ver

    found = {k: v for k, v in versions.items() if v is not None}
    if not found:
        return 0, "no versions found"

    unique = set(found.values())
    detail = ", ".join(f"{k}={v}" for k, v in found.items())
    if len(unique) == 1:
        return 1, next(iter(unique))
    else:
        return 0, f"mismatch: {detail}"


# ---------------------------------------------------------------------------
# Check 4: Constitution Validation
# ---------------------------------------------------------------------------

def check_constitution(repo: str) -> tuple[int, str]:
    """Validate constitution via validate-constitution.py. Returns (score, violations)."""
    content = gh_file_content(repo, ".specify/memory/constitution.md")
    if content is None:
        return 1, ""  # No constitution = not applicable, not a failure

    # Write to temp file and run validator
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["python3", VALIDATOR_PATH, tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return 1, ""
        else:
            # Extract violation lines
            violations = result.stdout.strip()
            return 0, violations
    except subprocess.TimeoutExpired:
        return 0, "validator timed out"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Check 5: Open GitHub Issues
# ---------------------------------------------------------------------------

def check_open_issues(repo: str) -> int:
    """Return count of open issues for a repo."""
    data = gh_api(
        f"repos/crunchtools/{repo}/issues?state=open&per_page=100"
    )
    if not data or not isinstance(data, list):
        return 0
    # GitHub API returns PRs in the issues endpoint — filter them out
    return sum(1 for issue in data if "pull_request" not in issue)


# ---------------------------------------------------------------------------
# Zabbix trapper protocol
# ---------------------------------------------------------------------------

def send_trapper(items: list[dict[str, str]]) -> bool:
    """Send items to Zabbix via the trapper (ZBXD) binary protocol.

    Each item dict has keys: host, key, value.
    Protocol: ZBXD\\x01 + 4-byte LE data length + 4-byte LE reserved (0)
    """
    payload = json.dumps({
        "request": "sender data",
        "data": items,
    }).encode("utf-8")

    # Header: ZBXD\x01 + 4-byte data length (LE) + 4-byte reserved (LE)
    header = b"ZBXD\x01" + struct.pack("<II", len(payload), 0)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect((ZABBIX_SERVER, ZABBIX_PORT))
            sock.sendall(header + payload)

            # Read response
            resp_header = sock.recv(13)
            if len(resp_header) < 13:
                print("WARN: Short response from Zabbix", file=sys.stderr)
                return False
            resp_len = struct.unpack("<I", resp_header[5:9])[0]
            resp_body = sock.recv(resp_len)
            resp = json.loads(resp_body)
            info = resp.get("info", "")
            print(f"Zabbix response: {info}")
            return "failed: 0" in info
    except Exception as e:
        print(f"ERROR sending to Zabbix: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    trapper_items: list[dict[str, str]] = []

    def add_item(key: str, value: str | int) -> None:
        trapper_items.append({
            "host": ZABBIX_HOST,
            "key": key,
            "value": str(value),
        })

    print("=" * 60)
    print("CrunchTools Fleet Watchdog")
    print("=" * 60)

    # --- Check 1: GHA Status (all repos) ---
    print("\n--- GHA Workflow Status ---")
    for repo in REPOS:
        score = check_gha_status(repo)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {repo}: {status}")
        add_item(f"fleet.gha[{repo}]", score)

    # --- Check 2: Version Sync (MCP repos only) ---
    print("\n--- Version Sync ---")
    for repo in MCP_REPOS:
        score, version_info = check_version_sync(repo)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {repo}: {status} ({version_info})")
        add_item(f"fleet.version.sync[{repo}]", score)
        add_item(f"fleet.version[{repo}]", version_info)

    # --- Check 3: Artifact Sync (MCP repos only) ---
    print("\n--- Artifact Sync ---")
    for repo in MCP_REPOS:
        score, artifact_info = check_artifact_sync(repo)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {repo}: {status} ({artifact_info})")
        add_item(f"fleet.artifact.sync[{repo}]", score)

    # --- Check 4: Constitution Validation (all repos) ---
    print("\n--- Constitution Validation ---")
    for repo in REPOS:
        score, violations = check_constitution(repo)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {repo}: {status}")
        if violations:
            print(f"    {violations[:200]}")
        add_item(f"fleet.constitution[{repo}]", score)
        add_item(f"fleet.constitution.violations[{repo}]", violations)

    # --- Check 5: Open Issues (all repos) ---
    print("\n--- Open Issues ---")
    for repo in REPOS:
        count = check_open_issues(repo)
        print(f"  {repo}: {count}")
        add_item(f"fleet.issues.open[{repo}]", count)

    # --- Send to Zabbix ---
    print(f"\n--- Sending {len(trapper_items)} items to Zabbix ---")
    success = send_trapper(trapper_items)

    print("\nDone.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
