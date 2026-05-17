# AgentAtlas Stage Report

This report consolidates the completion status for Stages 0 through 7a.

It records what each stage proves, which artifacts satisfy the stage, what remains deferred, and which validation commands must pass.

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
| Stage 7c | API schema ingestion (OpenAPI / JSON Schema / GraphQL) | not started |
| Stage 7d | MCP metadata ingestion | not started |

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

- API schema ingestion (Stage 7c).
- MCP metadata ingestion (Stage 7d).
- LLM/prose extraction from captured artifacts.
- Canonical auto-publication after ingestion.
- Runtime sandbox verification and L3 promotion (Stage 8).

## Validation

The consolidated stage report is current only if these commands pass:

```bash
cd backend
.venv/bin/python -m pytest tests
.venv/bin/ruff check app tests
```
