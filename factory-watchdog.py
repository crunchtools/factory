#!/usr/bin/env python3
"""CrunchTools factory watchdog — auto-discovers repos, runs checks, serves status.

Monitors all crunchtools repos that have a constitution across 7 dimensions:
  1. GHA workflow status (all repos)
  2. Version sync across pyproject.toml / __init__.py / server.py (MCP Server repos)
  3. Artifact sync across GitHub release / PyPI / Quay.io / GHCR (MCP Server repos)
  4. Constitution validation (all repos with constitutions)
  5. Open GitHub issues & PRs (all repos)
  6. Zabbix item coverage (summary items exist on host)
  7. Live service health (Web Application repos — HTTP, TCP, process checks)

Repos are auto-discovered from the GitHub org. Any repo with a constitution
at .specify/memory/constitution.md is monitored. The constitution's Profile
header determines which checks apply.

Results are:
  - Written to /data/factory-status.json (consumed by factory-dashboard)
  - Pushed to Zabbix as ~10 summary trapper items (for alerting)

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
from datetime import datetime, timezone
from pathlib import Path

ZABBIX_HOST = "factory.crunchtools.com"
ZABBIX_SERVER = os.environ.get("ZABBIX_SERVER", "127.0.0.1")
ZABBIX_PORT = int(os.environ.get("ZABBIX_PORT", "10051"))
ZABBIX_API_URL = os.environ.get("ZABBIX_API_URL", "")
ZABBIX_API_TOKEN = os.environ.get("ZABBIX_API_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "crunchtools")
STATUS_FILE = os.environ.get("STATUS_FILE", "/data/factory-status.json")

VALIDATOR_PATH = "/usr/local/lib/validate-constitution.py"

# PyPI package names follow the pattern mcp-{name}-crunchtools
PYPI_PREFIX = "mcp-"
PYPI_SUFFIX = "-crunchtools"

# Fallback live service registry for repos whose constitutions don't have
# machine-parseable monitoring sections yet. Keyed by repo name.
# Each entry: list of check dicts with type, port/pattern, container, description.
LIVE_SERVICES = {
    "acquacotta": {
        "container": "acquacotta.crunchtools.com",
        "checks": [
            {"type": "http", "port": 80, "desc": "Apache"},
            {"type": "process", "pattern": "gunicorn", "desc": "Gunicorn"},
        ],
    },
    "rotv": {
        "container": "rootsofthevalley.org",
        "checks": [
            {"type": "http", "port": 8080, "desc": "Node.js backend"},
            {"type": "tcp", "port": 5432, "desc": "PostgreSQL"},
            {"type": "process", "pattern": "postgres", "desc": "PostgreSQL"},
            {"type": "process", "pattern": "node", "desc": "Node.js"},
        ],
    },
    "immich": {
        "container": "images.rootsofthevalley.org",
        "checks": [
            {"type": "tcp", "port": 2283, "desc": "Immich web"},
            {"type": "process", "pattern": "postgres", "desc": "PostgreSQL"},
            {"type": "process", "pattern": "valkey-server", "desc": "Valkey"},
        ],
    },
    "zabbix": {
        "container": "zabbix.crunchtools.com",
        "checks": [
            {"type": "http", "port": 8090, "desc": "Zabbix web"},
            {"type": "process", "pattern": "postgres", "desc": "PostgreSQL"},
            {"type": "process", "pattern": "zabbix_server", "desc": "Zabbix server"},
        ],
    },
    "postiz": {
        "container": "postiz.crunchtools.com",
        "checks": [
            {"type": "http", "port": 8092, "desc": "Postiz web"},
            {"type": "process", "pattern": "postgres", "desc": "PostgreSQL"},
            {"type": "process", "pattern": "valkey-server", "desc": "Valkey"},
            {"type": "process", "pattern": "node", "desc": "Node.js"},
        ],
    },
    "rt": {
        "container": "rt.fatherlinux.com",
        "checks": [
            {"type": "http", "port": 80, "desc": "Apache/RT"},
            {"type": "process", "pattern": "mariadbd", "desc": "MariaDB"},
            {"type": "process", "pattern": "master", "desc": "Postfix"},
        ],
    },
    "proxy": {
        "container": "proxy.crunchtools.com",
        "checks": [
            {"type": "http", "port": 80, "desc": "Apache proxy"},
        ],
    },
}


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


def gh_api_paginated(endpoint: str, per_page: int = 100) -> list:
    """Call the GitHub API with pagination. Returns combined results list."""
    all_results = []
    page = 1
    sep = "&" if "?" in endpoint else "?"
    while True:
        data = gh_api(f"{endpoint}{sep}per_page={per_page}&page={page}")
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        all_results.extend(data)
        if len(data) < per_page:
            break
        page += 1
    return all_results


def gh_file_content(repo: str, path: str) -> str | None:
    """Fetch a file from GitHub and return its decoded text content."""
    data = gh_api(f"repos/{GITHUB_ORG}/{repo}/contents/{path}")
    if not data or "content" not in data:
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

def parse_constitution_header(text: str) -> dict[str, str]:
    """Extract key-value pairs from the blockquote header of a constitution."""
    header: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r">\s*\*\*(\w[\w\s]*):\*\*\s*(.*)", line)
        if match:
            key = match.group(1).strip()
            value = match.group(2).strip()
            header[key] = value
    return header


def discover_repos() -> list[dict]:
    """Discover all repos in the org that have a constitution.

    Returns a list of dicts:
        {
            "name": "mcp-cloudflare",
            "profile": "MCP Server",
            "constitution": "<full text>",
            "header": {parsed header dict},
        }
    """
    print("Discovering repos from GitHub org...")
    repos_data = gh_api_paginated(f"orgs/{GITHUB_ORG}/repos?type=sources")
    if not repos_data:
        print("  WARN: Could not list org repos, falling back to empty list")
        return []

    repo_names = sorted(r["name"] for r in repos_data if not r.get("archived"))
    print(f"  Found {len(repo_names)} non-archived repos in {GITHUB_ORG}")

    discovered = []
    for name in repo_names:
        content = gh_file_content(name, ".specify/memory/constitution.md")
        if content is None:
            continue
        header = parse_constitution_header(content)
        profile = header.get("Profile", "Unknown")
        discovered.append({
            "name": name,
            "profile": profile,
            "constitution": content,
            "header": header,
        })
        print(f"  + {name} [{profile}]")

    print(f"  Discovered {len(discovered)} repos with constitutions")
    return discovered


# ---------------------------------------------------------------------------
# Check 1: GHA Workflow Status
# ---------------------------------------------------------------------------

def check_gha_status(repo: str) -> int:
    """Return 1 if all latest workflow runs on main are green, 0 otherwise."""
    data = gh_api(
        f"repos/{GITHUB_ORG}/{repo}/actions/runs?branch=main&per_page=10"
    )
    if not data or "workflow_runs" not in data:
        return 0

    runs = data["workflow_runs"]
    if not runs:
        return 1

    latest_by_workflow: dict[int, dict] = {}
    for run in runs:
        wf_id = run.get("workflow_id")
        if wf_id and wf_id not in latest_by_workflow:
            latest_by_workflow[wf_id] = run

    for run in latest_by_workflow.values():
        conclusion = run.get("conclusion")
        if conclusion not in ("success", "skipped"):
            return 0

    return 1


# ---------------------------------------------------------------------------
# Check 2: Version Sync (MCP Server repos only)
# ---------------------------------------------------------------------------

def extract_version_pyproject(text: str) -> str | None:
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def extract_version_init(text: str) -> str | None:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def extract_version_server(text: str) -> str | None:
    match = re.search(r'version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def check_version_sync(repo: str) -> tuple[int, str]:
    """Return (1, version) if all version sources match, (0, details) otherwise."""
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
        return 1, next(iter(unique))
    else:
        detail = ", ".join(f"{k}={v}" for k, v in found.items())
        return 0, f"mismatch: {detail}"


# ---------------------------------------------------------------------------
# Check 3: Artifact Sync (MCP Server repos only)
# ---------------------------------------------------------------------------

SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def is_semver_tag(name: str) -> bool:
    return bool(SEMVER_RE.match(name.lstrip("v")))


def get_github_release_version(repo: str) -> str | None:
    data = gh_api(f"repos/{GITHUB_ORG}/{repo}/releases/latest")
    if data and "tag_name" in data:
        tag = data["tag_name"]
        return tag.lstrip("v") if tag else None

    tags = gh_api(f"repos/{GITHUB_ORG}/{repo}/tags?per_page=10")
    if tags and isinstance(tags, list):
        for tag in tags:
            name = tag.get("name", "")
            if is_semver_tag(name):
                return name.lstrip("v")
    return None


def get_pypi_version(repo: str) -> str | None:
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
    url = f"https://quay.io/api/v1/repository/{GITHUB_ORG}/{repo}/tag/?limit=20&onlyActiveTags=true"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            tags = data.get("tags", [])
            for tag in tags:
                name = tag.get("name", "").lstrip("v")
                if re.match(r"^\d+\.\d+\.\d+$", name):
                    return name
            for tag in tags:
                name = tag.get("name", "").lstrip("v")
                if re.match(r"^\d+\.\d+$", name):
                    return name
            return None
    except Exception:
        return None


def get_ghcr_latest_tag(repo: str) -> str | None:
    data = gh_api(
        f"orgs/{GITHUB_ORG}/packages/container/{repo}/versions?per_page=10"
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
    """Return (1, summary) if versions match, (0, details) otherwise."""
    gh_ver = get_github_release_version(repo)
    pypi_ver = get_pypi_version(repo)
    quay_ver = get_quay_latest_tag(repo)
    ghcr_ver = get_ghcr_latest_tag(repo)

    versions: dict[str, str | None] = {
        "github": gh_ver,
        "pypi": pypi_ver,
        "quay": quay_ver,
    }
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

def check_constitution(repo_info: dict) -> tuple[int, str]:
    """Validate constitution via validate-constitution.py. Returns (score, violations)."""
    content = repo_info.get("constitution")
    if content is None:
        return 1, ""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
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
            return 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 0, "validator timed out"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Check 5: Open GitHub Issues & PRs
# ---------------------------------------------------------------------------

def check_open_issues(repo: str) -> int:
    data = gh_api(
        f"repos/{GITHUB_ORG}/{repo}/issues?state=open&per_page=100"
    )
    if not data or not isinstance(data, list):
        return 0
    return sum(1 for issue in data if "pull_request" not in issue)


def check_open_prs(repo: str) -> int:
    data = gh_api(
        f"repos/{GITHUB_ORG}/{repo}/pulls?state=open&per_page=100"
    )
    if not data or not isinstance(data, list):
        return 0
    return len(data)


# ---------------------------------------------------------------------------
# Check 6: Zabbix Item Coverage (summary items)
# ---------------------------------------------------------------------------

SUMMARY_KEYS = [
    "factory.health",
    "factory.repos.total",
    "factory.repos.healthy",
    "factory.repos.failing",
    "factory.gha.failing",
    "factory.constitution.failing",
    "factory.version.failing",
    "factory.artifact.failing",
    "factory.services.total",
    "factory.services.failing",
]


def zabbix_api_call(method: str, params: dict) -> dict | None:
    """Make a Zabbix JSON-RPC API call."""
    if not ZABBIX_API_URL or not ZABBIX_API_TOKEN:
        return None
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json-rpc",
        "Authorization": f"Bearer {ZABBIX_API_TOKEN}",
    }
    try:
        req = urllib.request.Request(ZABBIX_API_URL, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if "error" in data:
            msg = data["error"].get("data", data["error"].get("message", ""))
            print(f"  WARN: Zabbix API error: {msg}", file=sys.stderr)
            return None
        return data.get("result")
    except Exception as e:
        print(f"  WARN: Zabbix API unreachable: {e}", file=sys.stderr)
        return None


def check_zabbix_coverage() -> tuple[int, list[str]]:
    """Return (1, []) if all summary items exist, (0, missing) otherwise."""
    if not ZABBIX_API_URL or not ZABBIX_API_TOKEN:
        print("  SKIP: Set ZABBIX_API_URL and ZABBIX_API_TOKEN to enable")
        return 1, []

    hosts = zabbix_api_call("host.get", {
        "filter": {"host": [ZABBIX_HOST]},
        "output": ["hostid"],
    })
    if not hosts:
        return 1, []
    host_id = hosts[0]["hostid"]

    items = zabbix_api_call("item.get", {
        "hostids": host_id,
        "output": ["key_"],
        "search": {"key_": "factory."},
        "startSearch": True,
    })
    if items is None:
        print("  WARN: Could not query Zabbix API, skipping")
        return 1, []

    existing = {item["key_"] for item in items}
    missing = sorted(set(SUMMARY_KEYS) - existing)
    if missing:
        return 0, missing
    return 1, []


# ---------------------------------------------------------------------------
# Check 7: Live Service Health
# ---------------------------------------------------------------------------

def check_http(port: int, host: str = "127.0.0.1", timeout: int = 10) -> bool:
    """Check if an HTTP service responds on the given port."""
    try:
        url = f"http://{host}:{port}/"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def check_tcp(port: int, host: str = "127.0.0.1", timeout: int = 5) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            return True
    except Exception:
        return False


def check_process(container: str, pattern: str) -> int:
    """Check process count inside a container. Returns count (0 = not running).

    Requires podman on the host. Returns -1 if podman is not available
    (e.g., running inside a container without host podman access).
    """
    # Try pgrep first
    try:
        result = subprocess.run(
            ["podman", "exec", container, "pgrep", "-c", pattern],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 127:
            count = result.stdout.strip()
            return int(count) if count.isdigit() else 0
    except FileNotFoundError:
        return -1  # podman not available
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return 0

    # Fallback: podman top -o args
    try:
        result = subprocess.run(
            ["podman", "top", container, "-o", "args"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[1:]
            count = sum(1 for line in lines if pattern in line)
            if count > 0:
                return count
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    # Fallback: plain podman top (minimal images)
    try:
        result = subprocess.run(
            ["podman", "top", container],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")[1:]
            return sum(1 for line in lines if pattern in line)
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    return 0


def run_live_checks(repos: list[dict]) -> list[dict]:
    """Run live service checks for Web Application repos.

    Returns list of service check results:
        {
            "repo": "acquacotta",
            "container": "acquacotta.crunchtools.com",
            "check": "HTTP :80",
            "type": "http",
            "ok": True/False,
        }
    """
    results = []

    # Build set of repos that are Web Application profile
    web_repos = {r["name"] for r in repos if r["profile"] == "Web Application"}

    # Also check repos in LIVE_SERVICES even if not Web Application profile
    # (e.g., zabbix, postiz, rt are infrastructure but have live services)
    check_repos = web_repos | set(LIVE_SERVICES.keys())

    for repo_name in sorted(check_repos):
        svc = LIVE_SERVICES.get(repo_name)
        if not svc:
            continue

        container = svc["container"]
        for check in svc["checks"]:
            check_type = check["type"]
            desc = check["desc"]
            ok = False

            if check_type == "http":
                ok = check_http(check["port"])
                label = f"HTTP :{check['port']}"
            elif check_type == "tcp":
                ok = check_tcp(check["port"])
                label = f"TCP :{check['port']}"
            elif check_type == "process":
                count = check_process(container, check["pattern"])
                if count == -1:
                    # podman not available — skip process checks entirely
                    continue
                ok = count > 0
                label = f"Process: {check['pattern']}"
            else:
                continue

            results.append({
                "repo": repo_name,
                "container": container,
                "check": label,
                "desc": desc,
                "type": check_type,
                "ok": ok,
            })

    return results


# ---------------------------------------------------------------------------
# Zabbix trapper protocol
# ---------------------------------------------------------------------------

def send_trapper(items: list[dict[str, str]]) -> bool:
    """Send items to Zabbix via the trapper (ZBXD) binary protocol."""
    payload = json.dumps({
        "request": "sender data",
        "data": items,
    }).encode("utf-8")

    header = b"ZBXD\x01" + struct.pack("<II", len(payload), 0)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect((ZABBIX_SERVER, ZABBIX_PORT))
            sock.sendall(header + payload)

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
# JSON status output
# ---------------------------------------------------------------------------

def write_status(status: dict) -> None:
    """Write status to JSON file atomically."""
    status_path = Path(STATUS_FILE)
    status_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file then rename for atomicity
    tmp_path = status_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(status, indent=2) + "\n")
    tmp_path.rename(status_path)
    print(f"Wrote status to {STATUS_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("CrunchTools Factory Watchdog")
    print("=" * 60)

    # --- Auto-discover repos ---
    repos = discover_repos()
    if not repos:
        print("ERROR: No repos discovered, aborting", file=sys.stderr)
        return 1

    repo_names = [r["name"] for r in repos]
    mcp_repos = [r["name"] for r in repos if r["profile"] == "MCP Server"]
    web_repos = [r["name"] for r in repos if r["profile"] == "Web Application"]

    # Per-repo results accumulator
    repo_results: dict[str, dict] = {}
    for r in repos:
        repo_results[r["name"]] = {
            "profile": r["profile"],
            "gha": None,
            "version_sync": None,
            "version": None,
            "artifact_sync": None,
            "constitution": None,
            "constitution_violations": "",
            "issues_open": 0,
            "prs_open": 0,
            "healthy": True,
        }

    # --- Check 1: GHA Status ---
    print("\n--- GHA Workflow Status ---")
    for name in repo_names:
        score = check_gha_status(name)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {name}: {status}")
        repo_results[name]["gha"] = score

    # --- Check 2: Version Sync (MCP repos) ---
    print("\n--- Version Sync ---")
    for name in mcp_repos:
        score, version_info = check_version_sync(name)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {name}: {status} ({version_info})")
        repo_results[name]["version_sync"] = score
        repo_results[name]["version"] = version_info

    # --- Check 3: Artifact Sync (MCP repos) ---
    print("\n--- Artifact Sync ---")
    for name in mcp_repos:
        score, artifact_info = check_artifact_sync(name)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {name}: {status} ({artifact_info})")
        repo_results[name]["artifact_sync"] = score

    # --- Check 4: Constitution Validation ---
    print("\n--- Constitution Validation ---")
    repo_map = {r["name"]: r for r in repos}
    for name in repo_names:
        score, violations = check_constitution(repo_map[name])
        status = "OK" if score == 1 else "FAIL"
        print(f"  {name}: {status}")
        if violations:
            print(f"    {violations[:200]}")
        repo_results[name]["constitution"] = score
        repo_results[name]["constitution_violations"] = violations

    # --- Check 5: Open Issues & PRs ---
    print("\n--- Open Issues ---")
    for name in repo_names:
        count = check_open_issues(name)
        print(f"  {name}: {count}")
        repo_results[name]["issues_open"] = count

    print("\n--- Open Pull Requests ---")
    for name in repo_names:
        count = check_open_prs(name)
        print(f"  {name}: {count}")
        repo_results[name]["prs_open"] = count

    # --- Check 6: Zabbix Item Coverage ---
    print("\n--- Zabbix Item Coverage ---")
    zabbix_ok, missing_keys = check_zabbix_coverage()
    if missing_keys:
        print(f"  WARN: {len(missing_keys)} summary items missing:")
        for key in missing_keys:
            print(f"    - {key}")
    elif zabbix_ok and ZABBIX_API_URL and ZABBIX_API_TOKEN:
        print(f"  OK: All {len(SUMMARY_KEYS)} summary items exist")

    # --- Check 7: Live Service Health ---
    print("\n--- Live Service Health ---")
    service_results = run_live_checks(repos)
    for svc in service_results:
        status = "OK" if svc["ok"] else "FAIL"
        print(f"  {svc['repo']}/{svc['desc']}: {status} ({svc['check']})")

    # --- Compute summary ---
    # A repo is "healthy" if all its applicable checks pass
    for name, res in repo_results.items():
        healthy = True
        if res["gha"] == 0:
            healthy = False
        if res["constitution"] == 0:
            healthy = False
        if res["version_sync"] == 0:
            healthy = False
        if res["artifact_sync"] == 0:
            healthy = False
        res["healthy"] = healthy

    total_repos = len(repo_results)
    healthy_repos = sum(1 for r in repo_results.values() if r["healthy"])
    failing_repos = total_repos - healthy_repos
    gha_failing = sum(1 for r in repo_results.values() if r["gha"] == 0)
    constitution_failing = sum(1 for r in repo_results.values() if r["constitution"] == 0)
    version_failing = sum(1 for n in mcp_repos if repo_results[n]["version_sync"] == 0)
    artifact_failing = sum(1 for n in mcp_repos if repo_results[n]["artifact_sync"] == 0)
    services_total = len(service_results)
    services_failing = sum(1 for s in service_results if not s["ok"])
    all_healthy = (failing_repos == 0 and services_failing == 0)

    # --- Write JSON status ---
    status_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "org": GITHUB_ORG,
        "summary": {
            "health": 1 if all_healthy else 0,
            "repos_total": total_repos,
            "repos_healthy": healthy_repos,
            "repos_failing": failing_repos,
            "gha_failing": gha_failing,
            "constitution_failing": constitution_failing,
            "version_failing": version_failing,
            "artifact_failing": artifact_failing,
            "services_total": services_total,
            "services_failing": services_failing,
        },
        "repos": repo_results,
        "services": service_results,
    }
    write_status(status_data)

    # --- Send summary trapper items to Zabbix ---
    trapper_items = [
        {"host": ZABBIX_HOST, "key": "factory.health", "value": str(1 if all_healthy else 0)},
        {"host": ZABBIX_HOST, "key": "factory.repos.total", "value": str(total_repos)},
        {"host": ZABBIX_HOST, "key": "factory.repos.healthy", "value": str(healthy_repos)},
        {"host": ZABBIX_HOST, "key": "factory.repos.failing", "value": str(failing_repos)},
        {"host": ZABBIX_HOST, "key": "factory.gha.failing", "value": str(gha_failing)},
        {"host": ZABBIX_HOST, "key": "factory.constitution.failing", "value": str(constitution_failing)},
        {"host": ZABBIX_HOST, "key": "factory.version.failing", "value": str(version_failing)},
        {"host": ZABBIX_HOST, "key": "factory.artifact.failing", "value": str(artifact_failing)},
        {"host": ZABBIX_HOST, "key": "factory.services.total", "value": str(services_total)},
        {"host": ZABBIX_HOST, "key": "factory.services.failing", "value": str(services_failing)},
    ]

    print(f"\n--- Sending {len(trapper_items)} summary items to Zabbix ---")
    success = send_trapper(trapper_items)

    # --- Print summary ---
    print(f"\n{'=' * 60}")
    print(f"Summary: {healthy_repos}/{total_repos} repos healthy, "
          f"{services_total - services_failing}/{services_total} services up")
    if not all_healthy:
        print("  Issues:")
        if gha_failing:
            print(f"    GHA failing: {gha_failing}")
        if constitution_failing:
            print(f"    Constitution failing: {constitution_failing}")
        if version_failing:
            print(f"    Version sync failing: {version_failing}")
        if artifact_failing:
            print(f"    Artifact sync failing: {artifact_failing}")
        if services_failing:
            print(f"    Services failing: {services_failing}")
    print("Done.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
