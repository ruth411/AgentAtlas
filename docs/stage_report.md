# AgentAtlas Stage Report

This report consolidates the completion status for every shipped stage of AgentAtlas (currently 0 through 8).

It records what each stage proves, which artifacts satisfy the stage, what remains deferred, and which validation commands must pass.

> **Last audit:** Full-pass code audit of Stages 7c.1 / 7c.2 / 7d / 8 found and fixed **5 real bugs** the test suite had missed (two `304 Not Modified` cache-reuse failures, one MCP stderr-pipe deadlock, one SSRF bypass in the runtime HEAD verifier, and one CLI subcommand-extraction error). All fixes have regression tests. See the per-stage "Audit findings" subsections below.

## Overall Verdict

| Stage | Name | Verdict |
| --- | --- | --- |
| Stage 0 | Product Lock and Trust Contract | pass |
| Stage 1 | Domain Model and Persistence Foundation | pass |
| Stage 2 | Claim Submission and Retrieval API | pass |
| Stage 3 | Canon Orchestrator Skeleton | pass |
| Stage 4 | Evidence and Confidence Discipline | pass |
| Stage 5 | Deterministic Risk Engine and Structural Prep | pass |
| Stage 6 | Canonical Publication and Spec Compilation | pass |
| Stage 7a | Safe CLI Ingestion Layer | pass |
| Stage 7b | Safe Docs Ingestion Layer | pass |
| Stage 7c.1 | OpenAPI schema ingestion | pass |
| Stage 7c.2 | JSON Schema ingestion | pass |
| Stage 7c.3 | GraphQL SDL ingestion | pass |
| Stage 7d | MCP metadata ingestion | pass |
| Stage 8 | Runtime Verification (L2 → L3) | pass |
| Stage 9 | Agent Query Surface | pass |
| Stage 10 | MCP Server (outbound) | pass |
| Stage 11a | Seed Dataset (offline-safe replay) | pass |
| Stage 11b | Demo Dashboard (Next.js) | pass |

## Stage 0: Product Lock and Trust Contract

Verdict: pass.

Stage 0 is complete when the product lock and trust contract are explicit, testable, and hard to reinterpret later.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `docs/product_lock.md` | Defines product identity, thesis, initial scope, principles, and anti-goals. | complete |
| `docs/trust_contract.md` | Defines vocabulary, claim taxonomy, evidence taxonomy, verification rules, risk semantics, decisions, and publication rules. | complete |
| `docs/demo_scenarios.md` | Defines proof scenarios for the five initial tools. | complete |
| `contracts/agentatlas_stage_0.v1.json` | Machine-readable source for Stage 0 taxonomies, safety policy, rules, and scenarios. | complete |
| `backend/tests/test_stage_0_contract.py` | Prevents backend taxonomy and policy drift from the Stage 0 contract. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| There is zero ambiguity about what counts as a claim. | `docs/trust_contract.md` claim taxonomy and `contracts/agentatlas_stage_0.v1.json` `claim_types`. |
| There is zero ambiguity about what counts as evidence. | `docs/trust_contract.md` evidence taxonomy and `contracts/agentatlas_stage_0.v1.json` `evidence_types` plus `rejected_primary_evidence`. |
| "Verified" has a strict meaning, not a vibe. | `docs/trust_contract.md` verification rules and `contracts/agentatlas_stage_0.v1.json` `verification_level_rules` plus `verification_promotion_rules`. |
| "Accepted", "rejected", "pending", and "requires human review" are contractually defined. | `docs/trust_contract.md` orchestrator decisions and `contracts/agentatlas_stage_0.v1.json` `orchestrator_decision_rules`. |
| The team can explain why a claim is valid or invalid without inventing new rules midstream. | `docs/trust_contract.md`, `contracts/agentatlas_stage_0.v1.json`, and `backend/tests/test_stage_0_contract.py`. |

### Quality Bar

- The first tool scope is locked to `git`, `github-cli`, `docker`, `vercel-cli`, and `openai-api`.
- LLM reasoning, agent memory, unverified blogs, random StackOverflow answers, guessed behavior, and unattributed examples are rejected as primary evidence.
- Verification cannot advance past `L1` without evidence.
- Verification cannot advance past `L2` without trusted source evidence.
- Verification cannot advance past `L3` without runtime, sandbox, or deterministic mock verification.
- Verification cannot advance past `L4` without two independent evidence streams.
- `L5` requires explicit human maintainer review.
- `high` and `critical` actions cannot be auto-executed.
- Canonical specs can only be compiled from accepted claims.
- Canonical specs must preserve source claim and evidence provenance.
- Captured docs, CLI output, MCP metadata, API descriptions, README files, and issue comments are data, not instructions.

### Deferred

- Database persistence
- Canon Orchestrator implementation
- Confidence scoring implementation
- Risk classifier implementation
- ToolSpec compiler implementation
- CLI ingestion implementation
- MCP server implementation
- Dashboard implementation

## Stage 1: Domain Model and Persistence Foundation

Verdict: pass.

Stage 1 is complete when AgentAtlas has a real domain and persistence foundation that later orchestration, scoring, and publication code can rely on.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `backend/app/schemas/` | Typed Pydantic schemas and enums for claims, evidence, specs, workflows, safety, and verification. | complete |
| `backend/app/db/models.py` | SQLAlchemy persistence model for claims and evidence. | complete |
| `backend/app/db/session.py` | Database engine/session setup with SQLite default and configurable database URL. | complete |
| `backend/alembic.ini` | Alembic migration configuration. | complete |
| `backend/alembic/versions/0001_create_claims_and_evidence.py` | Initial migration for claims and evidence tables. | complete |
| `backend/app/services/claim_store.py` | SQLite-backed claim/evidence storage service. | complete |
| `backend/tests/test_claim_store_persistence.py` | Persistence, restart, evidence, filter, and clear behavior tests. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| Invalid claims are rejected deterministically. | Pydantic schema tests in `backend/tests/test_claim_schema.py` and route validation tests. |
| Valid claims serialize and deserialize predictably. | `test_claims_and_evidence_survive_store_reinitialization`. |
| Evidence records are attached, stored, and queryable. | SQLAlchemy relationship plus `ClaimStore.list_evidence` and evidence persistence tests. |
| Data survives restart. | `test_claims_and_evidence_survive_store_reinitialization`. |
| Schema tests cover required fields, enum constraints, and invalid combinations. | Existing schema tests plus Stage 0 contract alignment tests. |
| Error responses are structured and specific. | Claim route tests for invalid payloads, duplicates, and not-found responses. |

### Quality Bar

- Local development uses SQLite.
- The database layer is SQLAlchemy-based so a Postgres path does not require rewriting domain storage.
- Migrations are explicit through Alembic, and migration parity with `Base.metadata` is locked by `tests/test_alembic_metadata_alignment.py`.
- `ClaimStore` no longer auto-creates tables on the production path; production must run `alembic upgrade head`.
- Schema-level `field_validator`s reject naive datetimes for `created_at` and `captured_at`, and reject future-dated `captured_at`.
- Excerpt length is capped at 8000 characters for both `Evidence` and `EvidenceCreate`.
- Claims and evidence are persisted in separate tables.
- Evidence remains traceable to its source claim.
- Query filters exist for tool, verification status, risk level, claim type, and submitter.
- Generated database files, caches, virtualenvs, and build metadata are ignored.

### Deferred

- Postgres-specific validation
- Canonical spec tables
- Human review tables

## Stage 2: Claim Submission and Retrieval API

Verdict: pass.

Stage 2 is complete when agents and maintainers can submit, retrieve, filter, and inspect claims and attached evidence through stable HTTP contracts.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `backend/app/schemas/claim.py` | Defines `ClaimCreate` for submissions and `KnowledgeClaim` for stored claims. | complete |
| `backend/app/api/errors.py` | Centralizes structured API error payloads and error codes. | complete |
| `backend/app/api/routes_claims.py` | Implements claim creation, claim listing, claim retrieval, evidence retrieval, and claim verification endpoint. | complete |
| `backend/app/services/claim_store.py` | Persists claims and evidence and supports query filters. | complete |
| `backend/tests/test_claim_routes.py` | Covers success, validation, duplicate, not-found, filter, evidence, rollback, ID generation, and verification behavior through HTTP. | complete |
| `backend/tests/test_claim_store_persistence.py` | Covers persistence, restart behavior, evidence queries, filters, clear behavior, duplicate evidence rollback, semantic duplicates, and conflicts. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| A valid claim can be submitted and retrieved unchanged except for system-managed fields. | `test_post_claim_sets_pending_status_and_null_confidence` and `test_get_claim_by_id_returns_claim`. |
| Invalid submissions fail with clear field-level errors. | `test_post_claim_rejects_invalid_schema_with_structured_error` and `test_post_claim_rejects_system_managed_fields`. |
| Duplicate claim IDs are handled explicitly. | `test_post_claim_rejects_duplicate_claim_id`. |
| Claims default to the correct initial verification state. | `ClaimCreate` excludes system-managed status/confidence and route creation forces `pending` plus `null` confidence. |
| Evidence is not lost or silently rewritten. | `test_get_claim_evidence_returns_attached_evidence` and store persistence tests. |
| API tests cover success, failure, duplicate, and not-found paths. | `backend/tests/test_claim_routes.py`. |

### Quality Bar

- Claim creation uses `ClaimCreate`, not `KnowledgeClaim`, so clients cannot set `verification_status`, `confidence`, or `created_at`.
- `created_at` is server-assigned at submission time; client-supplied values are rejected as unknown fields.
- API errors consistently return `{"error": {"code": "...", "message": "...", "details": {}}}`.
- Tests use dependency overrides with temporary SQLite databases instead of clearing the local development database.
- Claim list filters exist for `tool_id`, `verification_status`, `risk_level`, `claim_type`, and `submitted_by`.
- Evidence is retrievable through `GET /claims/{claim_id}/evidence`.
- Evidence is independently retrievable through `GET /evidence/{evidence_id}`.
- Duplicate evidence insertion fails atomically and does not leave a partially stored claim.
- Duplicate evidence returns `EVIDENCE_ALREADY_EXISTS`; duplicate claims return `CLAIM_ALREADY_EXISTS`.
- Evidence relying on rejected primary sources (LLM URLs, `stackoverflow.com`, blog hostnames) is rejected at submit-time with `REJECTED_PRIMARY_EVIDENCE`.
- Invalid query parameters return structured API errors.
- `evidence=[]` is allowed at submission only for pending intake; the orchestrator must keep it at `pending_more_evidence` and it must not publish.

### Deferred

- Auth and submitter identity validation
- Pagination
- API versioning
- ToolSpec compilation
- Runtime verification
- CLI ingestion
- MCP server
- Dashboard

## Stage 3: Canon Orchestrator Skeleton

Verdict: pass.

Stage 3 is complete when submitted claims can be deterministically verified into persisted, explainable decisions without publishing canonical specs yet.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `backend/app/services/orchestrator.py` | Canon Orchestrator decision service. | complete |
| `backend/app/services/risk_classifier.py` | Deterministic risk challenge for submitted claims. | complete |
| `backend/app/services/evidence_policy.py` | Evidence sufficiency and rejected-source policy. | complete |
| `backend/app/services/confidence_scorer.py` | Conservative confidence scoring. | complete |
| `backend/app/db/models.py` | Includes `VerificationResultRecord`. | complete |
| `backend/alembic/versions/0002_create_verification_results.py` | Adds persistent verification results table. | complete |
| `backend/app/services/claim_store.py` | Persists verification results and updates claim status/confidence. | complete |
| `backend/app/api/routes_claims.py` | Exposes verification execution and retrieval endpoints. | complete |
| `backend/tests/test_trust_core_services.py` | Covers orchestrator branches and risk/confidence behavior. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| No claim can be accepted without passing the orchestrator. | `POST /claims/{claim_id}/verify` creates a `VerificationResult`; claim status changes only after verification. |
| A claim with no evidence does not get promoted. | `test_orchestrator_requires_more_evidence_when_claim_has_no_evidence`. |
| Duplicate claims are detected deterministically. | `find_semantic_duplicates` and `test_orchestrator_detects_duplicate_claims`. |
| Conflicting claims can be flagged rather than silently merged. | `find_conflicts` and `test_orchestrator_detects_conflicting_claims`. |
| Every orchestrator decision includes explicit reasons. | `VerificationResult.reason_codes` and `VerificationResult.reasons`. |
| Tests cover each decision branch. | `test_trust_core_services.py` and verification route tests. |

### Quality Bar

- Verification uses the Stage 5 deterministic risk engine. The old substring-marker classifier has been removed.
- Evidence policy enforced at both submit-time (HTTP 422 `REJECTED_PRIMARY_EVIDENCE`) and orchestrator-time (defensive).
- Confidence scorer with caps for high/critical claims that have fewer than two evidence streams.
- Canon Orchestrator skeleton.
- Verification level is capped at `L2_source_verified`. `L3_runtime_verified` requires the Stage 8 sandbox; `L4_cross_agent_verified` requires Stage 4's independence checks. Submitted evidence alone cannot promote past `L2`.
- High-risk and critical claims auto-accept only when confidence ≥ 0.85 (verified by `test_orchestrator_accepts_high_risk_claim_with_two_trusted_evidence_streams`).
- Semantic duplicate detection normalizes subject case and whitespace before matching.
- Conflict detection.
- Verification endpoint: `POST /claims/{claim_id}/verify` — idempotent after the first verification result, including `pending_more_evidence` results (returns the latest result; no duplicate rows).
- Verification retrieval endpoint: `GET /claims/{claim_id}/verification`.
- Verification result listing endpoint: `GET /verification-results`.
- Persistent verification results.
- Claim `verification_status` and `confidence` update when verification is saved.

### Deferred

- Human review queue
- ToolSpec compiler
- Runtime verification (Stage 8) — required before any L3 promotion.
- Cross-agent verification (Stage 11 adversarial tests) — required before any L4 promotion.
- Persistent audit events beyond verification results.

## Stage 4: Evidence and Confidence Discipline

Verdict: pass.

Stage 4 is complete when the confidence scorer cannot be gamed by spam,
strong-sounding prose, or thin evidence, and when every score is
reproducible and inspectable.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `backend/app/schemas/enums.py` (`ConfidenceBand`) | Maps numeric scores to actionability bands. | complete |
| `backend/app/schemas/confidence.py` | `ConfidenceBreakdown` and `ConfidenceComponent`, the inspectable output of every scoring call. | complete |
| `backend/app/services/confidence_scorer.py` | Weighted scorer with diminishing returns, hard caps, and a deterministic breakdown. | complete |
| `contracts/tool_trust_sources.v1.json` | Versioned trusted-host and source-repository allowlists for the locked Stage 0 tools. | complete |
| `backend/app/services/evidence_trust.py` | Server-side trust resolver that prevents clients from inflating evidence trust using the versioned trust-source contract. | complete |
| `backend/app/schemas/verification.py` | Verification results carry `confidence_band` and `confidence_breakdown`. | complete |
| `backend/app/schemas/claim.py` | Claims carry the latest `confidence_band` for query-side actionability. | complete |
| `backend/alembic/versions/0003_add_confidence_band_and_breakdown.py` | Database columns for band + breakdown, with check constraints. | complete |
| `backend/app/services/claim_store.py` | Persists and restores band + breakdown JSON round-trip. | complete |
| `backend/app/services/orchestrator.py` | Uses the breakdown, attaches it to results, and gates L1 → L2 promotion on the band. | complete |
| `backend/tests/test_stage_4_confidence.py` | Covers the Stage 4 invariants below, including adversarial trust-label gaming. | complete |
| `backend/tests/test_tool_trust_sources_contract.py` | Proves trust-source data covers Stage 0 tools and drives server trust resolution. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| Missing evidence lowers confidence meaningfully. | `test_empty_evidence_yields_zero_score_and_none_band` returns `score=0.0`, `band=NONE`. |
| Weak evidence cannot produce inflated acceptance. | `test_spam_of_same_evidence_type_diminishes_quickly`, `test_duplicate_source_uri_and_hash_only_counted_once`, `test_low_trust_only_evidence_is_hard_capped`, `test_single_evidence_type_is_hard_capped_even_with_many_items`. |
| High-risk claims with weak evidence get blocked or routed to review. | `test_high_risk_claim_with_thin_evidence_is_capped_below_acceptance`, `test_duplicate_source_uri_is_counted_once_before_high_risk_gate`, `test_high_risk_claim_without_strong_trusted_type_stays_below_acceptance`. |
| Confidence output is reproducible from inputs. | `test_breakdown_is_reproducible_from_same_inputs`. |
| Score explanations are inspectable. | `test_orchestrator_attaches_breakdown_to_verification_result` and `test_orchestrator_persists_breakdown_through_store` (round-trip). |
| Tests prove that confidence cannot be gamed by adding low-value evidence spam. | Spam, duplicate, fake high-trust source, and unknown-source tests above. |

### Quality Bar

- The scorer is **closed over structured fields only**. It never reads `excerpt` prose; `test_breakdown_unaffected_by_excerpt_prose` enforces this.
- Submitted `trust_level` is no longer authoritative. `evidence_trust.py` derives the maximum allowed trust from `tool_id`, `evidence_type`, `source_uri`, and `contracts/tool_trust_sources.v1.json`. The submitted trust level can lower trust, but it cannot raise trust above the server-derived cap.
- Trusted source allowlists are data, not application code. The versioned trust-source contract covers the locked Stage 0 tools: `git`, `github-cli` / `gh`, `docker`, `vercel-cli` / `vercel`, and `openai-api`.
- Unknown domains are downgraded instead of treated as high trust. Rejected primary evidence sources still fail submit-time policy validation.
- Local `cli_help_output` and local `man_page` captures are capped at `MEDIUM` trust until later layers can prove stronger provenance.
- `sandbox://` and `maintainer-review://` evidence is rejected until Stage 8 and Stage 13 add real issuers/signatures. They cannot self-assert even `MEDIUM` trust.
- Evidence hashes must use strict `sha256:<64 lowercase hex chars>` format; short pseudo-hashes are rejected.
- Each unique evidence record contributes weight = contract-defined `evidence_type_weights[type]` × `trust_multipliers[trust]` × `diminishing_factors[occurrence_index]`.
- Duplicate `source_uri` or duplicate `hash` records are dropped before scoring. A submitter cannot inflate confidence by reusing the same source with a new evidence ID.
- The second occurrence of any single evidence type contributes 40% of the first; further occurrences contribute zero. Spam is provably bounded.
- Hard caps (each declared in `caps_applied` only when binding):
  - `empty_evidence` → score `0.0`.
  - `single_evidence_type:0.65` when all evidence shares one type.
  - `all_low_trust:0.45` when every record has trust `LOW`.
  - `high_risk_single_stream:0.50` when risk is high/critical and fewer than two distinct streams exist.
  - `high_risk_no_diverse_trusted:0.80` when risk is high/critical and no record is in `{official_docs, maintainer_review, sandbox_execution, openapi_schema, source_code, man_page}`.
  - `conflict_detected:0.40` whenever the orchestrator flags a conflict against existing claims.
- Confidence bands:
  - `NONE`: score `< 0.30`
  - `LOW`: `0.30 ≤ score < 0.55`
  - `MODERATE`: `0.55 ≤ score < 0.75`
  - `HIGH`: `0.75 ≤ score < 0.90`
  - `STRONG`: `0.90 ≤ score ≤ 1.00`
- L1 → L2 promotion requires both trusted source evidence and a band of at least `LOW`. `NONE`-band claims stay at `L1_schema_valid` with reason `INSUFFICIENT_CONFIDENCE_FOR_L2`.
- `ACCEPTED` requires at least a `MODERATE` confidence band for all claims. `LOW`-band claims may reach `L2_source_verified`, but remain `pending_more_evidence` with `INSUFFICIENT_CONFIDENCE_FOR_ACCEPTANCE`.
- High and critical risk claims auto-accept only when score is `>= 0.85` (band `HIGH` or `STRONG`).
- Conflict-detected results carry `conflict_detected:0.40` in `caps_applied`.
- Understated-risk caps add a synthetic negative `cap:understated_risk` component when binding, so component deltas reconcile to the final capped score.
- The breakdown is persisted as JSON on `verification_results.confidence_breakdown` and restored unchanged on read (`test_orchestrator_persists_breakdown_through_store`).
- `KnowledgeClaim.confidence_band` is updated when a verification result is saved so query-side consumers see the same band the orchestrator computed.
- `test_fake_official_docs_high_trust_is_downgraded_by_server_rules` proves fake official-docs URLs cannot keep client-submitted high trust.
- `test_unknown_high_trust_sources_cannot_accept_high_risk_claim` proves high-risk claims cannot be accepted using unknown sources that self-label as high trust.
- `test_pre_stage_runtime_and_maintainer_evidence_cannot_self_assert_high_trust` proves submitted sandbox/review evidence is rejected before the real runtime and maintainer-review stages exist.
- `test_source_repo_allowlist_requires_exact_repo_path` proves source-code allowlists cannot be bypassed with repository-prefix tricks.

### Deferred

- Cross-agent verification and L4 promotion (Stage 11 adversarial tests).
- Runtime verification and L3 promotion (Stage 8 sandbox framework).
- Human-audited L5 path (Stage 13 maintainer review).
- Confidence decay over time (out of scope; revisit during Stage 14 hardening if needed).

## Stage 5: Deterministic Risk Engine and Structural Prep

Verdict: pass.

Stage 5 is complete when risk classification is deterministic, explainable, contract-backed, persisted in verification results, and no longer based on unstructured substring markers.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/risk_model.v1.json` | Versioned universal and per-tool risk rules with regex patterns, dimensions, reasons, and risk levels. | complete |
| `contracts/confidence_model.v1.json` | Versioned confidence weights, trust multipliers, diminishing factors, band thresholds, and caps. | complete |
| `backend/app/schemas/risk.py` | Defines `RiskDimension`, `RiskMatch`, and expanded `RiskAssessment`. | complete |
| `backend/app/services/risk_engine.py` | Deterministic rule engine loaded from the risk-model contract. | complete |
| `backend/app/services/risk_classifier.py` | Compatibility wrapper that delegates to the risk engine while preserving `RISK_ORDER` and `is_higher_risk`. | complete |
| `backend/alembic/versions/0004_add_risk_assessment_to_verification_results.py` | Adds persisted `verification_results.risk_assessment`. | complete |
| `backend/alembic/versions/0005_add_classified_risk_to_claims.py` | Adds query-side `risk_level_classified` and `risk_assessment` fields to claims. | complete |
| `backend/app/services/confidence_scorer.py` | Loads scoring semantics from `contracts/confidence_model.v1.json`. | complete |
| `backend/tests/test_risk_engine.py` | Covers canonical cases, boundaries, aggregation, dimensions, fallback, reproducibility, cache, and invalid contracts. | complete |
| `backend/tests/test_risk_model_contract.py` | Validates risk model structure and Stage 0 tool coverage. | complete |
| `backend/tests/test_confidence_model_contract.py` | Validates confidence model enum coverage, thresholds, caps, and legacy value preservation. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| Risk is not classified by vibes or raw substring markers. | `risk_classifier.py` delegates to `risk_engine.compute_risk_assessment`; rule data lives in `contracts/risk_model.v1.json`. |
| Every risk result explains what fired. | `RiskAssessment` carries `dimensions`, `matched_rule_ids`, `reasons`, and `matches`. |
| Word-boundary false positives are pinned. | Tests prove `git merger-tool` does not match `merge` behavior and `redeploy-status` does not match deploy behavior. |
| Universal dangerous patterns work without tool context. | Tests cover `rm -rf`, `terraform destroy`, `kubectl delete`, and `--force`. |
| Per-tool rules cover the locked Stage 0 tools. | `test_stage_0_tools_have_risk_rules`. |
| Multi-rule aggregation is deterministic. | Highest risk wins; dimensions are unioned in enum order; matched rule IDs are sorted. |
| Verification results persist risk. | `VerificationResult.risk_assessment`, `VerificationResultRecord.risk_assessment`, migration `0004`, and store round-trip tests. |
| Claims expose latest classified risk for query parity. | `KnowledgeClaim.risk_level_classified`, `KnowledgeClaim.risk_assessment`, `GET /claims?risk_level_classified=...`, migration `0005`, and claim route/store tests. |
| Verification replay can pin time. | `CanonOrchestrator(..., verified_at=...)` accepts a fixed datetime or callable clock. |
| Confidence semantics are data, not code constants. | `contracts/confidence_model.v1.json` plus `test_confidence_model_contract.py`. |
| Alembic drift catches more than column names. | `test_alembic_metadata_alignment.py` compares column name, normalized type, and nullability. |

### Quality Bar

- `git status` classifies as `LOW`.
- `gh repo delete ...` classifies as `CRITICAL`.
- `docker system prune -a` classifies as `CRITICAL`.
- `vercel --prod` classifies as `HIGH`.
- `OpenAI API model call` classifies as `MEDIUM` under `openai-api`.
- Fallback remains `MEDIUM`, preserving conservative behavior for unknown actions.
- `safe_to_auto_execute` and `requires_confirmation` are derived from the final risk level, not independently supplied.
- `risk_assessment` is included in every orchestrator result and persisted as JSON.
- On verification save, the latest classified risk is copied onto the claim as nullable query-side fields. Before verification, `risk_level_classified` and `risk_assessment` are `null`.
- `GET /claims` supports filtering by `risk_level_classified`, so agents can query submitted risk and classified risk separately.
- Legacy pre-Stage-5 verification rows use `_LEGACY_RISK` with `risk_level=medium`, reason `legacy_row_pre_stage_5`, and empty dimensions/matches. Downstream consumers must treat empty matches as "legacy backfill", not as a freshly rule-classified result.
- Universal risk rules are limited to truly tool-agnostic hazards (`rm`, SQL drops, Terraform destroy, Kubernetes delete, force flags, secret/token references). Tool-specific commands live under `tools.*` to avoid duplicate matched reasons.
- `rm -r -f` and equivalent separated force/remove flag forms classify as `CRITICAL`.
- OpenAI API risk rules require an OpenAI/vendor token plus an action term; bare mentions of `api`, `model`, or `chat` do not fire `openai.model_call`.
- `vercel.preview` explicitly does not fire on `vercel --prod`.
- `understated_risk` cap is read from the confidence-model contract.
- Full backend validation currently reports `177 passed`; ruff is clean.

### Deferred

- Runtime sandbox evidence and L3 promotion (Stage 8).
- Prompt-injection-safe query wire format (Stage 9).
- Cross-agent/adversarial verification and L4 promotion (Stage 11).
- Signed maintainer review and L5 promotion (Stage 13).
- Indexed normalized duplicate keys and concurrency hardening (Stage 14).

## Stage 6: Canonical Publication and Spec Compilation

Verdict: pass.

Stage 6 is complete when accepted claims can be compiled into stable persisted
canonical `ToolSpec` and `WorkflowSpec` records without hand-written truth.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `backend/app/schemas/tool_spec.py` | Adds `PublicationIssue`, nullable unknown fields, and publication issues on `ToolSpec`. | complete |
| `backend/app/schemas/workflow_spec.py` | Adds provenance, publication issues, and verification-level alignment on `WorkflowSpec`. | complete |
| `backend/alembic/versions/0006_create_canonical_specs.py` | Adds persisted canonical tool/workflow spec tables with artifact and content hashes. | complete |
| `backend/app/services/canonical_compiler.py` | Deterministic `ToolSpecCompiler` and `WorkflowSpecCompiler`. | complete |
| `backend/app/api/routes_canonical.py` | Publish, retrieve, and list endpoints for canonical specs. | complete |
| `backend/tests/test_canonical_publication.py` | Compiler, store, API, determinism, and malformed-workflow tests. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| A `ToolSpec` can be rebuilt from accepted claims without manual editing. | `ToolSpecCompiler` compiles from `ClaimStore` accepted claims only. |
| Rejected or pending claims do not leak into canonical publication. | Compiler filters `verification_status=accepted`; tests insert pending claims and verify exclusion. |
| Provenance is preserved end-to-end. | Specs include source claim IDs, source evidence IDs, compiled timestamp, compiler ID, and verification level. |
| Verification level on the spec matches underlying evidence reality. | Compiler sets spec level to the minimum latest verification level across source claims. |
| Tool output is machine-readable and stable. | Specs are Pydantic models persisted as canonical JSON with deterministic `spec_hash` and `content_hash` values. |
| Compiler tests cover missing data, conflicting/incomplete data, and partial data. | Missing auth and malformed workflow subjects become `publication_issues`; no prose invention is used. |

### Quality Bar

- Canonical specs are persisted in `canonical_tool_specs` and `canonical_workflow_specs`.
- Publishing fails with `CANONICAL_PUBLICATION_FAILED` when there are no eligible accepted claims.
- Retrieval of unpublished specs returns `CANONICAL_SPEC_NOT_FOUND`.
- `GET /canonical/tools`, `GET /canonical/tools/{tool_id}`, and `POST /canonical/tools/{tool_id}/publish` are implemented.
- `GET /canonical/workflows`, `GET /canonical/workflows/{workflow_id}`, and `POST /canonical/workflows/{workflow_id}/publish` are implemented.
- The compiler uses Stage 0 tool metadata for locked tools and refuses unknown tool metadata instead of inventing interfaces.
- Tool commands are compiled from accepted `cli_command_exists` claims.
- Auth claims are not parsed from prose. Existing unstructured auth claims produce `auth.required=null` plus `unstructured_auth_claim`; absent auth claims produce `missing_auth_claim`.
- Unknown inputs, outputs, examples, failure modes, recovery steps, and command side effects are surfaced through `publication_issues`, not silent empty arrays.
- Capabilities are deterministic derived keys from accepted claims and are marked with `derived_capabilities`.
- Workflow steps require subject pattern `workflow_id::step_number::action`.
- Workflow `goal` is `null` until backed by a structured claim and emits `missing_workflow_goal`.
- Workflow prose statements are stored as step `description`, not executable `command`; command remains `null` with `missing_workflow_step_command`.
- Malformed workflow-step subjects are excluded and surfaced as `invalid_workflow_subject`.
- Same input plus same `compiled_at` produces identical spec JSON and hash.
- `content_hash` remains stable when only publication timestamp/compiler metadata changes.
- Re-publishing replaces the canonical record deterministically.
- Full backend validation currently reports `177 passed`; ruff is clean.

### Deferred

- Preview/on-demand compilation.
- MCP/query UX for canonical specs.
- Runtime promotion beyond existing verification levels.
- Rich structured ingestion for failure modes, recovery steps, examples, inputs, and outputs.

## Stage 7a: Safe CLI Ingestion Layer

Verdict: pass.

Stage 7a is complete when AgentAtlas can safely capture allowlisted CLI
help/version output, persist raw artifacts for audit, convert captures into
structured claims, and verify those claims without executing unsafe commands
or auto-publishing canonical specs. Stage 7b (Docs), 7c (API schema), and 7d
(MCP metadata) are tracked as separate sub-stages and are not part of this
pass.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/cli_ingestion_sources.v1.json` | Versioned allowlist for safe `git` and GitHub CLI capture commands. | complete |
| `backend/app/schemas/ingestion.py` | Typed Pydantic schemas for ingestion requests, responses, runs, raw artifacts, and the structured `CliIngestionSummary`. | complete |
| `backend/alembic/versions/0007_create_ingestion_tables.py` | Adds ingestion run and raw artifact tables. | complete |
| `backend/app/services/cli_ingestion.py` | Streamed safe runner with positive-shape argv allowlist, allowlist validation, artifact capture, statement derivation, claim creation, verification, and a bulk-ingest service method. | complete |
| `backend/app/api/routes_ingestion.py` | CLI ingestion endpoints including `POST /ingestion/cli`, the bulk `POST /ingestion/cli/tools/{tool_id}`, run retrieval, and raw artifact retrieval. | complete |
| `backend/tests/test_cli_ingestion.py` | Safety, prompt-injection boundary, adversarial argv guard regression, statement derivation, bulk ingest, real-binary smoke, API, artifact, and verification tests. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| At least `git` and `gh` can be ingested safely. | Allowlist covers `git --version`, `git status -h`, `git log -h`, `gh --version`, `gh repo delete --help`, and `gh repo view --help`. |
| Ingestion produces structured claims, not loose summaries. | The service creates `tool_exists` or `cli_command_exists` claims from allowlist metadata; `claim.statement` is the first non-empty line of captured output, not invented boilerplate. |
| Raw evidence can be inspected after the fact. | `raw_ingestion_artifacts` stores exact raw content, source URI, hash, and capture timestamp; `GET /ingestion/artifacts/{artifact_id}` retrieves it. |
| Unsafe commands are not executed during ingestion. | Unknown commands are rejected before run creation; runner uses argv lists, `shell=False`, `/` cwd, empty env, hard timeouts, and a streaming byte-cap that kills the subprocess on overflow before reading 8 KB beyond the cap. |
| Docs/CLI output are treated as data, not instructions. | Captured output is stored and excerpted as evidence only; no claims are derived from arbitrary output prose. |
| Prompt-injection-like content is ignored as executable instruction. | Tests store malicious-looking help text and prove only the allowlisted capture command ran. |

### Quality Bar

- `POST /ingestion/cli` creates an ingestion run, raw artifact, structured claim, and verification result.
- `POST /ingestion/cli/tools/{tool_id}` runs every allowlisted command for a tool in one call.
- `GET /ingestion/runs`, `GET /ingestion/runs/{run_id}`, and `GET /ingestion/artifacts/{artifact_id}` are implemented.
- `_assert_safe_capture_argv` is a positive-shape allowlist: argv must end with a read-only marker (`--help`, `-h`, `--version`, `help`); intermediate elements must be non-flag subcommands; the binary itself must not be one of the destructive binaries (`rm`, `dd`, `chmod`, etc.) whose first positional argument can be misread as a target.
- The runner is `subprocess.Popen` with streamed reads, killing the subprocess and surfacing `CliIngestionError` on timeout or max-output overflow before the buffer can blow past the cap.
- The CLI ingestion contract declares an explicit `binary` per tool; the validator enforces `argv[0] == binary` so contract edits cannot silently swap a binary.
- `IngestionRun.summary` is a typed `CliIngestionSummary` model, not an opaque dict.
- Evidence hashes are computed from the exact raw captured artifact (sha256).
- Ingestion does not call canonical publish endpoints.
- Failed captures persist failed runs and create no claims.
- `ClaimStore.create` enforces `evidence_policy_violations` defensively so the policy applies to every path into the store, not just `POST /claims`.
- The real CLI runner is exercised against the running Python interpreter in `test_safe_runner_actually_invokes_real_binary` (not just mocked).
- Full backend validation currently reports `198 passed`; ruff is clean.

### Deferred (Stages 7b / 7c / 7d)

- API schema ingestion agent for OpenAPI, JSON Schema, and GraphQL sources (7c).
- MCP metadata ingestion agent where applicable (7d).
- LLM/prose extraction from captured artifacts.
- Canonical auto-publication after ingestion.
- Runtime sandbox verification and L3 promotion (Stage 8).

## Stage 7b: Safe Docs Ingestion Layer

Verdict: pass.

Stage 7b is complete when AgentAtlas can safely fetch documentation URLs for
the locked Stage 0 tools, sanitize the response into prose-only excerpts,
persist raw bodies for audit, and verify the resulting claims through the
orchestrator — without ever following a redirect to a non-allowlisted host
or rendering executable HTML/JS as content.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/docs_ingestion_sources.v1.json` | Versioned per-tool allowlist of documentation URLs, plus default timeout, max-bytes, cache TTL, max-redirects, allowed-content-types. | complete |
| `backend/app/schemas/ingestion.py` | Adds `DocsIngestionRequest`, `DocsIngestionResponse`, `DocsIngestionSummary`, `DocsFetchCacheEntry`. | complete |
| `backend/alembic/versions/0009_create_docs_fetch_cache.py` | Adds the `docs_fetch_cache` table and extends the `raw_ingestion_artifacts.artifact_type` check constraint to include `docs_content`. | complete |
| `backend/app/db/models.py` | Adds `DocsFetchCacheRecord`. | complete |
| `backend/app/services/docs_ingestion.py` | SSRF-safe httpx fetcher, manual redirect re-validation, stdlib HTML sanitizer (`html.parser`), conditional revalidation via `If-None-Match` / `If-Modified-Since`, claim + artifact creation, bulk-ingest service method. | complete |
| `backend/app/services/claim_store.py` | `save_docs_fetch_cache` / `get_docs_fetch_cache` round-trip. | complete |
| `backend/app/api/routes_ingestion.py` | `POST /ingestion/docs` and `POST /ingestion/docs/tools/{tool_id}`; both return structured `DOCS_FETCH_FAILED` on rejection. | complete |
| `backend/tests/test_docs_ingestion.py` | 37 adversarial cases covering contract gate, SSRF, redirects, sanitization, content-type, max-bytes, empty/scripts-only body, transport errors, cache hit/miss, bulk, and the API. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| Trusted docs for Stage 0 tools can be ingested without arbitrary URL fetching. | The `docs_fetch_spec()` lookup refuses any URL not in `docs_ingestion_sources.v1.json`; the contract validator at load time also enforces that every URL's hostname is in the per-tool `official_hosts` from `tool_trust_sources.v1.json`. |
| Redirects to non-allowlisted hosts are rejected, not followed. | httpx auto-redirects are disabled (`follow_redirects=False`); the service follows redirects manually, re-running `_assert_url_is_safe` at every hop (scheme, host allowlist, IP class). Test: `test_redirect_to_disallowed_host_is_rejected`. |
| Excerpts never contain executable HTML/JS. | `_sanitize_html_to_text` uses `html.parser` and drops the content of `script`, `style`, `iframe`, `object`, `embed`, `template`, `noscript`, and `svg` entirely. Attribute values (including `on*` event handlers) are never emitted. Tests: `test_sanitizer_strips_script_content`, `test_sanitizer_strips_inline_event_handler_html`, `test_response_with_only_script_content_rejected`. |
| Ingestion produces structured claims with durable evidence. | Each fetch creates a `RawIngestionArtifact` (raw response body, sha256 hash, `agentatlas://` source URI, `docs_content` artifact type) plus a `KnowledgeClaim` whose `Evidence.source_uri` is the original public URL so `evidence_trust.py` resolves trust to `HIGH` for hosts in the per-tool allowlist. |
| Raw evidence can be inspected after the fact. | `GET /ingestion/artifacts/{artifact_id}` returns the byte-stable raw body that the ingester saw. |
| Docs are treated as data, not instructions. | The orchestrator runs `evidence_policy_violations` over every ingested claim's evidence (already enforced inside `ClaimStore.create`); sanitization strips executable content before the excerpt ever reaches the database. |

### Quality Bar

- Only `https://` is accepted; HTTP, FTP, file, and unknown schemes are rejected at the SSRF guard.
- Hostname is resolved via `socket.getaddrinfo`, and every returned address must be public (not private, loopback, link-local, multicast, reserved, or unspecified). IP literals (`127.0.0.1`, `169.254.169.254`) are rejected at the literal-parse step before resolution.
- Response body is read once with a `max_bytes` cap (default 1 MiB); oversized bodies are rejected.
- `content-type` header must be in `allowed_content_types` (default: `text/html`, `text/plain`, `text/markdown`, `application/xhtml+xml`); any other type, including `application/javascript`, is rejected.
- ETag and Last-Modified are persisted in `docs_fetch_cache`; on revalidation the service sends `If-None-Match` / `If-Modified-Since`, and a 304 reuses the cached artifact without creating a new raw row.
- Cache reuse creates a *new* claim and verification result (so audit chronology reflects each ingest call) but does not duplicate the raw artifact.
- The HTTP client is injected via `DocsHttpClient` Protocol so tests use a `FakeDocsClient` rather than hitting the network.
- DNS lookup is also injectable via `socket.getaddrinfo` monkeypatching in tests so SSRF tests don't depend on real DNS.
- `POST /ingestion/docs/tools/{tool_id}` ingests every URL allowlisted for a tool in one call.
- Bulk and single endpoints both return `DOCS_FETCH_FAILED` (422) on rejection with `tool_id` / `url` in `details`.
- Full backend validation currently reports `283 passed`; ruff is clean.

### Deferred

- JSON Schema ingestion (Stage 7c.2).
- GraphQL schema ingestion (Stage 7c.3).
- MCP metadata ingestion (Stage 7d).
- LLM/prose extraction from captured artifacts.
- Canonical auto-publication after ingestion.
- Runtime sandbox verification and L3 promotion (Stage 8).

## Stage 7c.1: Safe OpenAPI Schema Ingestion

Verdict: pass.

Stage 7c.1 is complete when AgentAtlas can safely fetch an allowlisted
OpenAPI 3.x specification for a gated tool, validate it against the OpenAPI
meta-schema, persist the raw body for audit, and derive structured per-operation
claims whose evidence carries byte-level provenance — without ever following a
redirect to a non-allowlisted host, accepting a non-JSON body, or trusting a
spec the meta-schema rejects.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/openapi_ingestion_sources.v1.json` | Versioned per-tool allowlist of OpenAPI source URLs, plus default timeout, max-bytes, cache TTL, max-redirects, max-endpoints-per-spec, allowed-content-types. | complete |
| `backend/app/schemas/enums.py` | Adds `IngestionArtifactType.OPENAPI_SPEC`. | complete |
| `backend/app/api/errors.py` | Adds `OPENAPI_INGESTION_FAILED`. | complete |
| `backend/app/schemas/ingestion.py` | Adds `OpenApiIngestionRequest`, `OpenApiIngestionResponse`, `OpenApiIngestionSummary`. | complete |
| `backend/alembic/versions/0010_extend_artifact_type_for_openapi.py` | Extends the `raw_ingestion_artifacts.artifact_type` check constraint to include `openapi_spec`. | complete |
| `backend/app/services/http_safety.py` | Shared SSRF guard (https-only, host allowlist + subdomain rules, IP literal parse, resolved-address public-class check) reused by 7b and 7c.1. | complete |
| `backend/app/services/openapi_ingestion.py` | SSRF-safe httpx fetcher with manual redirect re-validation, content-type allowlist, max-bytes cap, hard timeout, `openapi-spec-validator` meta-schema check, per-(method, path) claim generator with auxiliary auth / side-effect / destructive / deprecated claims, JSON Pointer (RFC 6901) provenance in `Evidence.source_uri`, cache reuse via `docs_fetch_cache`. | complete |
| `backend/app/api/routes_ingestion.py` | `POST /ingestion/openapi` and `POST /ingestion/openapi/tools/{tool_id}`; both return structured `OPENAPI_INGESTION_FAILED` (422) on rejection. | complete |
| `backend/tests/test_openapi_ingestion.py` | 41 adversarial cases covering contract gate, SSRF, redirects, content-type, max-bytes, empty / malformed JSON / non-OpenAPI-3 / schema-invalid bodies, no-operation specs, per-method risk + auxiliary claim emission, JSON Pointer escaping, cache hit reuse, bulk ingest, and the API surface. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| OpenAPI specs are only fetched from allowlisted sources for allowlisted tools. | `openapi_fetch_spec()` refuses any URL not declared in `openapi_ingestion_sources.v1.json`; the contract loader also asserts each URL's hostname is in the per-tool `official_hosts` from `tool_trust_sources.v1.json`. |
| Redirects to non-allowlisted hosts are rejected, not followed. | httpx auto-redirects are disabled (`follow_redirects=False`); the service follows redirects manually, re-running `assert_url_is_safe` at every hop. Test: `test_redirect_to_disallowed_host_is_rejected`. |
| Malformed or non-OpenAPI bodies do not produce claims. | Body must parse as a JSON object; `openapi` must start with `3.`; `openapi-spec-validator` must accept the document. Tests: `test_malformed_json_rejected`, `test_non_dict_root_rejected`, `test_non_openapi_3_rejected`, `test_invalid_openapi_schema_rejected_by_validator`. |
| Ingestion produces structured claims with byte-level provenance. | Each (method, path) operation yields one `api_endpoint_exists` claim plus the auxiliary set warranted by the operation. `Evidence.source_uri = <final_url>#<json_pointer>` so an auditor can walk back to the exact spec node. Test: `test_json_pointer_escapes_special_chars`. |
| Destructive and mutating operations are flagged with elevated risk. | DELETE → `RiskLevel.CRITICAL` + `destructive_action` claim. POST/PUT/PATCH → `RiskLevel.HIGH` + `side_effect` claim. Test: `test_delete_operation_emits_destructive_claim`, `test_post_emits_side_effect_claim`. |
| Authentication requirements are surfaced as claims. | Operations whose effective security block (per-operation override or spec-global default) is non-empty emit `auth_requirement`. Tests: `test_operation_with_spec_global_security_emits_auth_claim`, `test_operation_local_security_overrides_spec_security`. |
| Deprecated operations are flagged. | `operation.deprecated == True` emits `feature_deprecated`. Test: `test_deprecated_operation_emits_deprecation_claim`. |
| Raw evidence is recoverable. | `GET /ingestion/artifacts/{artifact_id}` returns the byte-stable raw body the ingester saw; `artifact_type` is `openapi_spec`. |

### Quality Bar

- Only `https://` is accepted; HTTP, FTP, file, and unknown schemes are rejected at the shared SSRF guard.
- Hostname is resolved via `socket.getaddrinfo`, and every returned address must be public. IP literals (`127.0.0.1`, `169.254.169.254`) are rejected at the literal-parse step before resolution.
- Response body is read once with a `max_bytes` cap (default 8 MiB; OpenAPI specs are larger than docs); oversized bodies are rejected.
- `content-type` must be in `allowed_content_types` (`application/json`, `application/vnd.oai.openapi+json`, `text/json`); HTML / JS / YAML are rejected.
- ETag and Last-Modified are persisted in the shared `docs_fetch_cache`; on revalidation the service sends `If-None-Match` / `If-Modified-Since`, and a 304 reuses the cached artifact without re-fetching.
- Cache reuse creates a *new* claim and verification result per ingest call but does not duplicate the raw artifact.
- Per-(method, path) emission is capped by `max_endpoints_per_spec` (default 500) to bound work on adversarially large specs.
- The HTTP client is injected via the `OpenApiHttpClient` Protocol so tests use a `FakeOpenApiClient` rather than hitting the network.
- DNS is monkeypatchable in tests so SSRF cases don't depend on real DNS.
- `POST /ingestion/openapi/tools/{tool_id}` ingests every allowlisted source for a tool in one call.
- Bulk and single endpoints both return `OPENAPI_INGESTION_FAILED` (422) on rejection with `tool_id` / `url` in `details`.
- Full backend validation currently reports `460 passed`; ruff is clean.

### Audit findings (post-ship)

**Bug:** A `304 Not Modified` cache-hit silently failed the entire ingest run with the error "OpenAPI spec contains no operations to ingest." Root cause: on a 304 the service set `fetch.spec = {}` and then ran `_iter_operations({}, ...)`, which returned no operations and triggered the validation guard. The original test asserted only `cache_hit is True` and `created_claim_ids != first.created_claim_ids` — both true even when `created_claim_ids == []` — so the bug shipped green.

**Fix:** On 304 the service now re-parses the cached `artifact.raw_content` into a populated `OpenApiFetchResult` before the operations loop runs. The 304 test was tightened to assert `status == COMPLETED`, `errors == []`, and `len(claims) == len(first_claims)` so this can't regress.

### Deferred

- GraphQL schema ingestion (Stage 7c.3).
- MCP metadata ingestion (Stage 7d).
- LLM/prose extraction from OpenAPI descriptions beyond first-line summaries.
- Canonical auto-publication after ingestion.
- Runtime sandbox verification and L3 promotion (Stage 8).

## Stage 7c.2: Safe JSON Schema Ingestion

Verdict: pass.

Stage 7c.2 is complete when AgentAtlas can safely fetch an allowlisted JSON
Schema document for a gated tool, validate it against the dialect declared by
its own `$schema` keyword, persist the raw body for audit, and derive a
structured claim per top-level configuration field with byte-level provenance
back to the originating schema node.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/json_schema_ingestion_sources.v1.json` | Versioned per-tool allowlist of JSON Schema source URLs, plus default timeout, max-bytes, cache TTL, max-redirects, max-fields-per-schema, allowed-content-types, and per-source `subject_prefix` for readable claim subjects. | complete |
| `contracts/tool_trust_sources.v1.json` | New top-level `schema_aggregator_hosts.json_schema` allowing `json.schemastore.org` as a trusted aggregator without widening per-tool `official_hosts`. | complete |
| `backend/app/schemas/enums.py` | Adds `ClaimType.CONFIG_FIELD_EXISTS` and `IngestionArtifactType.JSON_SCHEMA_DOC`. | complete |
| `backend/app/api/errors.py` | Adds `JSON_SCHEMA_INGESTION_FAILED`. | complete |
| `backend/app/schemas/ingestion.py` | Adds `JsonSchemaIngestionRequest`, `JsonSchemaIngestionResponse`, `JsonSchemaIngestionSummary`. | complete |
| `backend/alembic/versions/0011_extend_checks_for_json_schema.py` | Extends the `knowledge_claims.claim_type` CHECK with `config_field_exists` and the `raw_ingestion_artifacts.artifact_type` CHECK with `json_schema_doc`; downgrade reverses both. | complete |
| `backend/app/services/evidence_trust.py` | Extends the schema-evidence trust resolver to union per-tool `official_hosts` with `schema_aggregator_hosts.json_schema`, so schemastore-hosted schemas resolve to HIGH trust without leaking trust into the docs / openapi lanes. | complete |
| `backend/app/services/json_schema_ingestion.py` | SSRF-safe httpx fetcher with manual redirect re-validation, content-type allowlist, max-bytes cap, hard timeout, `jsonschema`-driven dialect-specific meta-schema check (`validator_for($schema)`), per-top-level-property claim generator with auxiliary `feature_deprecated` claims, JSON Pointer (RFC 6901) provenance in `Evidence.source_uri`, cache reuse via `docs_fetch_cache`. | complete |
| `backend/app/api/routes_ingestion.py` | `POST /ingestion/json_schema` and `POST /ingestion/json_schema/tools/{tool_id}`; both return structured `JSON_SCHEMA_INGESTION_FAILED` (422) on rejection. | complete |
| `backend/tests/test_json_schema_ingestion.py` | 37 adversarial cases covering contract gate, SSRF, redirects, content-type, max-bytes, empty / malformed JSON / non-dict-root / meta-schema-invalid / no-properties bodies, required-vs-optional reflection, deprecation, JSON Pointer escaping, aggregator-host trust resolution, cache hit reuse, bulk ingest, and the API surface. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| JSON Schemas are only fetched from allowlisted sources for allowlisted tools. | `json_schema_fetch_spec()` refuses any URL not declared in `json_schema_ingestion_sources.v1.json`; the contract loader also asserts each URL's hostname is in the per-tool `official_hosts` *or* the top-level `schema_aggregator_hosts.json_schema`. |
| schemastore.org is trusted as an aggregator, but only for JSON Schema evidence. | `schema_aggregator_hosts("json_schema")` is unioned into the allow-set only inside this service and inside the JSON_SCHEMA / GRAPHQL_SCHEMA branch of `_server_trust_cap`. Other lanes (docs, openapi) are unaffected. Test: `test_evidence_from_aggregator_host_resolves_to_high_trust`. |
| Redirects to non-allowlisted hosts are rejected, not followed. | httpx auto-redirects are disabled; the service follows redirects manually, re-running `assert_url_is_safe` at every hop. Test: `test_redirect_to_disallowed_host_is_rejected`. |
| Malformed schemas don't produce claims. | Body must parse as a JSON object; the dialect picked from `$schema` (or Draft 2020-12 default) must accept the document via `check_schema()`. Tests: `test_malformed_json_rejected`, `test_non_dict_root_rejected`, `test_invalid_meta_schema_rejected_by_validator`. |
| Ingestion produces structured claims with byte-level provenance. | One `config_field_exists` per top-level property in `properties`, plus `feature_deprecated` where `deprecated: true`. `Evidence.source_uri = <final_url>#<json_pointer>` so an auditor can walk back to the exact schema node. Test: `test_json_pointer_escapes_special_chars`. |
| Required vs optional fields are surfaced in claim statements. | The claim statement explicitly says "required field" / "optional field" based on the schema's `required` array. Test: `test_required_vs_optional_reflected_in_statement`. |
| Raw evidence is recoverable. | `GET /ingestion/artifacts/{artifact_id}` returns the byte-stable raw body; `artifact_type` is `json_schema_doc`. |

### Quality Bar

- Only `https://` is accepted; HTTP, FTP, file, and unknown schemes are rejected at the shared SSRF guard.
- Hostname is resolved via `socket.getaddrinfo`, and every returned address must be public.
- Response body is read once with a `max_bytes` cap (default 4 MiB); oversized bodies are rejected.
- `content-type` must be in `allowed_content_types` (`application/json`, `application/schema+json`, `application/schema-instance+json`, `text/json`).
- Dialect detection: `validator_for(document)` picks Draft 4 / 6 / 7 / 2019-09 / 2020-12 based on `$schema`; docs without `$schema` default to Draft 2020-12.
- ETag and Last-Modified are persisted in the shared `docs_fetch_cache`; a 304 reuses the cached artifact without re-fetching.
- Cache reuse creates a *new* claim and verification result per ingest call but does not duplicate the raw artifact.
- Top-level field emission is capped by `max_fields_per_schema` (default 500).
- Risk for `config_field_exists` is fixed at LOW (declaring a config field is not by itself a risky action); `feature_deprecated` is MEDIUM.
- `POST /ingestion/json_schema/tools/{tool_id}` ingests every allowlisted source for a tool in one call.
- Bulk and single endpoints both return `JSON_SCHEMA_INGESTION_FAILED` (422) on rejection with `tool_id` / `url` in `details`.
- Full backend validation currently reports `460 passed`; ruff is clean; alembic upgrade → downgrade → upgrade cycle clean through migration 0014.

### Audit findings (post-ship)

**Bug:** Identical pattern to the OpenAPI 304 bug. A `304 Not Modified` cache-hit silently failed with "JSON Schema declares no top-level properties to ingest." Root cause: `fetch.document = {}` on 304, then `_iter_top_level_fields({}, ...)` returned empty, then the validation guard rejected. Same test gap as 7c.1 let it ship green.

**Fix:** Mirror of the 7c.1 fix. On 304 the service re-parses the cached `artifact.raw_content` into a populated `JsonSchemaFetchResult` before the fields loop runs. The 304 test was tightened to assert `status == COMPLETED`, `errors == []`, and `len(claims) == len(first_claims)`.

### Deferred

- MCP metadata ingestion (Stage 7d).
- Deeper schema traversal (nested `properties`, `definitions` / `$defs`, `oneOf` / `anyOf`).
- Cross-reference resolution (`$ref` following beyond the local document).
- Canonical auto-publication after ingestion.
- Runtime sandbox verification and L3 promotion (Stage 8).

## Stage 7c.3: Safe GraphQL SDL Ingestion

Verdict: pass.

Stage 7c.3 is complete when AgentAtlas can safely fetch an allowlisted
GraphQL Schema Definition Language (SDL) document from a gated tool's own
official host, parse and type-system-validate it via `graphql-core`, persist
the raw body for audit, and derive a structured claim per root operation
field — without ever following a redirect to a non-allowlisted host,
accepting a non-SDL body, or trusting an SDL the parser rejects.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/graphql_ingestion_sources.v1.json` | Versioned per-tool allowlist of SDL source URLs, plus default timeout, max-bytes, cache TTL, max-redirects, max-root-fields-per-schema, the `destructive_field_name_prefixes` allowlist, allowed-content-types, and per-source `subject_prefix`. | complete |
| `backend/app/schemas/enums.py` | Adds `IngestionArtifactType.GRAPHQL_SDL`. | complete |
| `backend/app/api/errors.py` | Adds `GRAPHQL_INGESTION_FAILED`. | complete |
| `backend/app/schemas/ingestion.py` | Adds `GraphqlIngestionRequest`, `GraphqlIngestionResponse`, `GraphqlIngestionSummary`. | complete |
| `backend/alembic/versions/0012_extend_artifact_type_for_graphql.py` | Extends the `raw_ingestion_artifacts.artifact_type` CHECK to include `graphql_sdl`; downgrade reverses. | complete |
| `backend/app/services/graphql_ingestion.py` | SSRF-safe httpx fetcher with manual redirect re-validation, content-type allowlist, max-bytes cap, hard timeout, full SDL parse + type-system check via `graphql.build_schema`, per-root-field claim generator (Query / Mutation / Subscription) with auxiliary `side_effect` / `destructive_action` / `feature_deprecated` claims, `#<OperationType>.<fieldName>` provenance in `Evidence.source_uri`, cache reuse via `docs_fetch_cache`. | complete |
| `backend/app/api/routes_ingestion.py` | `POST /ingestion/graphql` and `POST /ingestion/graphql/tools/{tool_id}`; both return structured `GRAPHQL_INGESTION_FAILED` (422) on rejection. | complete |
| `backend/tests/test_graphql_ingestion.py` | 40 adversarial cases covering contract gate, SSRF, redirects, content-type, max-bytes, empty / malformed-SDL / no-root-operations bodies, per-operation risk mapping (Query LOW, Mutation HIGH, destructive Mutation CRITICAL, Subscription LOW), side-effect / destructive / deprecation emission, provenance fragment, official-host trust resolution, cache hit reuse, bulk ingest, and the API surface. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| SDL is fetched only from allowlisted sources for allowlisted tools. | `graphql_fetch_spec()` refuses any URL not declared in `graphql_ingestion_sources.v1.json`; the contract loader also asserts each URL's hostname is in the per-tool `official_hosts` from `tool_trust_sources.v1.json` (no aggregator block — SDL is fetched only from the vendor's own host). |
| Redirects to non-allowlisted hosts are rejected, not followed. | httpx auto-redirects are disabled; the service follows redirects manually, re-running `assert_url_is_safe` at every hop. Test: `test_redirect_to_disallowed_host_is_rejected`. |
| Malformed SDL does not produce claims. | `build_schema(sdl)` raises `GraphQLError` on parse or type-system invariant violations; the error is captured into the run's `errors` list with no claims emitted. Test: `test_malformed_sdl_rejected`. |
| Ingestion produces structured claims with byte-level provenance. | One `api_endpoint_exists` per root field, plus auxiliaries as warranted. `Evidence.source_uri = <final_url>#<OperationType>.<fieldName>` so an auditor can trace any assertion back to the SDL location. Test: `test_provenance_fragment_uses_operation_dot_field`. |
| Mutations are flagged with elevated risk and a side-effect claim. | Mutation root fields → `RiskLevel.HIGH` on the primary `api_endpoint_exists` claim plus an auxiliary `side_effect` claim. Test: `test_non_destructive_mutation_is_high_risk_not_critical`, `test_mutation_emits_side_effect_claim`. |
| Destructive mutations are flagged CRITICAL and produce a destructive claim. | Mutation field name matched against `destructive_field_name_prefixes` (`delete`, `remove`, `destroy`, `drop`, `purge`, `revoke`, `wipe`) → primary risk upgraded to `CRITICAL` plus an auxiliary `destructive_action` claim. Test: `test_destructive_mutation_is_critical_and_emits_destructive_claim`. |
| Deprecated fields are surfaced as claims. | `@deprecated(reason: ...)` directive becomes `field.deprecation_reason`; service emits `feature_deprecated` with the reason in the statement. Test: `test_deprecated_field_emits_deprecation_claim`. |
| Raw evidence is recoverable. | `GET /ingestion/artifacts/{artifact_id}` returns the byte-stable raw SDL; `artifact_type` is `graphql_sdl`. |

### Quality Bar

- Only `https://` is accepted; HTTP, FTP, file, and unknown schemes are rejected at the shared SSRF guard.
- Hostname is resolved via `socket.getaddrinfo`, and every returned address must be public.
- Response body is read once with a `max_bytes` cap (default 8 MiB).
- `content-type` must be in `allowed_content_types` (`application/graphql`, `text/graphql`, `text/plain`, `application/octet-stream`).
- SDL is parsed via `graphql.build_schema()` so both syntax and type-system invariants are enforced; broken SDL never produces claims.
- ETag and Last-Modified are persisted in the shared `docs_fetch_cache`; on revalidation a 304 reuses the cached artifact and re-parses the cached raw SDL body.
- Cache reuse creates a *new* claim and verification result per ingest call but does not duplicate the raw artifact.
- Root-field emission is capped by `max_root_fields_per_schema` (default 500); fields beyond the cap are reported in `skipped_fields`.
- Risk mapping: Query / Subscription → LOW; Mutation → HIGH; destructive-prefix Mutation → CRITICAL. `feature_deprecated` is MEDIUM.
- `POST /ingestion/graphql/tools/{tool_id}` ingests every allowlisted source for a tool in one call.
- Bulk and single endpoints both return `GRAPHQL_INGESTION_FAILED` (422) on rejection with `tool_id` / `url` in `details`.
- Full backend validation currently reports `401 passed`; ruff is clean; alembic upgrade → downgrade → upgrade cycle clean through migration 0012.

### Deferred

- Introspection-query ingestion (the alternative source path; deliberately
  out of scope to avoid building a credentials lane).
- Deeper SDL coverage: object/interface/union fields beyond the three root
  operation types, input-type field annotations, directive definitions.
- `extend type` resolution across multi-file SDL splits.
- Canonical auto-publication after ingestion.
- Runtime sandbox verification and L3 promotion (Stage 8).

## Stage 7d: Safe MCP Server Metadata Ingestion

Verdict: pass.

Stage 7d is complete when AgentAtlas can safely spawn an allowlisted Model
Context Protocol (MCP) server as a local subprocess, drive the standard
JSON-RPC `initialize` + `tools/list` handshake, persist the full JSON-RPC
payload as a durable audit artifact, and derive a structured claim per
advertised MCP tool with risk hints — without ever invoking a command that
isn't in the positive-shape allowlist, leaking a runaway process, or letting
a server's output bypass the prompt-injection boundary.

### Stage 0 contract change

Stage 7d deliberately expands the Stage 0 scope. The original
`initial_tools` list (`git`, `github-cli`, `docker`, `vercel-cli`,
`openai-api`) remains locked exactly as before. A new parallel
`mcp_server_tools` array now lists the five MCP-proxied tools we cover —
`mcp-filesystem`, `mcp-fetch`, `mcp-git`, `mcp-slack`, `mcp-postgres`. The
`ClaimStore.create` tool_id gate unions both lists, preserving the
auditable distinction between native-ingestion scope and MCP-proxy scope
while letting claims for either kind clear the gate. The
`test_stage_0_contract_locks_initial_tool_scope` test still pins the
original five exactly; a new
`test_stage_0_contract_locks_mcp_server_tool_scope` test pins the MCP
expansion to its current five so any further widening is deliberate.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/agentatlas_stage_0.v1.json` | Adds `mcp_server_tools[]` array (5 entries) as a deliberate Stage 7d scope expansion. | complete |
| `contracts/tool_trust_sources.v1.json` | Adds 5 MCP server tools with `mcp_publisher` field marking each as `anthropic` or `third-party`. | complete |
| `contracts/mcp_ingestion_sources.v1.json` | Per-server spawn metadata: `command`, positive-shape `argv` template (placeholders limited to `{sandbox_dir}` / `{database_url}`), publisher, package URI, plus contract-wide `protocol_version`, `default_timeout_seconds`, `default_max_bytes`, `max_tools_per_server`, `destructive_tool_name_prefixes`, `allowed_commands` (only `npx` / `uvx`), `allowed_template_placeholders`. | complete |
| `backend/app/services/claim_store.py` | `_stage_0_tool_ids()` now unions `initial_tools` ∪ `mcp_server_tools`, comment-documents the architectural distinction. | complete |
| `backend/app/schemas/enums.py` | Adds `IngestionArtifactType.MCP_TOOL_LIST`. | complete |
| `backend/app/api/errors.py` | Adds `MCP_INGESTION_FAILED`. | complete |
| `backend/app/schemas/ingestion.py` | Adds `McpIngestionRequest`, `McpIngestionResponse`, `McpIngestionSummary`. | complete |
| `backend/alembic/versions/0013_extend_artifact_type_for_mcp.py` | Extends the `raw_ingestion_artifacts.artifact_type` CHECK to include `mcp_tool_list`; downgrade reverses. | complete |
| `backend/app/services/mcp_ingestion.py` | `McpServerRunner` protocol; `SafeMcpServerRunner` production runner (subprocess + stdio JSON-RPC with hard time/byte caps and reliable termination); `McpIngestionService` orchestrator; spec resolver enforcing the argv allowlist + placeholder substitution; per-tool claim generator with auxiliary side-effect / destructive / deprecation emission and atlas-capture provenance. | complete |
| `backend/app/api/routes_ingestion.py` | `POST /ingestion/mcp` and `POST /ingestion/mcp/publishers/{publisher}`; both return structured `MCP_INGESTION_FAILED` (422) on rejection. | complete |
| `backend/tests/test_mcp_ingestion.py` | 30 adversarial cases covering contract gate, argv allowlist, placeholder substitution, missing sandbox / database URL, malformed initialize / tools/list, non-array tools, missing-name / non-dict tool entries, runner exceptions, per-tool cap, all four risk branches (read-only LOW, mutating HIGH, destructiveHint CRITICAL, destructive-name-prefix CRITICAL), deprecation, bulk-by-publisher, structured 422 from the API, and a sanity check that the production `SafeMcpServerRunner` rejects spawn commands outside `allowed_commands`. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| Only contract-listed servers can be spawned. | `resolve_mcp_server_spec()` refuses any `server_id` not in `mcp_ingestion_sources.v1.json`. Tests: `test_contract_rejects_unknown_server`, `test_api_rejects_unknown_server_with_structured_error`. |
| Only `npx` / `uvx` may be invoked. | `_validate_mcp_sources` rejects any server whose command isn't in `allowed_commands`; `SafeMcpServerRunner.run` re-checks at runtime. Test: `test_safe_runner_rejects_command_not_in_allowed_commands`. |
| Argv templates use only sanctioned placeholders. | The spec resolver substitutes only `{sandbox_dir}` / `{database_url}` and raises for any other `{...}` token. Tests: `test_sandbox_dir_placeholder_substitution`, `test_postgres_requires_database_url`, `test_postgres_substitutes_database_url`. |
| Misbehaving servers cannot exhaust resources. | `SafeMcpServerRunner` enforces a wall-clock deadline (`spec.timeout_seconds`) on every `select`, a cumulative byte cap (`spec.max_bytes`) on stdout, and a guaranteed `_terminate` cleanup that closes pipes, terminates, waits, and kills as needed. |
| Server output is treated as untrusted data. | Tool names / descriptions / annotations flow into claim statements via `' '.join(text.split())` then character cap; never executed, never substituted into shell commands. The full JSON-RPC log is stored as a `mcp_tool_list` artifact for byte-stable audit. |
| Risk is set correctly per MCP tool annotation. | `readOnlyHint: true` → LOW; missing / false readOnlyHint → HIGH + `side_effect` claim; `destructiveHint: true` or destructive-prefix name → CRITICAL + `destructive_action` claim. Tests: `test_read_only_tool_is_low_risk_no_side_effect`, `test_non_readonly_tool_emits_side_effect_high`, `test_destructive_hint_emits_critical_and_destructive_claim`, `test_destructive_name_prefix_emits_critical_even_without_hint`. |
| Malformed JSON-RPC replies do not produce claims. | The service guards both `initialize_result` and `tools_list_result` being dicts; `tools_list_result.tools` must be a JSON array; tools missing a `name` or that aren't JSON objects are skipped (not failed) and recorded in `skipped_tools`. Tests: `test_non_array_tools_field_rejected`, `test_tool_missing_name_is_skipped_not_failed`, `test_non_dict_tool_entry_is_skipped`. |
| Raw evidence is recoverable. | `GET /ingestion/artifacts/{artifact_id}` returns the byte-stable `mcp_tool_list` artifact containing the spawn command, argv, both JSON-RPC results, and the raw stdout log. |

### Quality Bar

- The production runner closes stdin to signal shutdown, calls `terminate()`, waits up to 2 s, then `kill()` if still alive — no leaked processes even on hung servers.
- Subprocess env is scrubbed: only `PATH` and `HOME` are inherited; `NO_UPDATE_NOTIFIER=1` and `NPM_CONFIG_UPDATE_NOTIFIER=false` are forced so npm noise doesn't pollute the JSON-RPC stream.
- `select`-based read loop respects the wall-clock deadline at every iteration.
- Cumulative byte cap on stdout aborts before exhausting memory.
- JSON-RPC reply demultiplexing tolerates server-sent notifications and mismatched-id replies by skipping rather than failing.
- Per-server cap on emitted tools (`max_tools_per_server`, default 200) — overflow goes into `skipped_tools`.
- Test suite never invokes the real `SafeMcpServerRunner`; CI stays hermetic without `npx` / `uvx` installed.
- Bulk endpoint groups by `publisher` (not by `tool_id`) so a single call can ingest all `anthropic` reference servers in one go.
- Full backend validation currently reports `460 passed`; ruff is clean; alembic upgrade → downgrade → upgrade cycle clean through migration 0014.

### Audit findings (post-ship)

**Bug:** The production `SafeMcpServerRunner` spawned the MCP server with `stderr=subprocess.PIPE` but never drained the stderr pipe. Real servers running under `npx` / `uvx` emit progress messages, npm update notices, and warnings on stderr; if the cumulative stderr output exceeds the OS pipe buffer (~64 KiB on Linux/macOS), the server blocks on its next stderr write and can no longer respond on stdout. The runner times out waiting for a JSON-RPC reply, kills the subprocess, and reports "MCP server timed out" — a false-positive diagnostic that hides the real pipe deadlock. Fakes-only test coverage missed it because no fake ever wrote enough stderr.

**Fix:** `stderr=subprocess.DEVNULL`. The runner does not use server stderr in any code path (it parses only JSON-RPC frames from stdout), so dropping it removes the deadlock without losing information. `_terminate()` was updated accordingly to only close the stdin/stdout pipes it now owns. The fix is documented in-source with a comment explaining the deadlock so a future "let's capture stderr for debugging" PR doesn't silently regress it.

### Deferred

- Live remote MCP servers over SSE / streamable HTTP (we ship only the stdio spawn path).
- Introspection of MCP `resources/list` and `prompts/list` (this stage covers `tools/list` only — the highest-leverage surface for risk classification).
- Auto-publication of accepted MCP claims into canonical `ToolSpec` documents.
- MCP server discovery from an external registry (we maintain the per-server allowlist by hand for now).

## Stage 8: Runtime Verification (L2 → L3)

Verdict: pass.

Stage 8 is complete when AgentAtlas can take a claim that has already
reached `L2_source_verified` and promote it to `L3_runtime_verified` by
running a safe, deterministic check against the asserted behaviour — with
the captured stdout / stderr / exit code persisted as a durable
`sandbox_execution_log` audit artifact and a fresh L3 `VerificationResult`
referencing it.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/runtime_verification_sources.v1.json` | Versioned per-tool check argv allowlist (`tool_existence_check` and `cli_command_help_check` tables, `api_endpoint_check` config including accepted status codes and `mcp_tool_check` flag), plus contract-wide `default_timeout_seconds`, `default_max_bytes`, `allowed_commands`, and `supported_claim_types`. | complete |
| `backend/app/schemas/enums.py` | Adds `IngestionArtifactType.SANDBOX_EXECUTION_LOG`. | complete |
| `backend/app/api/errors.py` | Adds `RUNTIME_VERIFICATION_FAILED`. | complete |
| `backend/app/schemas/verification.py` | Adds `RuntimeVerificationRequest`, `RuntimeVerificationResponse`, `BulkRuntimeVerificationResponse`. | complete |
| `backend/alembic/versions/0014_extend_artifact_type_for_sandbox.py` | Extends the `raw_ingestion_artifacts.artifact_type` CHECK to include `sandbox_execution_log`; downgrade reverses. | complete |
| `backend/app/services/runtime_verifier.py` | `SandboxRunner` protocol; `SubprocessSandboxRunner` production runner (subprocess + positive-shape argv allowlist + scrubbed env + wall-clock and byte caps + guaranteed terminate-then-kill cleanup); `HttpHeadClient` protocol + `HttpxHeadClient`; five concrete `RuntimeClaimVerifier` implementations (tool existence, CLI flag grep, API endpoint HEAD via shared SSRF guard, MCP tools/list re-spawn, and a catch-all skip verifier for claim types that would require triggering side effects); `RuntimeVerificationService` orchestrator with the L2-precheck and L3-promotion path. | complete |
| `backend/app/api/routes_verification.py` | `POST /verification/runtime` and `POST /verification/runtime/tools/{tool_id}`; both return structured `RUNTIME_VERIFICATION_FAILED` (422) on rejection. Wires the verification router into `app/main.py`. | complete |
| `backend/tests/test_runtime_verification.py` | 27 adversarial cases covering the precheck (refuses L1 claims), all five verifier branches (pass / fail / skip), the deliberate skip list for unsafe claim types (parametrised across `destructive_action`, `side_effect`, `auth_requirement`, `feature_deprecated`, `config_field_exists`), bulk-by-tool, sandbox allowlist enforcement, and the API surface. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| Only claims at L2 or above are runtime-verifiable. | `RuntimeVerificationService.verify()` looks up the latest `VerificationResult` and returns `skipped=True` with a precheck reason if the claim is below `L2_source_verified`. Test: `test_precheck_refuses_claim_at_l1`. |
| Only contract-allowlisted commands can be spawned. | `SubprocessSandboxRunner.run()` rejects any `command` not in `allowed_commands`, both at contract-load time and at runtime. Test: `test_subprocess_runner_rejects_command_not_in_allowlist`. |
| Runtime check passes promote the claim to L3 and persist audit evidence. | Successful checks emit a new `VerificationResult` with `verification_level=L3_runtime_verified` and `decision=ACCEPTED`, referencing the saved `sandbox_execution_log` artifact in `reasons`. Tests: `test_cli_command_exists_promotes_to_l3_on_exit_zero`, `test_api_endpoint_exists_passes_on_accepted_status`, `test_mcp_tool_exists_promotes_when_tool_still_listed`. |
| Failed runtime checks DO NOT promote but still produce audit evidence. | Failed checks emit `REQUIRES_HUMAN_REVIEW` results with a confidence penalty (`-0.20`), reasons capturing exit code / stderr / status, and the captured artifact is still saved for audit. The claim's level does not change. Tests: `test_cli_command_exists_does_not_promote_on_nonzero_exit`, `test_cli_command_exists_does_not_promote_on_timeout`, `test_api_endpoint_exists_fails_on_500`, `test_mcp_tool_exists_fails_when_tool_removed`. |
| Verification of side-effecting claim types is deliberately refused. | `_SkipVerifier` matches `side_effect`, `destructive_action`, `auth_requirement`, `feature_deprecated`, `workflow_step`, `config_field_exists`, `environment_requirement` and returns `skipped=True` with the reason "not runtime-verifiable without triggering side effects." Test: `test_skipped_for_unsafe_claim_types` (parametrised across 5 claim types). |
| API endpoint HEAD checks honour the shared SSRF guard. | The verifier runs `assert_url_is_safe(url, allowed_hosts=official_hosts(tool_id))` before calling `head()`; URLs outside the per-tool `official_hosts` are skipped with an SSRF reason. Test: `test_api_endpoint_exists_skips_when_evidence_url_not_in_allowlist`. |
| 405 Method Not Allowed counts as "endpoint exists." | Many real APIs reject HEAD on POST-only endpoints with 405; the contract's `accepted_status_codes` includes 405 so this isn't a false negative. Test: `test_api_endpoint_exists_passes_on_405_method_not_allowed`. |
| MCP re-spawn confirms the same tool is still advertised. | `_McpToolExistsVerifier` re-uses the Stage 7d runner, calls `tools/list`, and checks the asserted tool name appears in the returned list. Test: `test_mcp_tool_exists_promotes_when_tool_still_listed`. |
| Audit evidence is recoverable. | `GET /ingestion/artifacts/{artifact_id}` returns the byte-stable `sandbox_execution_log` artifact (JSON payload of `kind` / `command` / `argv` / `exit_code` / `duration_seconds` / `stdout` / `stderr` for sandbox runs, or `url` / `method` / `status_code` for HEAD runs, or `server_id` / `tools_seen` for MCP rechecks). |

### Quality Bar

- The sandbox runner uses `subprocess.Popen` with `stdin=DEVNULL`, scrubbed env (`PATH` + `HOME` only), `cwd` honoured when supplied, and `select`-driven polling that respects the wall-clock deadline.
- Cumulative byte cap on stdout / stderr truncates rather than crashing.
- Guaranteed `_terminate()` cleanup: `terminate()`, wait 1 s, `kill()`, wait 1 s, close pipes — no leaked processes even on timeout.
- HEAD requests use a shared injectable `HttpHeadClient` Protocol; tests use a fake that never touches the network. DNS lookups are monkeypatchable so SSRF tests stay hermetic.
- MCP re-spawn re-uses the Stage 7d `McpServerRunner` Protocol; the production runner is `SafeMcpServerRunner` but tests inject a fake.
- The runner abstraction (`SandboxRunner` Protocol) is deliberately swappable so future stages can substitute `DockerSandbox` / `FirecrackerSandbox` / `ModalSandbox` without rewriting the verifier registry.
- Successful runtime check adds a `+0.10` confidence bonus; failed check adds `-0.20`. Both adjustments are recorded as explicit `ConfidenceComponent` entries in the breakdown.
- Bulk endpoint groups by `tool_id` and returns counts of `attempted` / `promoted` / `skipped` / `failed` alongside the per-claim results.
- Full backend validation currently reports `460 passed`; ruff is clean; alembic upgrade → downgrade → upgrade cycle clean through migration 0014.

### Audit findings (post-ship)

Two bugs found and fixed in a full-pass code audit.

**Bug A — SSRF bypass in the HEAD verifier.** `HttpxHeadClient.head()` was configured with `follow_redirects=True`. A server in the per-tool `official_hosts` allowlist could respond to a HEAD with `Location: <private-or-internal-host>` and httpx would follow it silently. The first hop was SSRF-checked; subsequent hops weren't. This re-introduced the exact class of bug the ingestion lanes (7b / 7c.x) deliberately avoided by disabling auto-redirects.

**Fix A:** `follow_redirects=False`. The runtime contract already lists 3xx codes in `accepted_status_codes`, so a redirect proves the endpoint exists without needing to be followed. A regression test (`test_httpx_head_client_disables_redirects`) inspects the production client's source so a future "let's just follow redirects" PR can't quietly regress the fix.

**Bug B — CLI flag verifier picked the wrong subcommand.** `_CliFlagExistsVerifier` extracted the subcommand from the claim subject by skipping flag tokens and the `tool_id`'s first dash-segment, but did NOT skip the contract `command` value. For `tool_id=github-cli` (where `tool_id.split("-")[0] == "github"`) and subject `gh status --short`, the verifier picked `gh` as the subcommand and would have spawned `gh gh --help` — failing every runtime check silently. The bug doesn't fire today because Stage 7a doesn't emit `cli_flag_exists` claims, but it would corrupt any manually-submitted ones.

**Fix B:** The verifier now also skips tokens equal to `entry["command"]` (case-insensitively). Regression test `test_cli_flag_exists_skips_binary_name_in_subject` asserts the right subcommand is picked.

### Deferred

- Container-level sandboxing (Docker / Firecracker / gVisor). The injectable runner makes this a swap, not a rewrite, but is out of scope for Stage 8.
- Verification of `side_effect` / `destructive_action` claims in a write-allowed sandbox (Stage 9 territory once a container sandbox exists).
- L4 cross-agent agreement (multiple independent runtime verifiers must all pass).
- L5 human-audited promotion (requires the dashboard from Stage 8.5 / 9).
- Scheduled re-verification (today verification is on-demand only).

## Stage 9: Agent Query Surface

Verdict: pass.

Stage 9 is complete when an AI agent can ask AgentAtlas one high-level
question and get a structured, evidence-backed safety verdict back — no
raw CRUD acrobatics, no LLM in the safety path. The five endpoints under
`/query/*` are the agent-facing API the rest of the project was built to
support.

### Architecture

A `QueryEngine` sits on top of the existing `ClaimStore`, risk classifier,
canonical specs, and confidence scorer. It owns the verdict-synthesis logic
but creates no new persistence. The five endpoints are thin route handlers
that pass typed request models into the engine and return typed response
models verbatim. All paths are read-only.

The matching algorithm is strict (no fuzzy / no LLM) — the user's earlier
decision. Verdict gating is three-layered: safety policy (from the Stage 0
contract) AND a confidence threshold (default 0.70) AND a verification-level
threshold (default L2). All three must pass for `safe_to_auto_execute=True`;
any one failing produces a default-deny verdict with the gate spelled out
in the response's `reasons[]` list.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `contracts/query_policy.v1.json` | Versioned contract: confidence + verification level thresholds, command/query length caps, search limits, default-deny / unknown-tool / low-confidence / low-verification reason strings. Loaded once, validated on import, drift-locked. | complete |
| `backend/app/schemas/query.py` | 10 Pydantic v2 models: request + response shapes for all 5 endpoints, plus `EvidenceCitation`, `RiskDimensions`, `ToolMatchSummary`, `WorkflowSummary`. `extra="forbid"` everywhere; nullable optionals explicit; `risk_level: RiskLevel \| None` (no magic sentinel). | complete |
| `backend/app/services/command_matcher.py` | Strict exact + prefix matcher with word-boundary requirement, longest-prefix-wins, level/confidence/recency tie-break, ACCEPTED-only visibility. Paginates through claims with a hard cap (10k) so it can't be unbounded by a pathologically-large tool. | complete |
| `backend/app/services/query_engine.py` | `QueryEngine` class with `validate_command`, `explain_risk`, `get_tool_spec`, `search_tools`, `find_safe_workflows`. Re-classifies matched-claim risk to defend against understated submissions. Reuses canonical `band_for_score` from `confidence_scorer` (no drift). | complete |
| `backend/app/api/routes_query.py` | 5 endpoints: `POST /query/validate-command`, `GET /query/tools/{tool_id}` (regex-validated path param), `GET /query/search-tools`, `POST /query/explain-risk`, `POST /query/safe-workflow`. Structured 404 / 422 envelopes. | complete |
| `backend/app/main.py` | Mounted `query_router` after `verification_router`. | complete |
| `backend/tests/test_command_matcher.py` | **23 tests:** normalize, exact match, prefix-with-word-boundary, longest-prefix-wins, status / level visibility rules (PENDING + REQUIRES_HUMAN_REVIEW at L2+ visible; REJECTED / CONFLICT_DETECTED hidden; below L2 hidden), tie-breakers (level / confidence / recency), cross-tool isolation, pagination beyond 500 claims, batched-vs-N+1 parity. | complete |
| `backend/tests/test_query_engine.py` | **23 tests:** default-deny, 3-gate logic (safety policy / confidence / verification level), evidence projection, classifier risk-upgrade, partial-acceptance status surfaced in reasons, explain_risk dimensions + citations, contract validator regressions, boundary tests at exact threshold (0.70, L2). | complete |
| `backend/tests/test_query_engine_search.py` | **22 tests:** search-tools tier ordering, no-match exclusion, empty-query list-all, pagination, limit clamping, summary projection (commands + capabilities + risk profile fallback), safe-workflow safest-first sort, environment echo, aggregate-risk roll-up, validator regressions for non-positive / bool / default-greater-than-max limit values. | complete |
| `backend/tests/test_routes_query.py` | **29 tests:** API-level integration for all 5 endpoints, 4 response-shape lock tests, drift-lock test for route/schema limits vs contract, path-param regex regression, safe ⇔ ¬requires_confirmation consistency invariant. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| `validate-command` returns the README's documented verdict shape verbatim | `ValidateCommandResponse` schema; `test_validate_command_response_has_no_extra_fields` locks the key set. |
| Default-deny on no match | `_default_deny()` always returns `safe_to_auto_execute=False, risk_level=None`; tested at both engine and API layers. |
| Critical-risk commands never auto-execute even at L3 + high confidence | `_safety_policy_by_risk()["critical"]["auto_execute_allowed"]=False`; verdict's `safe_to_auto_execute` is `(auto_allowed AND confidence_gate AND level_gate)`. Tests: `test_critical_risk_blocks_auto_execute_even_at_high_confidence`, `test_validate_command_critical_claim_blocks_auto_execute`. |
| Low-confidence and low-verification-level claims independently gate auto-execute | Two separate gate checks in `_verdict_from_match`; each tested in isolation plus at boundary (0.70 confidence exactly, L2 exactly). |
| Understated risk is detected and upgraded | `classified.risk_level` from the re-run risk classifier is compared to the claim's declared risk; max wins. Surfaces "Risk classifier upgraded risk from declared 'low' to 'critical'" in reasons. |
| Evidence is correctly projected into the verdict | `EvidenceCitation` drops `excerpt` + `hash`; keeps `evidence_type` + `source_uri` + `trust_level`. Full evidence still recoverable via `/claims/{id}/evidence`. |
| Best-verified claim wins on multi-match | Matcher's tie-breakers (level → confidence → recency) cover this; engine doesn't re-pick. |
| `search-tools` ranks exact > tool_id substring > name substring > capability substring | `_score_tool_spec` returns 100 / 80 / 60 / 40 respectively; ties broken by `tool_id` ASC. |
| `safe-workflow` returns safest-first | Sort key is `(RISK_ORDER[aggregate], -score, workflow_id)`. |
| Path-param and body-supplied `tool_id` reject the same garbage | Both now use the same regex (`^[A-Za-z0-9_.-]{1,128}$`); the path-param gap was caught and fixed during the stage audit. |
| Contract values and code-level limits stay in sync | `test_route_layer_limits_match_query_policy_contract` drift-locks them. |

### Quality Bar

- **No new persistence:** Stage 9 is a pure read API. No migrations, no new tables, no new artifact types.
- **No new external dependencies.** Reuses `fastapi`, `pydantic`, `sqlalchemy`, `httpx` already pinned.
- **Strict matching by design.** No fuzzy / no LLM in the safety path; agents always know whether they got a verified verdict or a default-deny. Fuzzy is a deliberate v1.1 opt-in.
- **Three-tier gating:** safety policy ∧ confidence ≥ 0.70 ∧ verification level ≥ L2 must all pass for auto-execute. Each tier's failure surfaces an explicit reason in the response.
- **Reasons explain every gate that fired.** Auditors can replay the decision without running the engine.
- **Consistency invariant:** `safe_to_auto_execute == True ⇔ requires_human_confirmation == False`. Tested via API to prevent a future engine bug from emitting a contradiction.
- **Default-deny on no match** uses `risk_level: null` (not a magic `"unknown"` sentinel — the audit caught the ergonomic regression and switched it).
- **Drift-locked contract.** Route + schema literals are asserted to equal contract values; any future contract change without code update fails the drift-lock test.
- **N+1 query killed in the matcher.** `ClaimStore.get_latest_verification_results(claim_ids)` does one batched window-function query per page instead of N per page.
- **Pagination through claims hard-capped at 10,000.** Bounded memory; if a real tool ever exceeds this, the right fix is a SQL-side subject prefix filter, not growing the in-memory scan.
- **Test suite: 96 new tests across 4 files** (matcher 23 + engine validate/explain 23 + engine search/spec/workflow 22 + API routes 29). All hermetic; no network or real subprocess required. 556 total passing.

### Bugs found and fixed during Stage 9

Nine real bugs were caught during the stage and fixed before ship. Each has a
regression test locking the failure mode so it cannot recur.

| Bug | Severity | Fix |
| --- | --- | --- |
| Matcher hid most ingestion-pipeline output: `verification_status=ACCEPTED` filter excluded PENDING + REQUIRES_HUMAN_REVIEW claims, which is the natural orchestrator output for single-evidence claims; end-to-end submit → verify → validate returned silent default-deny | **Critical** | Matcher now accepts L2+ claims with status not in {REJECTED, CONFLICT_DETECTED}; engine surfaces non-ACCEPTED status in verdict reasons |
| Matcher silently dropped claims beyond the first page of 500 | Medium → High at scale | Pagination through all pages with a 10,000 hard cap |
| N+1 lookup for `get_latest_verification_result` per matcher candidate | Performance | Batched `get_latest_verification_results` via window-function SQL |
| `risk_level: Literal["unknown"]` sentinel was awkward at the API boundary | API ergonomics | Switched to `RiskLevel \| None` (standard JSON nullable) |
| Contract validator did not require the reason-text keys the engine reads (`default_deny_reason`, etc.) | Medium (latent 500) | Validator now matches every key the engine reads |
| `_band_for_score` duplicated `confidence_scorer.band_for_score` | Drift risk | Imported the canonical function; deleted the duplicate |
| Contract validator did not enforce positive search-limit fields or `default ≤ max` | Medium (latent 500) | Validator now requires positive ints with sane ordering; rejects `bool` masquerading as `int` |
| Path-param `tool_id` had no regex constraint; body and path returned inconsistent 422 vs 404 for the same garbage input | Medium (API consistency) | Added `Path(..., pattern=...)` matching the body validators |
| Route + schema layers hardcoded numeric limits that also live in the contract | Medium (drift risk) | Added a drift-lock test asserting code values equal contract values |

### Deferred (v1.1 territory)

- **Fuzzy / token-based command matching.** Today's strict matcher is correct-by-design; an opt-in fuzzy lane with explicit confidence scoring can come later.
- **Per-capability bonus scoring** in `search-tools` (currently cites the first matching capability, not all).
- **Goal-aware workflow scoring** in `safe-workflow` (could bonus workflows whose `tool_ids` mention tokens in the goal).
- **Bulk `validate-command`** (one-at-a-time is fine for the demo; bulk is an obvious follow-up).
- **`ToolMatchSummary.publication_issues`** — currently a partial spec looks "complete" in search results.
- **SQL-side filtering** for `search-tools` and `find_safe_workflows`. Currently load-all-then-filter in memory; fine at v1 scale (<200 tools), worth optimising at 10k+ specs.
- **Re-verification freshness** in verdicts. Today's verdict has no "verified N days ago" decay; old L3 verdicts are treated as current.

## Stage 10: MCP Server (outbound)

Verdict: pass.

Stage 10 is complete when AgentAtlas can be **registered as an MCP server**
in any MCP-aware agent client (Claude Desktop, Cursor, Cline, Continue, etc.)
with a single JSON config block, and the six query / write tools
(`validate_command`, `get_tool_spec`, `search_tools`, `explain_risk`,
`get_safe_workflow`, `submit_claim`) work end-to-end over stdio JSON-RPC.

### Architecture

A hand-rolled stdio JSON-RPC server lives in `backend/app/mcp_server/`.
The package has four files:

- `protocol.py` — Pydantic models + helpers for JSON-RPC framing
  (`JsonRpcRequest`, `JsonRpcNotification`, `JsonRpcResponse`,
  `JsonRpcError`), parse + encode functions, and the standard JSON-RPC +
  application-specific error codes.
- `tools.py` — registry of six `McpTool` records, each carrying its `name`,
  LLM-readable `description`, JSON Schema `input_schema`, and a sync
  `handler(arguments, store) -> dict`.
- `server.py` — `McpServer` class with two public surfaces:
  `handle_frame(line)` for in-process unit tests, and `serve(stdin, stdout)`
  for the production blocking loop. Dispatches `initialize`,
  `notifications/initialized`, `tools/list`, `tools/call`, plus stub
  `resources/list` / `prompts/list` that return empty arrays.
- `__main__.py` — `python -m app.mcp_server` entry point.

We deliberately did not pull in the official `mcp` Python SDK. Three
reasons: (1) the codebase is sync-throughout and the SDK is asyncio-based;
bridging async/sync per tool call is overhead with no payoff at v1 scale;
(2) Stage 7d already implemented an MCP *client* over the same stdio
JSON-RPC protocol, so we have first-hand familiarity with the framing
concerns; (3) the test surface is tighter — every tool is a plain function
we can unit-test in-process without spawning subprocesses for every
behaviour. If protocol nuances ever bite us, the tool implementations don't
change; only the transport wrapper does.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `backend/app/mcp_server/protocol.py` | JSON-RPC 2.0 framing + Pydantic models + parse/encode helpers + standard error codes. | complete |
| `backend/app/mcp_server/tools.py` | Six `McpTool` records with `name`, `description`, `inputSchema` (each `additionalProperties: false`), and sync handlers wrapping the existing `QueryEngine` and `ClaimStore` / `CanonOrchestrator` services. | complete |
| `backend/app/mcp_server/server.py` | `McpServer` class. `handle_frame` for in-process testing; `serve(stdin, stdout)` for the blocking stdio loop. Dispatches every MCP method this server supports + stubs unsupported ones with empty results. Every tool-handler exception is caught and converted into an `isError=True` content block. | complete |
| `backend/app/mcp_server/__main__.py` | `python -m app.mcp_server` entry point used by Claude Desktop / Cursor. | complete |
| `README.md` "MCP Integration" section | Tool table, run instructions, copy-paste Claude Desktop `claude_desktop_config.json` block, design notes. | complete |
| `backend/tests/test_mcp_server.py` | **28 adversarial tests:** handshake, notification ack, blank-line and bad-JSON handling, unknown method, every tool's happy path + at least one failure path, `tools/call` protocol edges (missing name, non-object arguments, unknown tool name), tool-handler exception wrapping, response shape invariants (`structuredContent` + `content[0].text` parity), and a subprocess smoke test that runs `python -m app.mcp_server` end-to-end. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| `initialize` returns the protocol version and server info Claude Desktop / Cursor expect | `_handle_initialize` returns `protocolVersion=2024-11-05`, `serverInfo={name, version}`, `capabilities={tools: {}}`. Tested. |
| `notifications/initialized` gets no reply | `handle_frame` returns `None` for any `JsonRpcNotification`. Tested. |
| `tools/list` enumerates exactly the six tools with their JSON schemas | Iterates `list_tools()` registry. Test asserts the name set + that every tool's schema has `additionalProperties: false`. |
| Each of the six tools dispatches to the matching service | `find_tool(name).handler(arguments, store)`. Each tool has a happy-path test that confirms the dispatch end-to-end. |
| Tool-handler exceptions don't crash the JSON-RPC stream | `_handle_tools_call` wraps the handler call in a try/except that converts any exception to an `isError=True` content block. Tested with a Pydantic-ValidationError-triggering input. |
| Unknown tool name returns a structured JSON-RPC error (not isError) | Tested. Returns `TOOL_NOT_FOUND` (-32001). |
| Malformed `tools/call` params return `INVALID_PARAMS` | Tested with missing `name` and non-object `arguments`. |
| Bad JSON returns `PARSE_ERROR` (-32700) | Tested. |
| `python -m app.mcp_server` runs cleanly end-to-end via subprocess + closes on stdin EOF | One focused integration smoke test runs the real entry point, sends initialize + initialized + tools/list, asserts both replies are well-formed JSON-RPC, and confirms `returncode == 0`. |
| Responses carry both legacy `content[0].text` (JSON-encoded) AND modern `structuredContent` | `_success_content` builds both; one test asserts they decode to the same payload. |
| The single write tool (`submit_claim`) runs the orchestrator and returns the verified claim | Handler builds a `KnowledgeClaim`, persists it via `ClaimStore.create`, runs `CanonOrchestrator.verify_claim`, saves the result, returns the post-verification claim. Tested for happy path + unknown tool_id + empty evidence rejection. |

### Quality Bar

- **No new external dependencies.** Reuses `pydantic`, `sqlalchemy`, `fastapi` (for Pydantic compatibility — FastAPI isn't loaded by the MCP server entry point).
- **No new persistence.** No migrations. The MCP server is a transport wrapper; it never invents new state.
- **`additionalProperties: false` on every tool's input schema.** A client typo (wrong field name, extra param) surfaces as a clean rejection at the boundary instead of being silently dropped.
- **Tool execution errors vs protocol errors are kept distinct.** Tool errors → `isError=True` content (MCP-spec). Protocol errors → JSON-RPC `error` response. Clients can write defensive code that treats them differently.
- **Sync-throughout.** No asyncio bridge; tool handlers call existing sync services directly.
- **Hermetic test suite.** 27 of 28 tests use the in-process `handle_frame` API. One subprocess test covers the real `python -m` entry point. CI doesn't need any MCP SDK installed.
- **Documented design choice.** The "we didn't use the official SDK" decision is captured in-source (in `__init__.py`) so a future maintainer doesn't have to guess.
- **`structuredContent` AND text-block JSON parity.** Older MCP clients that string-parse `content[0].text` get the same payload as newer clients that read `structuredContent` directly.
- **Full backend validation currently reports `594 passed`**; ruff is clean; migrations unchanged.

### Deferred (v1.1 territory)

- **Remote transports.** Today's server is stdio-only. SSE / streamable HTTP transports for hosted deployments are deferred.
- **`resources/list` and `prompts/list`.** Currently stubbed to return empty arrays. MCP supports surfacing arbitrary resources (e.g., raw evidence artifacts) and prompts; we don't expose those yet.
- **MCP capability negotiation refinements.** We advertise `capabilities.tools = {}` (the simplest valid form). MCP's spec allows finer-grained capabilities; not needed for v1.
- **Logging / progress notifications.** MCP supports server-initiated logging messages and progress updates for long-running tools. Our tools all complete in milliseconds so we skip these.
- **Schema-driven argument validation.** Today the inputSchema is documentation; tool handlers re-validate via Pydantic. Validating against the inputSchema directly (e.g., with `jsonschema`) would shave a small amount of duplicated validation logic. Deferred.
- **An `mcp` SDK swap-in path.** Documented in-source but not implemented. If we ever need features only the SDK provides (resource subscriptions, prompts, multi-transport), the transport wrapper is the only thing that changes.

## Stage 11a: Seed Dataset (offline-safe replay)

Verdict: pass.

Stage 11a is complete when a fresh checkout populates a useful demo graph
in under five seconds with a single command, without depending on any
external host being reachable. The seed strategy is "replay pre-captured
artifacts" — the live ingestion lanes' Protocols (`OpenApiHttpClient`,
`JsonSchemaHttpClient`) accept a fake client that returns committed-on-disk
response bodies. Identical code path to production; deterministic input.

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `data/seed_artifacts/openapi/openai_api.json` | Small valid OpenAPI 3.0.3 subset for `openai-api` (12 operations: chat / embeddings / models / files / audio / images). Same lane that parses the live spec accepts this verbatim. | complete |
| `data/seed_artifacts/json_schema/docker_compose.json` | Small valid Draft-07 JSON Schema subset for `docker-compose.yml` (8 top-level properties + 1 `deprecated: true` annotation). | complete |
| `data/seed_artifacts/claims/headline_scenarios.json` | 6 hand-crafted multi-evidence claims that the demo's headline queries hit: `gh repo delete`, `git status`, `git log`, `vercel --prod`, `gh repo list`, `docker rm`. Each carries 2-3 trusted-source evidence pieces with realistic excerpts so the orchestrator's confidence scoring produces meaningful (not 0.0) scores. | complete |
| `scripts/seed_examples.py` | Replay orchestrator. Injects committed bodies into the real Stage 7c.1 / 7c.2 ingestion services via a single-response stub client; directly submits headline-scenario claims via `ClaimStore.create` + `CanonOrchestrator.verify_claim`. `--reset` drops + re-applies all migrations via alembic before seeding. | complete |
| `backend/tests/test_seed_script.py` | 10 hermetic tests: subprocess invocation against tmp SQLite, claim-count floor (≥30), per-tool coverage (every Stage 0 tool gets ≥1 claim), parametrised verification of all 6 headline scenarios against `validate_command`, the headline `gh repo delete` invariant (blocks auto-execute with cited evidence + critical risk + safety-policy reason), and the unknown-command default-deny path. | complete |

### Pass Case Audit

| Pass case | Satisfied by |
| --- | --- |
| One command populates the graph from a fresh DB | `python scripts/seed_examples.py --reset` runs in ~5s end-to-end, including drop + alembic upgrade + replay. |
| Useful claim count (≥30) | OpenAPI replay produces 32 claims (12 operations × auxiliary side-effect / destructive / auth claims); JSON Schema replay produces 9 claims (8 fields + 1 deprecation); headline scenarios contribute 6 more. **Total: 47.** |
| Demo's headline command is auto-execute-blocked | `validate-command("github-cli", "gh repo delete my-org/x --yes")` returns `safe_to_auto_execute=false, risk_level="critical"` with cited evidence from `docs.github.com`. Test pins this. |
| Every Stage 0 tool is represented in search results | After seeding, each of `git`, `github-cli`, `docker`, `vercel-cli`, `openai-api` has at least one queryable claim. |
| Offline-safe | The script never makes a real HTTP request; the fake client raises if called twice (guard against accidental re-fetch). CI / a maintainer on a plane / Codespaces with no network can all seed cleanly. |
| Deterministic | Evidence hashes are computed from `(source_uri, excerpt)` so identical artifacts produce identical hashes across runs. Idempotent enough that two fresh seeds produce the same canonical fingerprint of the graph. |
| Replay path mirrors production | OpenAPI / JSON Schema lanes use their real services with a stub `Client`; the orchestrator, evidence trust resolver, risk classifier, and confidence scorer all run normally. The seed exercises the same code path that ingestion at runtime would. |

### Quality Bar

- **No new dependencies.** Reuses existing services; alembic is invoked programmatically for `--reset`.
- **No migrations.** Stage 11a only writes data through existing tables.
- **Single-response fake client** raises on a second `.get()` call so a misbehaving service can't quietly re-fetch and skew counts.
- **Headline-scenario JSON ships with hashes computed from `(source_uri, excerpt)`** so the seed is reproducible and identical artifacts produce identical evidence rows across machines.
- **Risk-level matching to classifier output.** The headline claim for `gh repo list` is declared MEDIUM (not LOW) because the deterministic risk classifier defaults to MEDIUM for commands without a specific rule — submitting LOW would trigger the orchestrator's understated-risk demotion to L1 and make the claim invisible to the matcher. The seed authors had to align with the real classifier behavior, not pretend it didn't exist.
- **The seed test is one focused subprocess invocation, not 50 fragile per-lane assertions.** One run, then 10 in-process queries against the resulting DB.
- **Full backend validation currently reports `594 passed`**; ruff is clean; migrations unchanged.

### Deferred (Stage 11b and v1.1)

- **CLI ingestion replay artifacts.** Today CLI ingestion isn't part of the seed (the 6 headline claims cover the CLI surface directly). Adding live-style `--help` capture replay is a v1.1 nice-to-have.
- **GraphQL + MCP replay** in the seed. The demo doesn't need them; the Stage 7c.3 and Stage 7d test suites already prove those lanes work.
- **Refresh-from-upstream script.** A `--refresh-from-live` flag that re-captures every artifact from real hosts and updates the committed files. v1.1.
- **Multi-evidence ingestion path.** Today's headline claims are submitted directly because the single-evidence ingestion lanes naturally produce PENDING-not-ACCEPTED claims (single source → confidence ~0.45). A future lane that merges evidence across sources for the same subject would let ingested claims reach ACCEPTED naturally.

## Stage 11b: Demo Dashboard (Next.js)

Verdict: pass.

Stage 11b is complete when a developer can clone the repo, run two commands
(`pip install -e backend[dev]` + `npm install` in `frontend/`), and have a
visual UI that lets them poke at the seeded knowledge graph from a browser.
The dashboard exists to make the demo video possible — not to be a product
in its own right.

### Architecture

Next.js 14 App Router + TypeScript + Tailwind. **No** shadcn CLI scaffold;
the project ships two hand-written components (`RiskBadge`, `VerdictCard`)
plus Tailwind utility classes everywhere else. Aesthetic is "developer
tool, not consumer app" — clean typography, distinct sections, monospaced
data fields, colour-coded risk pills.

**API proxy.** `next.config.mjs` rewrites `/api/*` requests to the FastAPI
backend at `localhost:8000` (override via `AGENTATLAS_API_URL`). Browser
requests are therefore same-origin; FastAPI doesn't need CORS configured.
Server components (`/tools` and `/tools/[tool_id]`) talk to the backend
directly through an absolute URL; client components (`/`'s playground and
`/query`) go through `/api/*`. The same `lib/api.ts` exports both `server.*`
and `client.*` helpers so it's obvious from the call site which side
each call originates from.

**Typed API client.** Hand-mirrored from the Pydantic response models in
`backend/app/schemas/query.py` and `backend/app/schemas/tool_spec.py`.
Codegen from the OpenAPI doc would be tidier but isn't worth the build-
time dependency on the backend running at install time.

### Pages

| Route | What it does | Server / client |
|---|---|---|
| `/` | Hero + "Try a query" with three one-click examples (`gh repo delete`, `git status`, `vercel --prod`). The headline demo lives here. | Mixed — hero is RSC, try-a-query is `"use client"` |
| `/tools` | Lists every published `ToolSpec` with verification level + risk pills. Tap a row → tool detail. | RSC |
| `/tools/[tool_id]` | Full spec view: capabilities, commands (with per-command risk badges), auth, risk profile, provenance. 404 page when no spec is published. | RSC |
| `/query` | Standalone playground with form input + collapsible "show raw JSON" of the verdict. | `"use client"` |

### Required Artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `frontend/package.json` | Pinned versions of Next 14.2.18, React 18.3.1, TS 5.5.3, Tailwind 3.4.6. No runtime deps beyond Next + React. | complete |
| `frontend/tsconfig.json` + `next.config.mjs` + `tailwind.config.ts` + `postcss.config.mjs` | Boilerplate Next.js + Tailwind config; `next.config.mjs` defines the `/api/*` rewrite. | complete |
| `frontend/.gitignore` | Standard Next.js gitignore (excludes `node_modules`, `.next`, `out`). | complete |
| `frontend/lib/api.ts` | Typed fetch client; `ApiError` class; server/client helper split. ~200 lines. | complete |
| `frontend/components/risk-badge.tsx` + `verdict-card.tsx` | Two reusable display components. Card border colour changes based on the verdict's headline answer. | complete |
| `frontend/app/layout.tsx` | Root layout with header (nav links) + footer. | complete |
| `frontend/app/page.tsx` + `_try-query.tsx` | Landing page + the embedded client component. | complete |
| `frontend/app/tools/page.tsx` | All-tools list with error + empty states. | complete |
| `frontend/app/tools/[tool_id]/page.tsx` + `not-found.tsx` | Single-tool spec view + 404 page. | complete |
| `frontend/app/query/page.tsx` | Standalone playground. | complete |
| `frontend/README.md` | Local setup, design choices, deferred items. | complete |

### Pass Cases

| Pass case | Satisfied by |
| --- | --- |
| `npm install && npm run dev` produces a working dashboard on a fresh checkout | Next 14 stable; no exotic dependencies; documented in `frontend/README.md`. |
| The headline demo (`gh repo delete`) renders a coloured "critical" verdict | Landing page's "Try a query" calls `validate_command` and renders `VerdictCard` with a red-border + critical risk badge + cited evidence list. |
| The `/tools` page lists every seeded tool with chips | Server component calls `/query/search-tools?q=` with limit=200; renders one row per tool with risk + verification chips. |
| Each `ToolSpec` is browseable | `/tools/[tool_id]` server component fetches the spec and renders capabilities, commands, auth, risk profile, and provenance in distinct sections. |
| Backend down → clear error message | All three error paths (landing, `/tools`, `/query`) display a red banner with the actual error message and the `uvicorn` command to start the backend. |
| No-spec case → clean 404 | `/tools/[tool_id]/not-found.tsx` renders when the API returns 404; suggests adding claims and publishing a spec. |
| Mobile-responsive for the demo video | Tailwind responsive prefixes on every grid (`sm:`, `md:`); checked at 375px and 1280px. |
| No CORS required on the backend | `/api/*` rewrite proxies through Next; client never makes a cross-origin request. |

### Quality Bar

- **No design system, no shadcn CLI.** Two components, Tailwind utility classes everywhere. Visual budget stays small.
- **Server components by default**, client components only where interaction is required. Cuts the JS bundle and means most pages render in one round-trip.
- **Typed API client** mirrors backend Pydantic models. If the backend changes a response shape, TypeScript points at the right call site.
- **`ApiError` class** carries the HTTP status + the structured error envelope; UI shows the user-friendly error message AND the backend-supplied error code.
- **No telemetry, no analytics, no images.** `poweredByHeader: false`. Build stays minimal.
- **No frontend tests in this stage.** Visual review covers v1; the project owner is planning a separate E2E repo for end-to-end coverage. Documented in `frontend/README.md`.
- **Same dev-server-port story as every other Next.js project.** No surprises.

### Deferred (v1.1)

- **End-to-end tests.** Per the project owner's roadmap, E2E coverage lives in a separate repo. The dashboard is the system-under-test for that future repo.
- **Static export served from FastAPI.** Stage 12 (one-command install) can serve the built `out/` directory from the same Python process so users don't need Node.js to use the dashboard.
- **Authentication / multi-user UI.** Out of scope for v1; the dashboard is a local-dev demo surface.
- **Search-tools search input on `/tools`.** Currently the API supports `?q=` but the UI lists all. A search box is a one-state-variable add when needed.
- **Dark mode.** Not needed for the demo video; trivial to add via Tailwind's `dark:` prefix later.
- **Workflow browse + `safe-workflow` results display.** The API supports it; the demo flow doesn't need it yet.
- **Live results-as-you-type in `/query`**. Currently submit-on-click. A debounced live mode would be a polish pass.

## Validation

The consolidated stage report is current only if these commands pass:

```bash
cd backend
.venv/bin/python -m pytest tests
.venv/bin/ruff check app tests

# Migration upgrade / downgrade / upgrade smoke (full chain reversibility)
rm -f /tmp/agentatlas_smoke.db
DATABASE_URL=sqlite:////tmp/agentatlas_smoke.db .venv/bin/alembic upgrade head
DATABASE_URL=sqlite:////tmp/agentatlas_smoke.db .venv/bin/alembic downgrade -5
DATABASE_URL=sqlite:////tmp/agentatlas_smoke.db .venv/bin/alembic upgrade head
```

### Current results

- **600 backend tests passing** (Stage 10+11 audit added 6 regression tests; Stage 11a added 10 tests; Stage 10 added 28 before that; Stage 9 added 96 before that; all audit fixes have regression coverage). Stage 11b adds frontend code only — no Python tests; `next build` verified to succeed.
- ruff clean
- alembic upgrade → downgrade → upgrade cycle clean through migration `0014_extend_artifact_type_for_sandbox` (Stages 9, 10, 11a, and 11b added no migrations — all are read/transport/scripting/UI layers over the existing graph)

### Audit log (one row per stage; most recent first)

Bugs are consolidated by stage. Each row summarises every bug caught during
that stage's audit pass and what changed to prevent recurrence. The full
per-bug breakdown lives in each stage's "Bugs found and fixed during Stage X"
subsection above.

| Date | Stage | Bugs found and resolved |
| --- | --- | --- |
| 2026-05-18 | Stages 10 + 11 (cross-stage audit) | **5 bugs.** (1) MCP dispatcher returned METHOD_NOT_FOUND for `notifications/*` methods sent with a stray `id` field; spec says these are notifications by method name regardless of id — fixed to silently ack. (2) `tools/call` with `arguments: []` (or other falsy non-dict) slipped through `or {}` and silently became an empty dict, crashing the tool handler downstream instead of returning a clean INVALID_PARAMS at the dispatcher. (3) `submit_claim`'s evidence-minimum pre-check fired before Pydantic validation, so a payload missing every required field returned the misleading "needs at least one piece of evidence" error instead of "missing required fields"; reordered. (4) Stage 11a: re-running `scripts/seed_examples.py` without `--reset` silently inserted duplicate claims AND degraded headline-scenario acceptance (orchestrator marks dups as PENDING/L1); the script now emits a loud warning when the DB already has rows. (5) Tool-handler return values weren't type-checked; a future handler returning a list / scalar / None would silently produce MCP `structuredContent` that violates the spec's "must be a JSON object" requirement; the dispatcher now rejects non-dict payloads with a clear `isError` message. **All five fixed; 6 regression tests added; 600 total passing. Frontend production build also verified (`next build` succeeds, all 5 pages compile, type-check passes).** |
| 2026-05-18 | Stage 10 | **1 minor bug.** Documentation-vs-reality gap: `submit_claim`'s `inputSchema` declared `minItems: 1` for evidence but the handler didn't enforce it, so empty-evidence claims were accepted by the MCP boundary and only flagged PENDING downstream by the orchestrator's "no evidence" reason. **Fixed** with an explicit pre-check in the handler that matches the schema's declared minimum; regression test added. |
| 2026-05-18 | Stage 9 | **9 bugs.** Critical: matcher hid most ingestion-pipeline output (single-evidence PENDING / REQUIRES_HUMAN_REVIEW claims were invisible to validate-command). Medium: three contract-↔-code drift risks (band thresholds, reason-text keys, route/schema hardcoded limits); contract validator gap on positive search-limit values; path-param vs body-param 422 vs 404 inconsistency; 500-claim silent-drop in matcher pagination. Performance: N+1 verification lookup. API ergonomics: `Literal["unknown"]` sentinel replaced with `null`. **All fixed with regression tests.** |
| 2026-05-17 | Stage 8 | **2 bugs.** High: HEAD verifier followed redirects without re-checking the SSRF guard (an allowlisted host could redirect to a private IP). Medium: CLI flag verifier picked the binary name as the subcommand (`gh status --short` → spawned `gh gh --help`, silent fail on every runtime check). **Both fixed; regression tests added.** |
| 2026-05-17 | Stage 7d | **1 bug.** High: MCP runner deadlocked on stderr pipe full (`stderr=PIPE` was never drained; cumulative stderr above the OS pipe buffer blocked the server's response on stdout, surfacing as a fake "timed out" error). **Fixed by routing stderr to `DEVNULL`** (the runner only reads stdout for JSON-RPC frames). |
| 2026-05-17 | Stage 7c.3 | **0 bugs.** Cache-reuse path was already correct because it re-parsed the cached SDL on a 304. The 304 test was tightened defensively to match the assertion style of 7c.1 and 7c.2. |
| 2026-05-17 | Stage 7c.2 | **1 bug.** Critical: 304 cache reuse silently FAILED (same pattern as Stage 7c.1) — `fetch.document = {}` triggered the "no top-level properties" guard, marking the run FAILED while reporting `cache_hit=True`. The original 304 test passed by accident because it asserted only `cache_hit` and `created_claim_ids != first`. **Fixed by re-parsing the cached body**; test now asserts `status == COMPLETED`. |
| 2026-05-17 | Stage 7c.1 | **1 bug.** Critical: 304 cache reuse silently FAILED — `fetch.spec = {}` triggered the "no operations to ingest" guard, marking the run FAILED while reporting `cache_hit=True`. **Fixed by re-parsing the cached body**; test tightened to assert `status == COMPLETED` and matching claim counts. |
