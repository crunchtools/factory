# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

Entries prior to 2026-09-19 are back-filled from GitHub Release notes (RT #1484).

## [Unreleased]

### Added

- Gourmand CI gate (`.github/workflows/gourmand.yml`, gatehouse v0.9.0) and
  `gourmand` + `ruff-check` pre-commit hooks, with ruff configured in
  `pyproject.toml` (RT #1509).

### Changed

- Cleared the Gourmand baseline in the watchdog and dashboard without
  changing behaviour: split `main()` and the gate/release checks into
  smaller functions, extracted the shared version-agreement and workflow
  iteration code, narrowed three `except Exception` fallbacks to the errors
  they actually handle, named the subprocess timeouts, used `HTTPStatus`
  in the dashboard, and dropped the ruler comments. Output and the status
  file schema are unchanged.
- The vendored `validate-constitution.py` is excluded from Gourmand; it is
  owned upstream in crunchtools/constitution.
- Constitution now inherits crunchtools/constitution v1.17.0.

## [1.3.2] - 2026-09-20

### Fixed

- `build.yml` never triggered on version tags and never produced a semver
  image tag -- only `push: branches: [main]` with `latest`/short-sha tags.
  So no crunchtools/factory release, including v1.3.1, ever shipped a
  version-tagged image to either registry; the registry-drift check was
  correctly flagging a gap CI had never closed. Added a `tags: ["v*"]`
  trigger and a `type=semver,pattern={{version}}` tag to both the Quay and
  GHCR jobs. Cutting v1.3.2 is what puts the first real versioned factory
  image in both registries.

## [1.3.1] - 2026-09-19

### Fixed
- **Watchdog runs took 57 minutes, and all but ~5 of them were spent idle in
  `connect()` waiting on unroutable IPv6 addresses.** The factory host
  advertises IPv6 but cannot route it. `pypi.org` publishes four AAAA records
  and `quay.io` publishes eight, glibc sorts IPv6 ahead of IPv4, and `urllib`
  walks that list strictly in order with no Happy Eyeballs fallback. So every
  version-sync call burned 4x15s and every artifact-sync call 8x15s before
  reaching a working IPv4 address — 180s per MCP repo, 57 minutes across 19 of
  them, which is the whole observed runtime.

  `get_pypi_version()` and `get_quay_latest_tag()` now share a `fetch_json()`
  helper that resolves IPv4 only.

  This also explains why the symptom was so hard to place: `api.github.com`
  publishes no AAAA record, so the several hundred `gh` calls were never
  affected, and per-call GitHub latency measured *faster* on the factory host
  than on a laptop. The process was not saturated, it was blocked — 12 minutes
  into a run it had consumed under one second of CPU and had no child process.

  Consequence beyond the runtime: `OnCalendar=*:0/15` silently degraded to
  back-to-back hourly runs, because systemd will not start a second instance of
  a `Type=oneshot` unit while one is still running. Observed directly — a run
  finished at 23:30:36 and the next started at 23:30:35. Fleet status was
  therefore up to an hour stale against a Nagios check that pages CRITICAL at
  120 minutes, leaving roughly 15 minutes of headroom.

## [1.3.0] - 2026-09-19

### Fixed
- **The watchdog now discovers forked repos.** `discover_repos()` asked GitHub
  for `?type=sources`, which silently means "not forks", so every forked repo in
  the org was invisible to all eight dimensions. transcriptor carries a
  constitution and the Forked MCP Server profile exists precisely for it, yet it
  had never been checked by anything. Now `?type=all`.

  Carrying a constitution is the membership test, not how the repo was born.

  The profile gating already handles the rest: version sync, artifact sync and
  the gourmand gate all test for the exact string `"MCP Server"`, and a fork's
  profile is `"Forked MCP Server"`, so those three skip it without any change.
  The releases dimension reads it as not distribution-bearing, because
  `publish-docker.yml` fires on tag push rather than on a release. What is left
  is GHA status, constitution validation, changelog and open issues/PRs — which
  are exactly the dimensions that mean something for a fork we maintain.

  Verified before merging: transcriptor passes constitution validation and the
  changelog check, and its GHA runs are green, so discovering it adds a healthy
  repo rather than a new red light. Fleet total goes 48 -> 49.

### Added
- `fork` is now recorded per repo in `factory-status.json`, so the dashboard and
  anything else reading the file can tell a fork from a source repo.

## [1.2.0] - 2026-09-19

### Added
- **Releases dimension** (RT #1485). `check_releases()` verifies over the GitHub
  API that every version tag in a distribution-bearing repo has a GitHub Release,
  as Constitution II requires since 1.15.0. Reported per repo, counted as
  `releases_failing` in the summary, and shown as the `D` gate light plus a
  tooltip line.

  **The dimension only asks the question where the answer means something.**
  A repo is distribution-bearing when a workflow's `on:` block contains
  `release:` — read from the wiring, not from the profile, so it cannot drift
  when a repo gains or drops a publish job. Everything else returns `None` and
  renders grey, the way `gourmand_gate` already no-ops for profiles it does not
  cover.

  **It is deliberately forward-looking**, scoped to tags dated on or after the
  1.15.0 cutoff. RT #1485 audited 178 tags with no release and found 166 of them
  to be deploy markers in five continuously deployed repos. A check that reported
  those as failures would sit permanently red, and a permanently red light is one
  nobody reads. The remaining truth — two genuinely undistributed versions — was
  invisible underneath them.

  Backfilling is also not a safe remedy, which is why the cutoff is in the
  constitution and not just here: release-triggered workflows check out the
  release ref, so creating a release against an old tag builds and ships that old
  code (RT #1462).

  Undatable tags are treated as historical rather than as violations. The check
  errs toward silence: a missed old tag costs nothing, a permanent false red
  costs the signal.

- **Malformed version tags are reported** by the same check. A release created
  against a bare `0.4.0` satisfies no audit matching `vX.Y.Z` and leaves a junk
  tag in the repo. Found in mcp-request-tracker, now fixed.

### Changed
- `check_factory_status.sh` needs no change: it greps named keys out of the
  summary block, so `releases_failing` is additive. Verified against a generated
  status file.

## [1.1.0] - 2026-09-19

### Added
- **Changelog dimension** (RT #1484). `check_changelog()` verifies via the GitHub
  API that each monitored repo has a `CHANGELOG.md` with an `[Unreleased]`
  section and a Keep a Changelog reference, as Constitution II requires. Reported
  per repo, counted as `changelog_failing` in the summary, and surfaced on the
  dashboard as the `L` gate light plus a tooltip line.

  The check is implemented against the API rather than by reusing
  `check_changelog()` in `validate-constitution.py`, because `check_constitution()`
  feeds the validator constitution text from a tempfile — so every
  filesystem-based check inside the validator is a silent no-op in the watchdog.
- **Gourmand CI gate dimension** (RT #1484, closing the gap RT #1468 left open).
  `check_gourmand_gate()` verifies over the API that a repo's CI actually calls
  gatehouse's reusable `gourmand.yml` — not a dead `cargo install` pattern, not
  an inlined copy-paste of the job. Applies to the MCP Server and CLI Tool
  profiles. Reported per repo, counted as `gourmand_failing`, and shown as the
  `R` gate light.

  This existed as `check_gourmand_ci_gate()` in the validator, but the tempfile
  problem above meant it had **never once run in the watchdog** — RT #1468's
  fleet-wide guardrail was only ever enforced in per-repo CI. Running it fleet-wide
  for the first time surfaced three false positives in the original, fixed
  upstream in crunchtools/constitution.

### Changed
- Repos failing the changelog or Gourmand gate check are now counted as unhealthy.
- Synced the vendored `validate-constitution.py` with crunchtools/constitution.

## [1.0.0] - 2026-03-10

First tagged release of the CrunchTools factory watchdog.

### Added
- Monitors **28 repos** across the crunchtools org (MCP servers, container image
  tree, web applications) across **6 dimensions**: GHA workflow status, version
  sync, artifact sync, constitution validation, open issues, Zabbix item coverage.
- Constitution validator covering all 5 profiles: MCP Server, Container Image,
  Claude Skill, Autonomous Agent, Web Application.
- `check_web_application()` validates base image, registry, runtime, host
  directory convention, data persistence, monitoring, testing, quality gates, and
  cascade rebuild.
- Deployment as a systemd timer (every 15 minutes) inside a container on
  `ubi10-core`, sending results to Zabbix via the trapper protocol.
