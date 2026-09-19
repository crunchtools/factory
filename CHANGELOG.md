# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

Entries prior to 2026-09-19 are back-filled from GitHub Release notes (RT #1484).

## [Unreleased]

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
  (That same gap means `check_gourmand_ci_gate` has never run here either; see
  RT #1484.)

### Changed
- Repos failing the changelog check are now counted as unhealthy.
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
