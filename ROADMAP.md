# AgentAtlas Roadmap

This roadmap is optimized for one thing: not building a polished fraud.

Every stage exists to prove the next one is worth building. If a stage does not pass, do not move ahead anyway.

## Core Rule

Do not treat activity as progress.

Progress means a capability exists, is testable, fails honestly, and produces structured output another agent can rely on.

Current implementation status is tracked in [Stage Report](docs/stage_report.md).

## Stage 0: Product Lock and Trust Contract

This stage defines what the product is, what it is not, and what "verified" actually means.

Stage 0 artifacts:

- [Product Lock](docs/product_lock.md)
- [Trust Contract](docs/trust_contract.md)
- [Demo Scenarios](docs/demo_scenarios.md)
- [Stage Report](docs/stage_report.md)
- [Machine-Readable Stage 0 Contract](contracts/agentatlas_stage_0.v1.json)

### Capabilities Built

- Canonical vocabulary for `KnowledgeClaim`, `Evidence`, `ToolSpec`, `WorkflowSpec`, `VerificationLevel`, and `RiskLevel`
- Fixed claim taxonomy
- Fixed evidence taxonomy
- Fixed verification ladder from `L0` to `L5`
- Fixed orchestrator decision types
- Fixed safety policy semantics
- Fixed demo scenarios for `git`, `gh`, `docker`, `vercel`, and one API provider

### Pass Cases

- There is zero ambiguity about what counts as a claim
- There is zero ambiguity about what counts as evidence
- "Verified" has a strict meaning, not a vibe
- "Accepted", "rejected", "pending", and "requires human review" are contractually defined
- The team can explain why a claim is valid or invalid without inventing new rules midstream

### Do Not Advance If

- Different examples imply different standards
- "Evidence" still includes LLM reasoning as primary proof
- Risk language is fuzzy

## Stage 1: Domain Model and Persistence Foundation

This stage builds the non-negotiable data backbone.

Stage 1 artifacts:

- [Stage Report](docs/stage_report.md)

### Capabilities Built

- Typed schemas for claims, evidence, tool specs, workflows, provenance, verification results, and safety policy
- Enum-driven validation for claim types, risk levels, evidence types, verification levels, and statuses
- Local persistence with SQLite
- Clean path to Postgres later
- Structured error contracts
- Migration setup
- Stable serialization contracts

### Pass Cases

- Invalid claims are rejected deterministically
- Valid claims serialize and deserialize predictably
- Evidence records are attached, stored, and queryable
- Data survives restart
- Schema tests cover required fields, enum constraints, and invalid combinations
- Error responses are structured and specific

### Do Not Advance If

- State is still ephemeral unless that is explicitly temporary and isolated
- Payload rules are enforced inconsistently
- You cannot trust the stored shape of your own data

## Stage 2: Claim Submission and Retrieval API

This stage makes the system ingest structured submissions without guessing.

Stage 2 artifacts:

- [Stage Report](docs/stage_report.md)

### Capabilities Built

- `POST /claims`
- `GET /claims`
- `GET /claims/{claim_id}`
- Evidence persistence tied to claim persistence
- Duplicate claim handling
- Query by tool, status, risk, and submitter
- Predictable request and response models

### Pass Cases

- A valid claim can be submitted and retrieved unchanged except for system-managed fields
- Invalid submissions fail with clear field-level errors
- Duplicate claim IDs are handled explicitly
- Claims default to the correct initial verification state
- Evidence is not lost or silently rewritten
- API tests cover success, failure, duplicate, and not-found paths

### Do Not Advance If

- The API accepts malformed payloads
- Error contracts are vague
- Retrieval behavior changes depending on hidden implementation details

## Stage 3: Trust Core, Part 1, Orchestrator Skeleton

This stage introduces the machine that decides whether claims deserve movement.

Stage 3 artifacts:

- [Stage Report](docs/stage_report.md)

### Capabilities Built

- Canon Orchestrator service
- Schema validation stage
- Evidence presence check
- Duplicate detection
- Conflict detection scaffold
- Decision outputs: `accepted`, `rejected`, `pending_more_evidence`, `duplicate`, `conflict_detected`, `requires_human_review`
- Reason codes attached to every decision

### Pass Cases

- No claim can be "accepted" without passing the orchestrator
- A claim with no evidence does not get promoted
- Duplicate claims are detected deterministically
- Conflicting claims can be flagged rather than silently merged
- Every orchestrator decision includes explicit reasons
- Tests cover each decision branch

### Do Not Advance If

- Orchestrator output is mostly manual or arbitrary
- Acceptance logic is hidden in route handlers or ad hoc helpers
- Rejections do not explain themselves

## Stage 4: Trust Core, Part 2, Evidence and Confidence Discipline

This stage prevents the system from rewarding weak evidence.

### Capabilities Built

- Evidence trust scoring
- Confidence scoring with hard caps
- Source weighting by evidence type
- Support for conflicting evidence penalties
- Confidence band semantics tied to actionability
- Proven rules for when a claim may move from `L1` to `L2`

### Pass Cases

- Missing evidence lowers confidence meaningfully
- Weak evidence cannot produce inflated acceptance
- High-risk claims with weak evidence get blocked or routed to review
- Confidence output is reproducible from inputs
- Score explanations are inspectable
- Tests prove that confidence cannot be gamed by adding low-value evidence spam

### Do Not Advance If

- Confidence is just a decorative number
- Strong-sounding prose can overpower weak evidence
- High-risk claims can still slip through on thin proof

## Stage 5: Risk Engine

This stage is where the product either becomes real or collapses into a documentation toy.

### Capabilities Built

- Deterministic risk classifier
- Detection of destructive verbs and irreversible actions
- Classification of local vs remote mutation
- Classification of auth sensitivity
- Classification of secret exposure risk
- Classification of cost-incurring actions
- Recommendation of `requires_confirmation`
- Machine-readable explanations for risk decisions

### Pass Cases

- `git status` is classified as low or none
- `gh repo delete` is classified as critical
- `vercel --prod` is classified as high
- Risk classification is rule-driven, inspectable, and test-covered
- Risk reasoning is available in structured output, not only prose
- High and critical actions are blocked from auto-execute recommendations

### Do Not Advance If

- Risk is based on vibes or string-matching alone without policy structure
- The classifier cannot explain itself
- Critical commands can still look "safe enough"

## Stage 6: Canonical Publication, ToolSpec and WorkflowSpec Compilation

This stage turns accepted claims into a usable knowledge layer.

### Capabilities Built

- Deterministic `ToolSpec` compiler
- Deterministic `WorkflowSpec` compiler
- Provenance stitching from evidence to claims to canonical outputs
- Verification level propagation
- Support for commands, capabilities, auth, side effects, failure modes, and recovery steps
- Regeneration of canonical specs from accepted claims only

### Pass Cases

- A `ToolSpec` can be rebuilt from accepted claims without manual editing
- Rejected or pending claims do not leak into canonical publication
- Provenance is preserved end-to-end
- Verification level on the spec matches the underlying evidence reality
- Tool output is machine-readable and stable
- Compiler tests cover missing data, conflicting data, and partial data

### Do Not Advance If

- Published specs contain hand-written truth that bypassed claims
- Provenance is incomplete
- Output changes unpredictably from the same input set

## Stage 7: Safe Ingestion Layer

This stage makes the repo truly "agent-maintained," but only under strict limits.

The original Stage 7 promised four ingestion agents (CLI, Docs, API schema, MCP).
To keep each lane honest and independently auditable, Stage 7 is split into four
sub-stages. Each sub-stage carries the same trust-contract requirements: structured
claims with durable evidence, raw artifact capture, no execution of dangerous
commands during discovery, and docs treated as data not instructions.

### Common Pass Cases (all sub-stages)

- The ingested lane produces structured claims, not loose summaries.
- Raw evidence can be inspected after the fact (artifact retrieval endpoint).
- No unsafe action is executed during ingestion.
- Captured content is treated as data, not as executable instruction.
- Tests prove that prompt-injection-like content cannot redirect the ingester.

### Common Do Not Advance If

- Ingestion is broad but shallow within the lane.
- Agents generate claims without durable evidence.
- The system executes dangerous commands during discovery.

---

### Stage 7a: Safe CLI Ingestion

#### Capabilities Built

- CLI ingestion agent for safe help, version, and read-only inspection.
- Streamed subprocess runner with positive-shape argv allowlist, byte-cap,
  hard timeout, empty env, and no shell.
- Allowlist contract per locked Stage 0 tool.
- Raw `agentatlas://` artifact storage with content hash.
- Claim creation from captured output and orchestrator verification.
- Bulk-ingest endpoint to refresh every allowlisted command for a tool.

#### Pass Cases

- At least `git` and `gh` can be ingested safely.
- Single-command and bulk-tool endpoints both work end to end.
- Allowlist contract drift (e.g. argv that does not end in a read-only marker,
  or whose binary is a destructive command) is rejected at contract load.

---

### Stage 7b: Docs Ingestion — SHIPPED

#### Capabilities Built

- Docs ingestion agent for trusted documentation extraction.
- HTTPS-only httpx fetcher constrained to the per-tool `official_hosts` allowlist
  defined in `contracts/tool_trust_sources.v1.json`, plus a separate
  `contracts/docs_ingestion_sources.v1.json` listing the exact URLs per tool.
- Full SSRF guard: rejects non-https, non-allowlisted hosts, private / loopback /
  link-local / multicast / reserved IPs (literals and resolved hostnames).
- Manual redirect handling: httpx auto-redirects disabled; every hop is
  re-validated against the same scheme + host + IP rules; `max_redirects`
  enforced.
- Stdlib HTML sanitization (`html.parser`): drops `script`, `style`, `iframe`,
  `object`, `embed`, `template`, `noscript`, `svg` content; emits text only;
  HTML entities decoded; whitespace collapsed.
- Content-type allowlist enforced (`text/html`, `text/plain`, `text/markdown`,
  `application/xhtml+xml`); other types rejected.
- Hard `max_bytes` cap and `timeout_seconds` cap per fetch.
- Persistent `docs_fetch_cache` table with ETag + Last-Modified;
  `If-None-Match` / `If-Modified-Since` revalidation reuses the prior artifact
  on 304 without storing a new raw body.
- Raw response stored as `raw_ingestion_artifacts` row with `docs_content`
  artifact type, sha256 hash, and `agentatlas://` source URI for audit.
- Redirect chain surfaced on `DocsIngestionResponse` for caller audit.
- Bulk endpoint `POST /ingestion/docs/tools/{tool_id}` runs every allowlisted
  URL for a tool.

#### Pass Cases

- Trusted docs for the locked Stage 0 tools can be ingested without arbitrary
  URL fetching. ✓
- Redirects to non-allowlisted hosts are rejected, not followed. ✓
- Excerpts never contain executable HTML/JS. ✓

#### Verification

37 adversarial tests in `tests/test_docs_ingestion.py` covering: SSRF
(scheme, host allowlist, private IP literals, private resolved IPs),
redirect chain abuse (cross-host, missing Location, too many hops),
sanitization (scripts, styles, iframes, event handlers, entity decode,
whitespace collapse), content-type enforcement, max-bytes overflow,
empty / scripts-only body, transport timeout, HTTP 5xx, cache hit/miss,
cache entry orphaned by deleted artifact, bulk ingest happy/error paths,
and structured 422 from the API.

---

### Stage 7c: API Schema Ingestion

Stage 7c is split into three sub-stages so each schema family can be shipped
and audited on its own merits:

- **7c.1 — OpenAPI 3.x — SHIPPED.** Per-endpoint claim generation with auxiliary
  auth / side-effect / destructive / deprecated annotations, JSON Pointer
  (RFC 6901) provenance back to the originating spec node, shared SSRF guard,
  contract-gated source allowlist, manual redirect re-validation,
  ETag / Last-Modified conditional revalidation via the shared
  `docs_fetch_cache`, schema validation via `openapi-spec-validator`, and 41
  adversarial tests covering bad schemes, private/loopback/link-local IPs,
  redirect attacks, oversized payloads, malformed JSON, non-OpenAPI-3 specs,
  invalid OpenAPI schemas, empty / no-operation specs, cache reuse, bulk
  ingest, and structured 422 from the API.
- **7c.2 — JSON Schema — SHIPPED.** Per-top-level-property claim generation
  with auxiliary `feature_deprecated` annotations, JSON Pointer provenance
  back to the originating schema node, shared SSRF guard, contract-gated
  source allowlist with new `schema_aggregator_hosts.json_schema` so
  `json.schemastore.org` is trusted for JSON Schema evidence without leaking
  trust into other lanes, dialect-specific meta-schema validation via
  `jsonschema.validator_for($schema)`, ETag/Last-Modified revalidation via
  the shared `docs_fetch_cache`, and 37 adversarial tests covering bad
  schemes, redirect attacks, oversized / malformed / non-dict-root / meta-
  schema-invalid bodies, no-properties schemas, required-vs-optional
  reflection, JSON Pointer escaping, aggregator-host trust resolution, cache
  reuse, bulk ingest, and structured 422 from the API.
- **7c.3 — GraphQL SDL — SHIPPED.** Per-root-field claim generation
  (Query / Mutation / Subscription) with auxiliary `side_effect` /
  `destructive_action` / `feature_deprecated` annotations, fragment-style
  `#<OperationType>.<fieldName>` provenance back to the SDL location, shared
  SSRF guard, contract-gated source allowlist (vendor `official_hosts`
  only — no aggregator block), manual redirect re-validation, full SDL parse
  + type-system validation via `graphql-core`'s `build_schema`,
  ETag/Last-Modified revalidation via the shared `docs_fetch_cache`,
  contract-driven destructive-prefix detection (`delete`, `remove`,
  `destroy`, etc.) that upgrades the matching Mutation primary claim to
  `CRITICAL`, and 40 adversarial tests covering bad schemes, redirect
  attacks, oversized payloads, malformed SDL, no-root-operations schemas,
  per-operation risk mapping, destructive detection, deprecation, cache
  reuse, bulk ingest, and structured 422 from the API.

#### Capabilities Built (7c.1)

- OpenAPI 3.x ingestion agent (`openai-api` tool, contract-gated source).
- Schema validation via `openapi-spec-validator` before any claim generation.
- One `api_endpoint_exists` claim per (method, path), plus auxiliary
  `auth_requirement` / `side_effect` / `destructive_action` /
  `feature_deprecated` claims as warranted by the operation.
- Evidence `source_uri` includes a JSON Pointer fragment to the exact spec
  node that grounded each claim.
- Cache reuses the Stage 7b `docs_fetch_cache` for conditional revalidation.

#### Pass Cases

- An `openai-api` OpenAPI spec produces `api_endpoint_exists` claims with
  structured side-effect annotations and JSON Pointer provenance.
- Malformed JSON, non-OpenAPI-3 specs, and OpenAPI specs that fail the
  meta-schema validator are rejected with structured errors and create no
  claims.

---

### Stage 7d: MCP Metadata Ingestion — SHIPPED

Stage 7d is shipped. AgentAtlas can now spawn allowlisted MCP servers
locally over stdio, drive the standard JSON-RPC `initialize` + `tools/list`
handshake, capture the full reply as a durable audit artifact, and emit
structured claims per advertised tool with risk hints derived from
annotations and tool-name patterns.

#### Capabilities Built

- Stdio JSON-RPC client for MCP servers, with hard wall-clock + byte caps
  and guaranteed subprocess termination on the success and failure paths.
- Per-server positive-shape argv allowlist plus a limited placeholder
  vocabulary (`{sandbox_dir}`, `{database_url}`) so the contract gates both
  which servers and how they are invoked.
- Stage 0 contract expansion: new `mcp_server_tools[]` list (5 entries:
  `mcp-filesystem`, `mcp-fetch`, `mcp-git`, `mcp-slack`, `mcp-postgres`)
  parallel to the original `initial_tools` list; `ClaimStore.create` gate
  unions both.
- Trust contract extension: each MCP tool carries an `mcp_publisher` field
  (`anthropic` / `third-party`).
- Claim generation per MCP tool: primary `mcp_tool_exists` plus auxiliary
  `side_effect` (when not `readOnlyHint: true`), `destructive_action` (when
  `destructiveHint: true` or the tool name matches a contract-listed
  destructive prefix), and `feature_deprecated` (annotation or description
  heuristic). Risk mapping: read-only → LOW, mutating → HIGH, destructive →
  CRITICAL.
- `POST /ingestion/mcp` + `POST /ingestion/mcp/publishers/{publisher}` API
  routes returning structured `MCP_INGESTION_FAILED` (422) on rejection.
- 30 adversarial tests covering the spawn allowlist, JSON-RPC reply
  handling, per-tool risk branches, deprecation, bulk-by-publisher, and the
  API surface. Tests run hermetically via a `FakeMcpRunner` so they do not
  require `npx` / `uvx` to be installed.

#### Pass Cases

- An allowlisted MCP server (e.g., `mcp-filesystem`) can be ingested into a
  structured `mcp_tool_exists` claim per advertised tool, with the full
  JSON-RPC payload recoverable as a `mcp_tool_list` audit artifact.
- A spawn command outside `allowed_commands` is rejected at both contract
  validation time and runtime (`SafeMcpServerRunner` sanity check).
- A misbehaving server that exceeds the time or byte cap is killed cleanly
  without leaking the subprocess.

---

## Stage 8: Runtime Verification Layer — SHIPPED

Stage 8 is shipped. AgentAtlas can now promote claims from
`L2_source_verified` to `L3_runtime_verified` by actually running a safe,
deterministic check against the asserted behaviour and persisting the
captured stdout / stderr / exit code as durable audit evidence. Dangerous
claim types (`destructive_action`, `side_effect`, etc.) are deliberately
skipped — we never invoke a destructive command to "verify" it.

### Capabilities Built

- `SandboxRunner` protocol with a `SubprocessSandboxRunner` production
  implementation: positive-shape argv allowlist (only `git` / `gh` /
  `docker` / `vercel` / `npx` / `uvx`), scrubbed env (`PATH` + `HOME`),
  wall-clock + byte caps, guaranteed terminate-then-kill cleanup. The
  runner is injectable so future stages can swap in Docker / Firecracker /
  Modal sandboxes without rewriting the verifier registry.
- Five concrete verifiers: `tool_exists` and `cli_command_exists` spawn
  `<tool> --version`; `cli_flag_exists` spawns help and greps for the
  asserted flag token; `api_endpoint_exists` issues a single HTTP HEAD
  through the shared SSRF guard with the tool's `official_hosts` as the
  allowlist; `mcp_tool_exists` re-spawns the originating MCP server via
  the Stage 7d runner and confirms the tool is still listed.
- Deliberate skip-list for claim types whose verification would require
  triggering a side effect; each emits `skipped=True` with a reason.
- L3 promotion path: successful checks emit a new `VerificationResult`
  with `verification_level=L3_runtime_verified`, `decision=ACCEPTED`,
  `+0.10` confidence bonus, and a reference to the saved
  `sandbox_execution_log` artifact. Failed checks emit
  `REQUIRES_HUMAN_REVIEW` with `-0.20` penalty and the captured artifact
  still saved.
- Pre-check refuses claims below `L2_source_verified` (the
  promotion-rule contract is enforced, not just trusted).
- `POST /verification/runtime` + `POST /verification/runtime/tools/{tool_id}`
  routes returning structured `RUNTIME_VERIFICATION_FAILED` (422) on
  rejection. Bulk endpoint reports `attempted` / `promoted` / `skipped` /
  `failed` counts.
- 27 adversarial tests covering the precheck, all five verifier branches
  (pass / fail / skip), the deliberate skip list (parametrised), bulk-by-
  tool, sandbox allowlist enforcement, and the API surface. Hermetic via
  fake sandbox / HTTP / MCP runners.

### Pass Cases

- A claim like `cli_command_exists` for `git status` (already at L2) can be
  runtime-verified by spawning `git --version` and promotes to L3 with the
  raw output as `sandbox_execution_log` audit evidence.
- A claim like `destructive_action` for `gh repo delete` is deliberately
  refused at the verifier-registry level, with a clear skip reason — we
  never invoke a destructive command to "verify" it.
- A failed runtime check (non-zero exit, timeout, HEAD 500, missing MCP
  tool) does NOT promote the claim's level but DOES record the captured
  output as audit evidence and emits a `REQUIRES_HUMAN_REVIEW` result.

### Do Not Advance If

- Runtime verification can drift into unsafe execution → blocked by the
  positive-shape `allowed_commands` allowlist + the explicit skip list for
  side-effecting claim types.
- The system claims `L3` without a real runtime artifact → impossible:
  every L3 result references a captured `sandbox_execution_log` artifact.
- Sandbox policy is permissive enough to undermine the whole product →
  the only commands the production runner can spawn are 6 well-known
  developer tools, and each spawn is bounded by time and byte caps.

## Stage 9: Agent Query Surface — SHIPPED

Stage 9 is shipped. AgentAtlas now exposes a high-level agent-facing API
that turns the raw `KnowledgeClaim` graph into structured, evidence-backed
safety verdicts. The five endpoints under `/query/*` are the surface other
agents (and the future MCP wrapper in Stage 10) will actually call.

### Capabilities Built

- `POST /query/validate-command` — the headline endpoint. Strict exact +
  prefix matching against accepted claims; three-tier verdict gating
  (safety policy ∧ confidence ≥ 0.70 ∧ verification level ≥ L2); reasons
  list explains every gate that fired; default-deny on no match
  (`risk_level: null`, never an "unknown" magic string).
- `GET /query/tools/{tool_id}` — canonical `ToolSpec` retrieval with regex
  path validation; structured 404 if no spec is published.
- `GET /query/search-tools` — tiered substring scoring (tool_id exact >
  tool_id substring > name substring > capability substring), sorted
  score-DESC then `tool_id`-ASC for deterministic order. Pagination clamped
  to contract `max_search_limit`.
- `POST /query/explain-risk` — deterministic risk classification plus six
  boolean dimensions (destructive_action / mutates_remote_state /
  reversible / requires_auth / may_cost_money / may_expose_secrets) plus
  citing claim ids when a matching claim exists.
- `POST /query/safe-workflow` — substring + token-overlap matching against
  `WorkflowSpec.goal`, sorted safest-first (`aggregate_risk` ASC).
- `contracts/query_policy.v1.json` — versioned policy (confidence +
  verification thresholds, length caps, search limits, reason-text
  templates). Validated on import, drift-locked to a test.
- `command_matcher` module with strict longest-prefix-with-word-boundary
  matching, ACCEPTED-only visibility, paginated through claims with a
  10k hard cap, batched-window-function lookup of latest verifications
  (one SQL per page instead of N).
- 92 tests across 4 files; all hermetic. Suite total now 552 passing.

Notably NOT included (deliberate v1.1 scope):

- Fuzzy / LLM-assisted command matching (would re-introduce nondeterminism
  in the safety path).
- `submit_claim` exposed under `/query` (claims are still managed via the
  existing `POST /claims` — no read endpoint mutates).
- Bulk validate-command (one-at-a-time is sufficient for the demo).
- SQL-side filtering for search-tools / safe-workflow (load-all-then-filter
  in memory is fine at v1 scale; optimise at 10k+ specs).

### Pass Cases

- An agent asks `validate_command("github-cli", "gh repo delete ...")` and
  gets `{safe_to_auto_execute: false, risk_level: "critical", reasons: [...]}`
  with cited evidence and `verification_level=L3_runtime_verified`.
- A no-match query returns the default-deny envelope with `risk_level: null`
  and a clear reason — no false reassurance.
- A low-risk claim with confidence below 0.70 is correctly gated to
  `safe_to_auto_execute: false`, with the confidence threshold spelled
  out in the response.
- Risk classifier upgrades understated risk (claim was submitted as LOW
  but the classifier rates it CRITICAL → verdict reflects CRITICAL, reasons
  mention the upgrade).
- Search-tools returns matches in deterministic score-DESC then tool_id-ASC
  order; pagination is consistent across pages.
- Path-param and body-supplied `tool_id` both reject the same garbage
  with the same 422 envelope (post-audit consistency fix).

### Do Not Advance If

- The response is mostly prose → **enforced:** every endpoint returns a
  typed JSON model; `extra="forbid"` everywhere.
- Command validation does not reference verification state → **enforced:**
  every verdict carries `verification_level` and reasons.
- Safe workflow recommendation is just a generic checklist → **enforced:**
  responses include `aggregate_risk_level`, `verification_level`, and
  `requires_confirmation` per workflow.

### Audit Summary

Nine real bugs were caught during Stage 9's audit and fixed before ship.
See `docs/stage_report.md`'s Stage 9 detail block for the full per-bug
breakdown. Pattern callouts:

- One critical integration bug: the matcher's status filter hid most of
  the ingestion pipeline's output (single-evidence claims that land at
  status=PENDING were invisible to validate-command). Caught only by a
  full end-to-end probe; per-component tests passed because they
  synthesised ACCEPTED states directly.
- Three contract-↔-code drift bugs (band threshold function duplicated,
  contract validator gaps on reason-text keys, route/schema hardcoded
  limits diverging from the contract). All now drift-locked by tests.
- One API consistency bug (path-param vs body-param 404-vs-422 mismatch)
  caught by deliberate cross-endpoint probing.
- One pagination silent-drop bug (matcher capping at 500) and one
  performance bug (N+1 verification lookup) caught by realistic-scale
  probes.

## Stage 10: MCP Server and External Interoperability

This stage turns AgentAtlas from a backend app into agent infrastructure.

### Capabilities Built

- MCP server exposing the core query surface
- Stable MCP tool schemas
- Contract-safe tool invocation patterns
- Integration tests using external-client-like flows
- Clear separation between canonical knowledge and advisory prose

### Pass Cases

- An external agent can query tool specs and command safety through MCP
- MCP outputs remain stable across repeated runs
- High-risk actions produce explicit warnings and confirmation requirements
- Tool schemas are machine-usable, not human-only
- Integration tests prove end-to-end behavior from query to result

### Do Not Advance If

- MCP is just a thin wrapper over unstable internals
- Outputs are not schema-stable
- External agents would need special-case hacks to consume responses

## Stage 11: Failure-Oriented Validation

This stage exists to stop self-deception.

### Capabilities Built

- Adversarial claim tests
- Conflicting evidence scenarios
- Incomplete documentation scenarios
- Ambiguous command interpretation cases
- Unknown or deprecated flag cases
- Misleading README or injected instruction cases
- Honest failure states and degraded-mode behavior

### Pass Cases

- The system can say "unknown," "insufficient evidence," or "requires human review" cleanly
- Conflicting evidence does not get silently flattened into a confident answer
- Deceptive documentation does not become executable truth
- Ambiguity reduces confidence rather than generating fake certainty
- Failure behavior is visible and structured

### Do Not Advance If

- The system still tries to answer everything confidently
- Conflict handling is cosmetic
- Failure modes are hidden from consumers

## Stage 12: Demonstration Layer

This is where you prove the product to humans, not where you invent it.

### Capabilities Built

- Dashboard for claims, evidence, tool specs, workflows, risk, and verification states
- Filters by tool, claim status, risk, and verification level
- Provenance and lineage views
- Demo script that shows ingestion, verification, compilation, and agent query
- Seed corpus for `git`, `gh`, `docker`, `vercel`, and one API provider

### Pass Cases

- A viewer can understand the whole pipeline quickly
- The UI does not hide uncertainty or rejected claims
- The demo includes one safe command and one dangerous command with correct classification
- The dashboard reflects actual backend state, not curated screenshots
- Seed data is strong enough to show real behavior, not empty placeholders

### Do Not Advance If

- The UI is compensating for backend weakness
- The demo only shows happy paths
- The product still cannot prove why a claim is trusted

## Stage 13: Human Review and Auditability

This stage is what separates a clever prototype from something teams may trust.

### Capabilities Built

- Maintainer review queue
- Human approval and rejection workflow
- Audit logs
- Claim revision history
- Explicit `L5_human_audited` path
- Review notes attached to canonical decisions

### Pass Cases

- Human review is traceable
- Canonical knowledge can be audited back to exact claims and evidence
- Overrides are visible rather than silent
- Audit history survives edits and republishes
- Human-reviewed knowledge is meaningfully distinct from machine-only acceptance

### Do Not Advance If

- Human review is just a manual toggle
- Audit trails can be lost or overwritten
- You cannot explain why a published spec changed

## Stage 14: Hardening

Only now should the system start acting like durable infrastructure.

### Capabilities Built

- Postgres production path
- Migrations discipline
- Background jobs where needed
- Observability and error reporting
- Auth and rate limiting if externalized
- Clear API versioning
- Publication isolation from ingestion failures
- Retry semantics and idempotency rules

### Pass Cases

- The system handles failures without corrupting publication state
- Deployments do not require hand-editing the database
- Operational behavior is observable
- API contracts are stable enough to support real clients
- Canonical data integrity survives partial failures

### Do Not Advance If

- One failing ingestion path can poison the whole system
- Operations are still "run it and hope"
- Versioning and migration discipline are absent

## Must-Have Non-Goals

Do not build these early:

- A chatbot frontend as the main product
- Broad web crawling before trust core maturity
- Semantic retrieval as a substitute for canonical structured knowledge
- Neo4j before the relational model becomes a real limitation
- Flashy UI before provenance, verification, and risk are real
- Agent framework complexity before deterministic orchestration is working

## Real Pass Condition for the Whole Project

AgentAtlas is only credible when another AI agent can ask:

- Does this command exist?
- What does it do?
- What are the risks?
- What evidence supports that?
- Has it been verified?
- What is the safer workflow?

The system must answer in structured, provenance-backed, safety-aware form, while also being able to say "I do not know" when the evidence is not good enough.
