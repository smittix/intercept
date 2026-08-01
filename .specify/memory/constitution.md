<!--
Sync Impact Report
===================
Version change: [TEMPLATE] → 1.0.0 (initial ratification)
Rationale: First concrete constitution for this repository. The file previously
contained only unfilled template placeholders, so this is treated as an
initial ratification rather than an amendment; version starts at 1.0.0.

Modified principles: N/A (no prior concrete principles existed)

Added sections:
- Core Principles: I. Backward Compatibility First, II. Test-First Bug Fixes
  & Regression Safety, III. Standards Compliance & Static Verification,
  IV. Surgical, Minimal-Footprint Changes, V. Process, Resource & Hardware
  Safety, VI. Input Validation & Secure-by-Default
- Technology & Platform Constraints
- Development Workflow & Quality Gates
- Governance

Removed sections: none

Templates requiring updates:
- ✅ .specify/templates/plan-template.md — Constitution Check gate derives
  from this file at runtime ("[Gates determined based on constitution
  file]"); no hardcoded principle list to edit, no change needed.
- ✅ .specify/templates/spec-template.md — no constitution-specific
  references found; no change needed.
- ✅ .specify/templates/tasks-template.md — no constitution-specific
  references found; no change needed.
- ✅ .specify/templates/commands/ — directory does not exist in this
  installation; nothing to sync.
- ✅ README.md / docs — no constitution references found; no change needed.
- ⚠ CLAUDE.md — already encodes compatible, complementary guidance
  (Surgical Changes, Simplicity First, Goal-Driven Execution). Not modified
  by this command; kept as project-level runtime guidance per Governance
  section below. No conflicts identified.

Follow-up TODOs:
- TODO(RATIFICATION_DATE): Original project inception predates this
  constitution's adoption; no historical ratification date is recorded.
  Ratification date below is set to the date this constitution was first
  adopted in concrete form (2026-07-31). Update if an earlier authoritative
  date is located.
-->

# INTERCEPT Constitution

## Core Principles

### I. Backward Compatibility First (NON-NEGOTIABLE)

Public interfaces MUST NOT break existing deployments without an explicit,
documented migration path. This includes: REST/SSE endpoint contracts, CLI
flags in `setup.sh`/`start.sh`, `INTERCEPT_*` environment variables, SQLite
schema in `instance/`, the Postgres ADS-B history schema, Docker Compose
profiles (`basic`, `history`), and the request/response shape of any
external-tool integration listed in CLAUDE.md's integration table. When a
breaking change is truly unavoidable, it MUST be called out in the plan's
Constitution Check and Complexity Tracking sections with: what breaks, who is
affected, and the migration/upgrade path. Additive, backward-compatible
changes (new optional fields, new endpoints, new env vars with safe
defaults) do not require this justification.

**Rationale**: INTERCEPT ships as a long-running console on field and
embedded hardware (RPi, Docker on constrained boxes). Operators upgrade in
place; a silent breaking change strands a deployed SIGINT capability with no
easy rollback path.

### II. Test-First Bug Fixes & Regression Safety (NON-NEGOTIABLE)

Every bug fix MUST begin with a test that reproduces the defect and fails
before the fix is applied, then passes after. The fix is not complete until
that test, and the full existing `pytest` suite, pass. New features MUST
include tests for their primary behavior and realistic edge cases (invalid
input, missing hardware, subprocess failure). External tools (SDR binaries,
`rtl_fm`, `dump1090`, `SatDump`, etc.) MUST be mocked per the conventions in
`tests/conftest.py` rather than requiring physical hardware to run the suite.

**Rationale**: This codebase is dominated by subprocess integrations with
native SIGINT tools that are impractical to manually re-verify on every
change. A regression test is the only durable way to keep a fix "fixed" as
the surrounding code evolves.

### III. Standards Compliance & Static Verification

Code MUST pass `ruff check`, `black --check`, and `mypy` before it is
considered done. Input validation for anything that reaches a subprocess or
external tool (frequencies, gains, device indices, file paths) MUST go
through the centralized helpers in `utils/validation.py` rather than ad hoc
checks scattered per route. New code MUST follow the existing architectural
patterns already established in the module it touches (blueprint structure
in `routes/`, `SDRFactory`/`CommandBuilder` in `utils/sdr/`, `DataStore`
TTL pattern) rather than introducing a parallel style for the same problem.

**Rationale**: A brownfield codebase with many independently-evolving signal
modes (pager, ADS-B, AIS, WiFi/BT, weather sat, Meshtastic, ...) stays
maintainable only if contributors converge on one enforced standard instead
of each mode drifting its own conventions.

### IV. Surgical, Minimal-Footprint Changes

Touch only what the task requires. Do not refactor adjacent code, rename
things, or "clean up" formatting outside the change's scope, even if it
looks like an improvement. Match existing style in the file being edited.
Pre-existing dead code or unrelated issues MUST be reported, not silently
deleted. Every changed line MUST trace back to the request that motivated
it; imports/helpers that a change makes unused MUST be removed, but nothing
beyond that.

**Rationale**: This mirrors the standing project convention already in
CLAUDE.md ("Surgical Changes"). Elevating it to the constitution makes it an
enforced gate — in a shared, actively-integrated codebase, unrelated diffs
are the most common source of merge pain and unreviewable PRs.

### V. Process, Resource & Hardware Safety

Subprocess-based decoders (rtl_fm, multimon-ng, rtl_433, dump1090, acarsdec,
airodump-ng, slowrx, SatDump, AIS-catcher, direwolf, etc.) MUST be cleaned up
via `safe_terminate()` and MUST use the established global locks to prevent
race conditions when multiple requests touch the same SDR or process.
Features MUST degrade gracefully — a clear error/status, not a crash or hung
request — when expected hardware (an SDR dongle, a specific radio) is
absent, busy, or disconnected. Long-running streams (SSE queues, decoder
threads) MUST NOT leak file descriptors, threads, or unbounded queues across
repeated start/stop cycles.

**Rationale**: INTERCEPT orchestrates a dozen-plus native tools against
shared, often single-instance hardware (a single RTL-SDR dongle, a single
radio front end). One leaking or hung integration must not degrade or take
down the other signal modes running in the same process.

### VI. Input Validation & Secure-by-Default

Every external input that influences a subprocess invocation, file path, or
query (frequency, gain, device index, filenames, user-supplied strings) MUST
be validated via `utils/validation.py` before use. Subprocess calls MUST NOT
use `shell=True` with unsanitized input, and MUST avoid the OWASP Top 10
classes relevant to this codebase: command/argument injection, path
traversal into `data/`/`instance/`, and template/XSS issues in Jinja
templates. New settings or endpoints MUST default to the least-privileged,
least-surprising behavior.

**Rationale**: A platform that exists to spawn native binaries against
attacker-observable RF/network data is a natural target for injection if any
input path skips validation; this principle keeps that guarantee uniform
across every mode rather than re-litigated per route.

## Technology & Platform Constraints

- **Runtime**: Python 3, Flask application (`app.py`), served via
  `gunicorn` + `gevent` in production (`start.sh`) or the Flask dev server
  for quick local iteration (`intercept.py`). Real-time features use
  Server-Sent Events over greenlets under gunicorn+gevent.
- **Packaging/deployment**: Docker is the primary supported path
  (single-stage `Dockerfile` compiling SDR tools from source), with
  `docker-compose.yml` `basic` and `history` profiles, and
  `build-multiarch.sh` producing amd64 + arm64 (Raspberry Pi 5) images.
  `setup.sh` remains the supported non-Docker install path and MUST stay
  functional for `--non-interactive` and `--profile=` installs.
- **Storage**: SQLite in `instance/` is the default and MUST remain the
  zero-config path; Postgres is opt-in only for ADS-B history under the
  `history` profile and MUST NOT become a hard dependency of core features.
- **External tool integrations**: The tool integration table in CLAUDE.md
  is the source of truth for supported native tools and their integration
  method (subprocess pipe, socket, JSON parsing, etc.). Any change to how a
  tool is invoked or parsed MUST keep that table accurate.
- **Configuration**: All new configuration MUST be exposed via the
  `INTERCEPT_`-prefixed environment variable convention in `config.py`,
  with a safe default — no feature may require manual config editing to
  retain prior behavior after an upgrade.

## Development Workflow & Quality Gates

- Before a change is considered complete: `ruff check .`, `black --check .`,
  `mypy .`, and `pytest` MUST all pass locally.
- Bug fixes follow reproduce → failing test → fix → passing test (Principle
  II). Feature work states its verification criteria up front and is not
  "done" until that criteria is checked, not just implemented.
- For UI/frontend changes, the dev server MUST be exercised in a browser for
  the golden path and relevant edge cases before reporting completion; a
  passing test suite verifies code correctness, not feature correctness.
- Plans produced by `/speckit-plan` MUST run the Constitution Check gate
  against all six Core Principles before Phase 0 research and again after
  Phase 1 design; any violation MUST be justified in Complexity Tracking or
  the plan MUST be simplified until it complies.
- Commits and other side-effectful actions (pushes, force operations,
  deleting data) follow the confirmation and safety rules already in force
  for this environment; this constitution does not loosen them.

## Governance

This constitution is authoritative over ad hoc practice for this
repository. CLAUDE.md continues to provide supplementary, project-specific
runtime guidance (build commands, module map, coding conventions); where the
two overlap they are intended to agree, and CLAUDE.md MUST be kept
consistent with amendments made here.

**Amendment procedure**: Amendments are made by updating this file via the
`/speckit-constitution` command (or equivalent direct edit reviewed the same
way), including an updated Sync Impact Report describing what changed and
why, and a version bump per the policy below.

**Versioning policy** (semantic versioning applied to governance):
- **MAJOR**: Backward-incompatible removal or redefinition of a principle,
  or a change that relaxes a NON-NEGOTIABLE guarantee.
- **MINOR**: A new principle or section is added, or existing guidance is
  materially expanded.
- **PATCH**: Wording clarifications, typo fixes, and non-semantic
  refinements that don't change what is required.

**Compliance review**: Every feature plan MUST explicitly verify compliance
with all Core Principles at the Constitution Check gate. Reviewers MUST
treat a failing gate as a blocker, not a note, unless the plan documents an
accepted, justified exception in Complexity Tracking.

**Version**: 1.0.0 | **Ratified**: 2026-07-31 | **Last Amended**: 2026-07-31
