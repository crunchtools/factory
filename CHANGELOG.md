# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

Entries prior to 2026-09-19 are back-filled from GitHub Release notes (RT #1484).

## [Unreleased]

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
