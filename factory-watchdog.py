#!/usr/bin/env python3
"""CrunchTools factory watchdog — auto-discovers repos, runs checks, serves status.

Monitors all crunchtools repos that have a constitution across 5 dimensions:
  1. GHA workflow status (all repos)
  2. Version sync across pyproject.toml / __init__.py / server.py (MCP Server repos)
  3. Artifact sync across GitHub release / PyPI / Quay.io / GHCR (MCP Server repos)
  4. Constitution validation (all repos with constitutions)
  5. Open GitHub issues & PRs (all repos)

Repos are auto-discovered from the GitHub org. Any repo with a constitution
at .specify/memory/constitution.md is monitored. The constitution's Profile
header determines which checks apply.

Live service monitoring (HTTP, TCP, process checks) is handled by Nagios —
factory does not duplicate that.

Results are written to /data/factory-status.json, which is both what
factory-dashboard renders and what Nagios alerts on: the host bind-mounts the
same directory and check_factory_status.sh reads the summary block directly.
That file IS the alerting interface — if its schema changes, the Nagios check
changes with it.

A sixth dimension and a metrics push to the old monitoring stack were removed
in RT #1478; Nagios reads the status file instead.

No pip dependencies — stdlib only + gh CLI.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GITHUB_ORG = os.environ.get("GITHUB_ORG", "crunchtools")
STATUS_FILE = os.environ.get("STATUS_FILE", "/data/factory-status.json")

VALIDATOR_PATH = "/usr/local/lib/validate-constitution.py"

# PyPI package names follow the pattern mcp-{name}-crunchtools
PYPI_PREFIX = "mcp-"
PYPI_SUFFIX = "-crunchtools"


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
    """Return (1, summary) if versions match, (0, details) otherwise.

    GitHub release, PyPI, and Quay are all required for MCP Servers.
    Missing artifacts are a failure, not silently skipped.
    """
    gh_ver = get_github_release_version(repo)
    pypi_ver = get_pypi_version(repo)
    quay_ver = get_quay_latest_tag(repo)
    ghcr_ver = get_ghcr_latest_tag(repo)

    # Check for missing required artifacts
    missing = []
    if not gh_ver:
        missing.append("github")
    if not pypi_ver:
        missing.append("pypi")
    if not quay_ver:
        missing.append("quay")
    if missing:
        present = []
        if gh_ver:
            present.append(f"github={gh_ver}")
        if pypi_ver:
            present.append(f"pypi={pypi_ver}")
        if quay_ver:
            present.append(f"quay={quay_ver}")
        detail = "missing: " + ",".join(missing)
        if present:
            detail += " | found: " + ", ".join(present)
        return 0, detail

    versions: dict[str, str] = {
        "github": gh_ver,
        "pypi": pypi_ver,
        "quay": quay_ver,
    }
    if ghcr_ver:
        versions["ghcr"] = ghcr_ver

    unique = set(versions.values())
    detail = ", ".join(f"{k}={v}" for k, v in versions.items())
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
# Check 5: Changelog
# ---------------------------------------------------------------------------

def check_changelog(repo: str) -> tuple[int, str]:
    """Verify CHANGELOG.md exists and satisfies Section II. Returns (score, detail).

    This duplicates check_changelog() in validate-constitution.py rather than
    calling it, because the watchdog has no checkout: check_constitution() feeds
    the validator a tempfile, so every filesystem-based check in it is a no-op
    here. The API is the only view of the repo we have.
    """
    content = gh_file_content(repo, "CHANGELOG.md")
    if content is None:
        return 0, "no CHANGELOG.md"

    missing = []
    if not re.search(r"^#+\s*\[Unreleased\]", content, re.MULTILINE):
        missing.append("no [Unreleased] section")
    if not re.search(r"keepachangelog\.com", content, re.IGNORECASE):
        missing.append("no Keep a Changelog reference")

    if missing:
        return 0, ", ".join(missing)
    return 1, ""


# ---------------------------------------------------------------------------
# Check 6: Open GitHub Issues & PRs
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
# JSON status output
# ---------------------------------------------------------------------------

def load_status() -> dict | None:
    """Load the existing status JSON file."""
    path = Path(STATUS_FILE)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


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

    # Parse --only flag for selective scanning
    only_repos: set[str] | None = None
    for arg in sys.argv[1:]:
        if arg.startswith("--only="):
            only_repos = set(arg[len("--only="):].split(","))
            print(f"Selective scan: {', '.join(sorted(only_repos))}")

    # --- Auto-discover repos ---
    repos = discover_repos()
    if not repos:
        print("ERROR: No repos discovered, aborting", file=sys.stderr)
        return 1

    # Load existing status for merging when doing selective scan
    existing_status = None
    if only_repos:
        existing_status = load_status()
        repos = [r for r in repos if r["name"] in only_repos]
        if not repos:
            print("No matching repos found for --only filter")
            return 1

    repo_names = [r["name"] for r in repos]
    mcp_repos = [r["name"] for r in repos if r["profile"] == "MCP Server"]

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
            "changelog": None,
            "changelog_detail": "",
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

    # --- Check 5: Changelog ---
    print("\n--- Changelog ---")
    for name in repo_names:
        score, detail = check_changelog(name)
        status = "OK" if score == 1 else "FAIL"
        print(f"  {name}: {status}" + (f" ({detail})" if detail else ""))
        repo_results[name]["changelog"] = score
        repo_results[name]["changelog_detail"] = detail

    # --- Check 6: Open Issues & PRs ---
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

    # --- Compute summary ---
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
        if res["changelog"] == 0:
            healthy = False
        res["healthy"] = healthy

    # Merge selective scan results into existing status
    if only_repos and existing_status and existing_status.get("repos"):
        merged = dict(existing_status["repos"])
        merged.update(repo_results)
        repo_results = merged
        mcp_repos = [n for n, r in repo_results.items() if r["profile"] == "MCP Server"]

    total_repos = len(repo_results)
    healthy_repos = sum(1 for r in repo_results.values() if r["healthy"])
    failing_repos = total_repos - healthy_repos
    gha_failing = sum(1 for r in repo_results.values() if r["gha"] == 0)
    constitution_failing = sum(1 for r in repo_results.values() if r["constitution"] == 0)
    changelog_failing = sum(1 for r in repo_results.values() if r.get("changelog") == 0)
    version_failing = sum(1 for n in mcp_repos if repo_results[n]["version_sync"] == 0)
    artifact_failing = sum(1 for n in mcp_repos if repo_results[n]["artifact_sync"] == 0)
    all_healthy = failing_repos == 0

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
            "changelog_failing": changelog_failing,
            "version_failing": version_failing,
            "artifact_failing": artifact_failing,
        },
        "repos": repo_results,
    }
    write_status(status_data)

    # --- Print summary ---
    print(f"\n{'=' * 60}")
    print(f"Summary: {healthy_repos}/{total_repos} repos healthy")
    if not all_healthy:
        print("  Issues:")
        if gha_failing:
            print(f"    GHA failing: {gha_failing}")
        if constitution_failing:
            print(f"    Constitution failing: {constitution_failing}")
        if changelog_failing:
            print(f"    Changelog failing: {changelog_failing}")
        if version_failing:
            print(f"    Version sync failing: {version_failing}")
        if artifact_failing:
            print(f"    Artifact sync failing: {artifact_failing}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
