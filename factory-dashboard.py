#!/usr/bin/env python3
"""CrunchTools factory dashboard — serves status page on port 8095.

Reads /data/factory-status.json (written by factory-watchdog) and renders
a dark-themed ASCII tree dashboard showing software delivery health.

Live service monitoring is handled by Zabbix natively — this dashboard
focuses exclusively on software delivery: GHA, version sync, artifact
sync, constitution compliance, and open issues/PRs.

No pip dependencies — stdlib only.
"""

import json
import os
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

STATUS_FILE = os.environ.get("STATUS_FILE", "/data/factory-status.json")
LISTEN_PORT = int(os.environ.get("DASHBOARD_PORT", "8095"))

# Profile icons for visual grouping
PROFILE_ICONS = {
    "MCP Server": "MCP",
    "Container Image": "IMG",
    "Web Application": "WEB",
    "Claude Skill": "SKL",
    "Autonomous Agent": "AGT",
    "Unknown": "???",
}

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Factory Dashboard</title>
<style>
body {{
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
    font-size: 13px;
    line-height: 1.5;
    margin: 0;
    padding: 20px;
}}
.header {{
    color: #00d4ff;
    margin-bottom: 10px;
}}
.header h1 {{
    margin: 0;
    font-size: 18px;
    font-weight: normal;
}}
.meta {{
    color: #888;
    font-size: 11px;
    margin-bottom: 20px;
}}
.summary {{
    background: #16213e;
    border: 1px solid #0f3460;
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 20px;
    display: inline-block;
}}
.summary .ok {{ color: #00ff88; }}
.summary .fail {{ color: #ff4444; }}
.summary .warn {{ color: #ffaa00; }}
.section {{
    margin-bottom: 24px;
}}
.section-title {{
    color: #00d4ff;
    border-bottom: 1px solid #0f3460;
    padding-bottom: 4px;
    margin-bottom: 8px;
    font-size: 14px;
}}
pre {{
    margin: 0;
    white-space: pre;
}}
.ok {{ color: #00ff88; }}
.fail {{ color: #ff4444; }}
.warn {{ color: #ffaa00; }}
.dim {{ color: #666; }}
.profile {{ color: #888; }}
.count {{ color: #aaa; }}
a {{
    color: #5599ff;
    text-decoration: none;
}}
a:hover {{
    text-decoration: underline;
}}
</style>
</head>
<body>
<div class="header">
<h1>CrunchTools Software Factory</h1>
</div>
<div class="meta">{meta}</div>
<div class="summary">{summary}</div>

<div class="section">
<div class="section-title">Software Delivery</div>
<pre>{software_tree}</pre>
</div>
</body>
</html>
"""


def load_status() -> dict | None:
    """Load the status JSON file."""
    path = Path(STATUS_FILE)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def span(css_class: str, text: str) -> str:
    return f'<span class="{css_class}">{text}</span>'


def status_dot(ok: bool) -> str:
    return span("ok", "OK") if ok else span("fail", "FAIL")


def render_software_tree(data: dict) -> str:
    """Render the per-repo software delivery tree."""
    repos = data.get("repos", {})
    if not repos:
        return span("dim", "(no repos discovered)")

    # Group by profile
    by_profile: dict[str, list[tuple[str, dict]]] = {}
    for name, info in sorted(repos.items()):
        profile = info.get("profile", "Unknown")
        by_profile.setdefault(profile, []).append((name, info))

    lines = []
    profile_order = ["MCP Server", "Container Image", "Web Application",
                     "Claude Skill", "Autonomous Agent", "Unknown"]
    profiles = [p for p in profile_order if p in by_profile]

    for pi, profile in enumerate(profiles):
        repos_in_profile = by_profile[profile]
        is_last_profile = (pi == len(profiles) - 1)
        icon = PROFILE_ICONS.get(profile, "???")
        branch = "\u2514" if is_last_profile else "\u251c"
        cont = " " if is_last_profile else "\u2502"

        lines.append(f"{branch}\u2500\u2500 {span('profile', f'[{icon}]')} {profile} ({len(repos_in_profile)})")

        for ri, (name, info) in enumerate(repos_in_profile):
            is_last_repo = (ri == len(repos_in_profile) - 1)
            repo_branch = "\u2514" if is_last_repo else "\u251c"

            # Determine repo health
            healthy = info.get("healthy", True)
            repo_status = status_dot(healthy)

            # Build dimension line
            dims = []
            gha = info.get("gha")
            if gha is not None:
                dims.append(f"GHA:{status_dot(gha == 1)}")

            constitution = info.get("constitution")
            if constitution is not None:
                dims.append(f"Constitution:{status_dot(constitution == 1)}")

            version_sync = info.get("version_sync")
            if version_sync is not None:
                ver = info.get("version", "")
                if version_sync == 1:
                    dims.append(f"Version:{span('ok', ver)}")
                else:
                    dims.append(f"Version:{span('fail', ver)}")

            artifact_sync = info.get("artifact_sync")
            if artifact_sync is not None:
                dims.append(f"Artifacts:{status_dot(artifact_sync == 1)}")

            issues = info.get("issues_open", 0)
            prs = info.get("prs_open", 0)
            if issues > 0 or prs > 0:
                parts = []
                if issues > 0:
                    parts.append(f"{issues}i")
                if prs > 0:
                    parts.append(f"{prs}pr")
                dims.append(span("count", "/".join(parts)))

            dim_str = "  ".join(dims) if dims else ""

            lines.append(
                f"{cont}  {repo_branch}\u2500\u2500 {repo_status}  {name}  {dim_str}"
            )

    return "\n".join(lines)


def render_page(data: dict | None) -> str:
    """Render the full HTML dashboard page."""
    if data is None:
        return HTML_TEMPLATE.format(
            meta="Status file not found. Waiting for first watchdog run...",
            summary=span("warn", "No data available"),
            software_tree=span("dim", "(waiting for factory-watchdog)"),
        )

    # Meta
    ts = data.get("timestamp", "unknown")
    try:
        dt = datetime.fromisoformat(ts)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age < 120:
            age_str = f"{int(age)}s ago"
        elif age < 7200:
            age_str = f"{int(age / 60)}m ago"
        else:
            age_str = f"{int(age / 3600)}h ago"
        ts_display = f"{dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({age_str})"
    except (ValueError, TypeError):
        ts_display = ts
    org = data.get("org", "crunchtools")
    meta = f"org: {org} | updated: {ts_display}"

    # Summary
    s = data.get("summary", {})
    health = s.get("health", 0)
    repos_total = s.get("repos_total", 0)
    repos_healthy = s.get("repos_healthy", 0)

    if health == 1:
        health_str = span("ok", "HEALTHY")
    else:
        health_str = span("fail", "DEGRADED")

    summary_parts = [
        f"Status: {health_str}",
        f"Repos: {span('ok' if repos_healthy == repos_total else 'warn', f'{repos_healthy}/{repos_total}')}",
    ]

    # Show specific failure counts if any
    failures = []
    for key, label in [
        ("gha_failing", "GHA"),
        ("constitution_failing", "Constitution"),
        ("version_failing", "Version"),
        ("artifact_failing", "Artifact"),
    ]:
        count = s.get(key, 0)
        if count > 0:
            failures.append(f"{label}:{span('fail', str(count))}")
    if failures:
        summary_parts.append("Failing: " + "  ".join(failures))

    summary = " | ".join(summary_parts)

    return HTML_TEMPLATE.format(
        meta=meta,
        summary=summary,
        software_tree=render_software_tree(data),
    )


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the factory dashboard."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return

        if self.path == "/api/status":
            data = load_status()
            self.send_response(200 if data else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data or {}).encode())
            return

        # Default: serve dashboard HTML
        data = load_status()
        html = render_page(data)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        print(f"{self.address_string()} {args[0]}", flush=True)


def main() -> int:
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), DashboardHandler)
    print(f"Factory dashboard listening on port {LISTEN_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
