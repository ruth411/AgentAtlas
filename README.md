# AgentAtlas

AgentAtlas is an agent-maintained, orchestrator-verified knowledge graph for AI-agent tool intelligence.

The project is currently in the backend trust-foundation phase. It can ingest structured claims and evidence, persist them in SQLite, retrieve them through HTTP, run deterministic verification through the Canon Orchestrator, attach persisted risk/confidence explanations to verification results, and publish accepted claims into persisted canonical specs.

## Current Stage Status

- Stage 0: pass. Product lock and trust contract are defined.
- Stage 1: pass. Schemas, SQLite persistence, and Alembic migration scaffolding exist; migration parity is locked by a drift test.
- Stage 2: pass. Claim and evidence submission/retrieval APIs are stable; submit-time evidence policy is enforced; `created_at` is server-assigned.
- Stage 3: pass. The Canon Orchestrator persists verification results, updates claim status/confidence, detects duplicates/conflicts, challenges understated risk, exposes verification retrieval APIs, and is idempotent after the first verification result. Verification level is capped at `L2_source_verified` until Stage 8.
- Stage 4: pass. Confidence scorer with weighted evidence, diminishing returns, hard caps, conflict penalty, and an inspectable `ConfidenceBreakdown` attached to every verification result. Spam, duplicate, low-trust, single-type, and high-risk-thin-evidence cases cannot produce inflated acceptance.
- Stage 5: pass. Risk classification is now deterministic, contract-backed, dimension-based, persisted as `risk_assessment`, copied to claims as `risk_level_classified` for query parity, and covered by word-boundary, aggregation, contract, migration, and replay-determinism tests.
- Stage 6: pass. Accepted claims compile into persisted canonical `ToolSpec` and `WorkflowSpec` records with deterministic artifact/content hashes, provenance, publication issues, and publish/retrieve/list APIs.
- Stage 7a (Safe CLI Ingestion): pass. Captures allowlisted `git` and GitHub CLI help/version output through a streamed, byte-capped runner, stores raw artifacts, derives claim statements from the captured output's first non-empty line, hashes evidence, and verifies via the orchestrator without auto-publishing canonical specs. Includes a positive-shape argv allowlist with adversarial regression tests, a bulk-ingest endpoint, and a real-binary smoke test.
- Stage 7b (Docs ingestion): pass. Fetches allowlisted https-only documentation URLs for each Stage 0 tool via an `httpx` client with full SSRF guards (host allowlist, manual redirect re-validation, private/loopback/link-local IP rejection, public-DNS verification), strict HTML sanitization (drops script/style/iframe/object/embed/template/noscript content; emits text only), allowed content-type enforcement, max-bytes cap, hard timeout, ETag/Last-Modified conditional revalidation with persisted `docs_fetch_cache`, and a bulk-per-tool endpoint. 37 adversarial tests cover SSRF, redirect attacks, oversized responses, script-only pages, and cache-hit reuse.
- Stage 7c.1 (OpenAPI schema ingestion): pass. Fetches allowlisted https-only OpenAPI 3.x specs for each gated tool via an `httpx` client with the shared SSRF guard (host allowlist, manual redirect re-validation, private/loopback/link-local IP rejection, public-DNS verification), strict content-type allowlist, max-bytes cap, hard timeout, `openapi-spec-validator` meta-schema validation, ETag/Last-Modified conditional revalidation via the shared `docs_fetch_cache`, and a bulk-per-tool endpoint. Emits one `api_endpoint_exists` claim per (method, path) plus auxiliary `auth_requirement` / `side_effect` / `destructive_action` / `feature_deprecated` claims, each with a JSON Pointer (RFC 6901) fragment in the evidence `source_uri` for byte-level audit. 41 adversarial tests cover SSRF, redirect attacks, oversized payloads, malformed / non-OpenAPI-3 / schema-invalid bodies, empty specs, cache hit reuse, bulk ingest, and structured 422 from the API.
- Stage 7c.2 (JSON Schema ingestion): pass. Fetches allowlisted https-only JSON Schema documents (Draft 4 / 6 / 7 / 2019-09 / 2020-12) from `json.schemastore.org` (and per-tool official_hosts when applicable) via an `httpx` client with the shared SSRF guard, manual redirect re-validation, JSON content-type allowlist, max-bytes cap, hard timeout, `jsonschema`-driven meta-schema validation against the dialect declared in `$schema`, ETag/Last-Modified conditional revalidation via the shared `docs_fetch_cache`, and a bulk-per-tool endpoint. Emits one `config_field_exists` claim per top-level property in the schema's `properties` map, plus auxiliary `feature_deprecated` claims for properties marked `deprecated: true`, each with a JSON Pointer (RFC 6901) fragment in the evidence `source_uri` for byte-level audit. Trust resolver was extended with `schema_aggregator_hosts` so schemastore-hosted schemas resolve to HIGH trust without widening the docs/openapi lanes. 37 adversarial tests cover SSRF, redirect attacks, oversized payloads, malformed / non-JSON / non-dict-root / meta-schema-invalid bodies, no-properties schemas, required-vs-optional reflection, deprecation annotation, JSON Pointer escaping, cache hit reuse, bulk ingest, and structured 422 from the API.
- Stage 7c.3 (GraphQL SDL ingestion): pass. Fetches allowlisted https-only GraphQL SDL documents from each tool's `official_hosts` (e.g. `docs.github.com/public/fpt/schema.docs.graphql`) via an `httpx` client with the shared SSRF guard, manual redirect re-validation, SDL content-type allowlist, max-bytes cap, hard timeout, full SDL parse + type-system validation via `graphql-core`'s `build_schema`, ETag/Last-Modified conditional revalidation via the shared `docs_fetch_cache`, and a bulk-per-tool endpoint. Emits one `api_endpoint_exists` claim per root operation field (Query / Mutation / Subscription) plus auxiliary `side_effect` for every Mutation, `destructive_action` for Mutations whose name starts with a contract-listed destructive prefix (`delete`, `remove`, `destroy`, etc.), and `feature_deprecated` for fields carrying `@deprecated`. Risk: Query / Subscription → LOW, Mutation → HIGH, destructive Mutation → CRITICAL. Evidence `source_uri` carries `#<OperationType>.<fieldName>` so an auditor can trace any assertion to its SDL location. 40 adversarial tests cover SSRF, redirect attacks, oversized payloads, malformed SDL, no-root-operations schemas, per-operation risk mapping, destructive-prefix detection, deprecation annotation, cache reuse, bulk ingest, and structured 422 from the API.
- Stage 7d (MCP metadata ingestion): pass. Spawns allowlisted MCP servers locally over stdio (positive-shape argv allowlist + placeholder substitution like `{sandbox_dir}` / `{database_url}`, only `npx` / `uvx` permitted), drives the standard JSON-RPC `initialize` handshake + `tools/list` call with hard time and byte caps, captures the full JSON-RPC payload as a durable `mcp_tool_list` artifact, and emits one `mcp_tool_exists` claim per advertised tool plus auxiliary `side_effect` for non-`readOnlyHint` tools, `destructive_action` for `destructiveHint: true` tools and tools whose name matches a contract-listed destructive prefix (`delete`, `remove`, `destroy`, etc.), and `feature_deprecated` for annotated or description-flagged deprecations. Risk mapping: read-only → LOW, mutating → HIGH, destructive → CRITICAL. Stage 0 contract was extended with a parallel `mcp_server_tools` list (preserving the original `initial_tools` lock); the ClaimStore tool_id gate unions both. Initial coverage: 5 servers (`mcp-filesystem`, `mcp-fetch`, `mcp-git`, `mcp-slack`, `mcp-postgres`) across two publishers. 30 adversarial tests cover contract gate, argv allowlist + placeholder substitution, malformed initialize / tools/list, non-array tools, missing tool name, runner exceptions, per-tool-cap, all risk-mapping branches, deprecation, bulk-by-publisher, and structured 422 from the API. Production runner is `SafeMcpServerRunner` (subprocess + stdio JSON-RPC); tests run hermetically via a `FakeMcpRunner` so they don't need `npx` / `uvx` installed.
- Stage 8 (Runtime Verification): pass. Promotes claims from `L2_source_verified` to `L3_runtime_verified` by actually running a safe deterministic check against the asserted behaviour and persisting the captured output as durable audit evidence. Five verifier kinds: `tool_exists` / `cli_command_exists` spawn `<tool> --version` and expect exit 0; `cli_flag_exists` spawns `<tool> --help` (or contract-equivalent) and greps for the asserted `--flag` token; `api_endpoint_exists` issues a single HTTP HEAD through the shared SSRF guard with the tool's `official_hosts` as allowlist; `mcp_tool_exists` re-spawns the originating MCP server via the Stage 7d runner and confirms the tool is still listed. Claim types whose verification would trigger a side effect (`destructive_action`, `side_effect`, `auth_requirement`, `feature_deprecated`, `workflow_step`, `config_field_exists`, `environment_requirement`) are deliberately skipped with a reason — we never invoke a destructive command to "verify" it. Sandbox is `SubprocessSandboxRunner`: positive-shape argv allowlist (only `git` / `gh` / `docker` / `vercel` / `npx` / `uvx`), scrubbed env (`PATH` + `HOME` only), wall-clock + byte caps, guaranteed terminate-then-kill cleanup. Each verification emits a `sandbox_execution_log` artifact (replayable JSON payload of command, argv, exit code, stdout, stderr, duration) plus a new L3 `VerificationResult` referencing it; failed runtime checks emit a `REQUIRES_HUMAN_REVIEW` result with a confidence penalty so the audit chain stays intact. Pre-check refuses to runtime-verify claims still below L2. 27 adversarial tests cover the precheck, all five verifier branches, the deliberate skip list (parametrised), bulk-by-tool, sandbox allowlist enforcement, and structured 422 from the API. Production sandbox / HTTP / MCP runners are never invoked during tests; hermetic fakes are used so CI doesn't need `git`, `npx`, or remote hosts available.

## Backend Setup

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Validation

```bash
cd backend
.venv/bin/python -m pytest tests
.venv/bin/ruff check app tests
```

## Database

Local development defaults to SQLite:

```text
backend/agentatlas.db
```

Override the database URL with:

```bash
AGENTATLAS_DATABASE_URL=sqlite:////tmp/agentatlas.db
```

Tables are managed by Alembic, not by `Base.metadata.create_all`. Before
starting the API for the first time, run:

```bash
cd backend
AGENTATLAS_DATABASE_URL=sqlite:////tmp/agentatlas.db .venv/bin/alembic upgrade head
```

If you already have an old local `backend/agentatlas.db` from before the Stage
4 migrations, delete it and recreate it through Alembic. Old local rows may
contain pre-hardening hash values such as `sha256:abc123`, which are now
invalid.

Migration parity with the SQLAlchemy models is enforced by
`tests/test_alembic_metadata_alignment.py`.

## API Surface

- `GET /health`
- `POST /claims`
- `GET /claims`
- `GET /claims/{claim_id}`
- `GET /claims/{claim_id}/evidence`
- `GET /evidence/{evidence_id}`
- `POST /claims/{claim_id}/verify`
- `GET /claims/{claim_id}/verification`
- `GET /verification-results`
- `POST /canonical/tools/{tool_id}/publish`
- `GET /canonical/tools`
- `GET /canonical/tools/{tool_id}`
- `POST /canonical/workflows/{workflow_id}/publish`
- `GET /canonical/workflows`
- `GET /canonical/workflows/{workflow_id}`
- `POST /ingestion/cli`
- `GET /ingestion/runs`
- `GET /ingestion/runs/{run_id}`
- `GET /ingestion/artifacts/{artifact_id}`

## What This Is Not Yet

- Not a complete production verification engine
- Not a runtime sandbox
- Not an MCP server
- Not a dashboard

Those belong to later roadmap stages.
