# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The v0.2 pivot to "agent search box that cuts API costs" is documented in
[roadmap_v0.2.md](./roadmap_v0.2.md). Work is gated behind a Phase 0
measurement spike; no v0.2 features have shipped yet.

## [0.1.0] — 2026-05-20

The initial public release. Ayiru is a verified, machine-readable
knowledge layer for AI agents — purpose-built to give agents
deterministic safety verdicts on destructive commands, with cited
evidence and a full audit trail.

### Added

#### Trust + verification core
- Stage 0 — Locked product contract and trust taxonomy. Initial tool
  coverage: `git`, `github-cli`, `docker`, `vercel-cli`, `openai-api`,
  plus 5 curated MCP servers. Versioned JSON contracts under
  `contracts/`.
- Stage 1 — Domain model and persistence foundation. Pydantic v2 +
  SQLAlchemy 2.0 + Alembic. Drift between models and migrations
  enforced by `tests/test_alembic_metadata_alignment.py`.
- Stage 2 — Claim submission and retrieval API. Typed `KnowledgeClaim`
  + `Evidence` with rejected-primary-evidence policy.
- Stage 3 — Canon orchestrator: schema validation, dedup, conflict
  detection.
- Stage 4 — Evidence + confidence discipline. Weighted scoring, caps,
  conflict penalties, contract-versioned model.
- Stage 5 — Deterministic risk engine. Dimension-based classifier
  driven entirely by `contracts/risk_model.v1.json` — no LLM in the
  safety path.
- Stage 6 — Canonical `ToolSpec` / `WorkflowSpec` compilation with
  byte-level provenance back to source claims.

#### Ingestion lanes (SSRF-safe)
- Stage 7a — CLI ingestion via subprocess sandbox with argv allowlist.
- Stage 7b — Docs ingestion via HTTPS-only fetch with SSRF guard +
  sanitisation + content-type allowlist.
- Stage 7c.1 — OpenAPI schema ingestion with JSON Pointer provenance.
- Stage 7c.2 — JSON Schema ingestion, dialect-aware.
- Stage 7c.3 — GraphQL SDL ingestion with destructive-action
  detection on mutation fields.
- Stage 7d — MCP server metadata ingestion via local stdio spawn +
  `tools/list` capture.

#### Runtime + query
- Stage 8 — Runtime verification. L2 → L3 promotion via safe sandboxed
  checks (tool existence, CLI flag existence, HTTP HEAD against
  allowlisted endpoints, MCP tool existence).
- Stage 9 — Agent query surface. `validate_command`, `search_tools`,
  `explain_risk`, `safe_workflow`, `get_tool_spec`. Default-deny on
  no match; matcher uses strict exact + prefix-with-word-boundary;
  three-gate auto-execute decision (safety policy ∧ confidence ≥ 0.70 ∧
  verification level ≥ L2).
- Stage 10 — Hand-rolled MCP server. Speaks JSON-RPC over stdio.
  Exposes 6 tools to Claude Desktop / Cursor / Cline / Continue.
  Defensive handling of `notifications/*` with stray ids,
  non-dict tool-handler returns, and falsy non-dict `arguments`.

#### Demo + distribution
- Stage 11a — Seed dataset. Pre-captured artifacts replay through
  the ingestion lanes; populates ~47 claims across 5 tools in a
  fresh checkout.
- Stage 11b — Next.js 14 demo dashboard with landing page, tools
  list, tool detail, and a `validate_command` playground. Same-origin
  proxy via `next.config.mjs` — no CORS configuration required.
- Stage 12 — `ayiru` CLI. One binary on PATH after install;
  subcommands `serve`, `mcp`, `seed`, `migrate`, `query`, `verify`,
  `tools`. One-stage Dockerfile for container deploys.

#### Human review + audit
- Stage 13 — Human Review and Auditability.
  - `POST /verification/human-review` files an `APPROVED` / `REJECTED`
    / `NEEDS_CHANGES` decision against a claim.
  - `APPROVED` against an L3+ claim promotes it to
    `L5_HUMAN_AUDITED`; below L3 the review is recorded but the
    promotion is refused with an explanatory reason.
  - `REJECTED` flips the claim's `verification_status` so the matcher
    excludes it from auto-execution surfaces.
  - `GET /verification/review-queue` paginates claims awaiting review,
    filterable by tool / risk level.
  - `GET /audit/events` and `GET /audit/claims/{claim_id}` expose the
    append-only audit log with structured filters.
  - **Append-only invariant**: no `update_audit_event` or
    `delete_audit_event` method exists on `ClaimStore`. Enforced
    by an introspection test.

#### Hardening
- Stage 14 — Production-grade polish.
  - Wheel bundles `contracts/`, `data/seed_artifacts/`, and the
    alembic migrations as package data. Clean-venv `pip install`
    works end-to-end with no checkout required.
  - `/v1/` API versioning. Legacy un-versioned paths stay alive
    for one transition release with RFC 8594 `Deprecation` /
    `Sunset` / `Link` headers.
  - `RequestObservabilityMiddleware` — mints `X-Request-ID` per
    request (or echoes a client-supplied one), emits one structured
    JSON log line per request to the `ayiru.request` logger.
  - Optional `AYIRU_API_KEY` Bearer-token auth on writes; reads
    + health endpoints stay public. Timing-safe key comparison via
    `hmac.compare_digest`.
  - Optional `AYIRU_REVIEWER_REGISTRY` allowlist gates the
    human-review endpoint.
  - `ayiru serve` auto-applies pending migrations on startup
    (`--no-migrate` opts out).
  - Postgres dialect offline smoke (`tests/test_postgres_dialect_smoke.py`)
    confirms every table + migration renders cleanly under the
    `postgresql+psycopg` dialect.
  - `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
    `.github/workflows/ci.yml` (pytest + ruff on Python 3.11/3.12,
    migration roundtrip, clean-venv wheel install smoke,
    `next build` for the frontend).
  - `CODE_OF_CONDUCT.md`, `.github/FUNDING.yml`, issue + PR templates
    (Phase B prep, included in v0.1.0).

### Validation

- **693 backend tests passing** across all 14 stages. Ruff clean.
- Alembic upgrade → downgrade → upgrade cycle clean through
  `0015_human_review_and_audit_log`.
- Clean-venv `pip install` end-to-end smoke verified:
  `pip install <wheel>` → `ayiru migrate` → `ayiru seed --reset`
  populates 47 claims → `ayiru query --tool github-cli --command
  'gh repo delete my-org/x --yes'` returns the expected critical-block
  verdict.

### Known limitations (will be addressed in v0.2+)

- SQLite is the only test-matrix dialect. Postgres support is verified
  offline but not exercised live.
- API-key auth is single-tenant only; OAuth / OIDC / per-key rate
  limiting deferred to v0.2.
- Reviewer registry is a string allowlist; cryptographic per-reviewer
  identity deferred to v0.2.
- No semantic / lexical retrieval `ask` endpoint yet (the v0.2 pivot).
  v0.1 ships the safety-arbiter surface only.
- Tool coverage is curated (Stage 0 lock). Adding tools is a contract
  change, not a code change; documented in `CONTRIBUTING.md`.
- No PyPI release under the original `ayiru` package name (the
  name was taken between project inception and v0.1 release).
  See [issue tracker] for the renamed package coordinates.

[Unreleased]: ../../compare/v0.1.0...HEAD
[0.1.0]: ../../releases/tag/v0.1.0
