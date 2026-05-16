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

### Capabilities Built

- CLI ingestion agent for safe help, version, and read-only inspection
- Docs ingestion agent for trusted documentation extraction
- API schema ingestion agent for OpenAPI, JSON Schema, and GraphQL sources
- MCP metadata ingestion agent where applicable
- Evidence capture and hashing
- Raw artifact storage for later audit
- Allowlists for runtime inspection

### Pass Cases

- At least `git` and `gh` can be ingested safely
- Ingestion produces structured claims, not loose summaries
- Raw evidence can be inspected after the fact
- Unsafe commands are not executed during ingestion
- Docs are treated as data, not instructions
- Ingestion tests prove that prompt-injection-like content is ignored as executable instruction

### Do Not Advance If

- Ingestion is broad but shallow
- Agents generate claims without durable evidence
- The system executes dangerous commands during discovery

## Stage 8: Runtime Verification Layer

This stage upgrades the system from source-aware to behavior-aware.

### Capabilities Built

- Sandbox execution framework
- Runtime verification for allowlisted, deterministic, low-risk checks
- Captured runtime evidence
- Upgrade path from `L2_source_verified` to `L3_runtime_verified`
- Cross-agent or multi-source agreement path toward `L4`
- Strict execution policy boundaries

### Pass Cases

- Safe runtime checks can confirm command existence, help output, or no-op behaviors
- Runtime evidence is stored like any other evidence
- Dangerous actions are never executed just to "verify" them
- Verification levels are raised only when actual runtime proof exists
- Tests cover successful runtime verification and blocked execution paths

### Do Not Advance If

- Runtime verification can drift into unsafe execution
- The system claims `L3` without a real runtime artifact
- Sandbox policy is permissive enough to undermine the whole product

## Stage 9: Agent Query Surface

This stage makes AgentAtlas useful to another agent before any UI polish.

### Capabilities Built

- `search_tools`
- `get_tool_spec`
- `validate_command`
- `get_safe_workflow`
- `submit_claim`
- `explain_risk`
- Structured JSON-first responses
- Environment-aware safe workflow recommendation

### Pass Cases

- Another agent can ask "can I run this command?" and get a structured answer
- Risk, confirmation requirement, verification level, and rationale are returned explicitly
- Safe alternatives can be suggested where appropriate
- High-risk commands are clearly flagged as not auto-executable
- Query API tests cover normal, risky, unknown, and conflicting cases

### Do Not Advance If

- The response is mostly prose
- Command validation does not reference verification state
- Safe workflow recommendation is just a generic checklist

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
