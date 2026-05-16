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

## What This Is Not Yet

- Not a complete production verification engine
- Not a runtime sandbox
- Not an MCP server
- Not a dashboard

Those belong to later roadmap stages.
