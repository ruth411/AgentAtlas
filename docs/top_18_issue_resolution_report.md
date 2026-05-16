# Historical Top 18 Issue Resolution Report

Historical status: closed snapshot from the Stage 0-2 fix queue.

This report is not the current project status document. Current stage status
lives in [Stage Report](stage_report.md).

This report tracked the highest-priority flags identified after Stages 0-2.

## Fixed or materially addressed

1. No orchestrator exists yet.
   Resolution: Added `CanonOrchestrator` with deterministic decisions and `POST /claims/{claim_id}/verify`.

2. Submitted `risk_level` is trusted.
   Resolution: Added `risk_classifier.py`; the orchestrator now challenges understated submitted risk.

3. Verification promotion rules are not enforced.
   Resolution: Added orchestrator rules for no evidence, source evidence, runtime evidence, cross-stream evidence, and human review escalation.

4. No semantic duplicate detection.
   Resolution: Added `ClaimStore.find_semantic_duplicates`.

5. `evidence=[]` is allowed.
   Resolution: Documented the policy explicitly: allowed only for pending intake; orchestrator returns `pending_more_evidence`; not publishable.

6. Evidence quality rules are not enforced.
   Resolution: Added evidence source/hash validation and `evidence_policy.py` for rejected primary-source markers.

7. No conflict detection.
   Resolution: Added `ClaimStore.find_conflicts` and orchestrator `conflict_detected` behavior.

8. No confidence scoring.
   Resolution: Added conservative `score_claim_confidence`.

9. No `claim_type` filter.
   Resolution: Added `claim_type` filter to store and `GET /claims`.

10. Duplicate evidence error is imprecise.
    Resolution: Added `DuplicateEvidenceError` and `EVIDENCE_ALREADY_EXISTS`.

11. Error codes are string literals.
    Resolution: Added centralized `ErrorCode` enum in `app/api/errors.py`.

12. No direct evidence lookup.
    Resolution: Added `GET /evidence/{evidence_id}`.

13. No API error response schema.
    Resolution: Added `ApiError` and `ApiErrorResponse`, and wired error response models into claim routes.

14. Datetimes are stored as strings.
    Resolution: Updated SQLAlchemy models and initial migration to use `DateTime(timezone=True)` for new databases.

15. No DB-level enum constraints.
    Resolution: Added database `CheckConstraint`s for claim type, evidence type, risk level, verification status, and trust level.

16. No server-side ID strategy.
    Resolution: Added server ID helpers and support for omitted claim/evidence IDs on create requests.

17. No README.
    Resolution: Added `README.md` with setup, validation, database, and API information.

18. No stage status document.
    Resolution: Added `docs/stage_report.md`.

## Still intentionally incomplete

- Verification results are returned but not persisted.
- The risk classifier is deterministic and conservative, but not final.
- Confidence scoring is conservative but not yet tied to a stored audit trail.
- Human review is represented as a decision, not a full workflow.
- ToolSpec compilation is not implemented.
- Runtime verification is not implemented.
- Pagination, auth, API versioning, and Postgres-specific tests remain deferred.

These are not hidden issues. They belong to later roadmap stages or production hardening.
