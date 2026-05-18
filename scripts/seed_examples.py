"""Stage 11a: Seed AgentAtlas with a demo-ready knowledge graph.

Reads pre-captured artifacts from `data/seed_artifacts/` and replays them
through the existing ingestion + orchestrator pipeline. Offline-safe and
deterministic — no live HTTP, no real MCP server spawn. Maintainers refresh
the artifacts by re-capturing from upstream when the data ages.

Usage:

    python scripts/seed_examples.py                 # additive seed
    python scripts/seed_examples.py --reset         # drop DB + alembic up + seed
    python scripts/seed_examples.py --database-url sqlite:///... --reset

The script is invoked from the repo root (sys.path is mangled to make
`app.*` imports work without installing the backend package). The
`backend/.venv` interpreter is recommended:

    backend/.venv/bin/python scripts/seed_examples.py --reset

Source distribution (the artifacts on disk → the lanes they replay through):

- `data/seed_artifacts/openapi/openai_api.json` → Stage 7c.1 OpenAPI lane
- `data/seed_artifacts/json_schema/docker_compose.json` → Stage 7c.2 JSON Schema lane
- `data/seed_artifacts/claims/headline_scenarios.json` → direct submission of
  6 hand-crafted multi-evidence claims that the demo's headline queries hit
  (`gh repo delete`, `git status`, `vercel --prod`, etc.)

The pre-captured-artifact strategy means a fresh checkout populates a
useful demo graph in under five seconds without any external dependency.
The trade-off is that the data freezes at capture time; rerun the live
ingestion lanes against upstream when their docs change materially.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

# Make `app.*` importable without installing the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

from app.db.models import Base  # noqa: E402
from app.db.session import create_database_engine, init_db  # noqa: E402
from app.schemas.claim import KnowledgeClaim  # noqa: E402
from app.schemas.enums import (  # noqa: E402
    ClaimType,
    EvidenceType,
    IngestionStatus,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence  # noqa: E402
from app.services.claim_store import ClaimStore  # noqa: E402
from app.services.evidence_trust import normalize_evidence_trust  # noqa: E402
from app.services.ids import (  # noqa: E402
    generate_claim_id,
    generate_evidence_id,
)
from app.services.json_schema_ingestion import (  # noqa: E402
    JsonSchemaIngestionService,
)
from app.services.openapi_ingestion import (  # noqa: E402
    OpenApiIngestionService,
)
from app.services.orchestrator import CanonOrchestrator  # noqa: E402


SEED_ARTIFACTS_DIR = REPO_ROOT / "data" / "seed_artifacts"


# -------- Fake HTTP clients --------
#
# Each ingestion lane takes its httpx client via dependency injection
# (`OpenApiHttpClient`, `JsonSchemaHttpClient` Protocols). The seed
# constructs a tiny one-response fake per replay that mirrors the live
# response shape: 200 status, the captured body as text, plus the
# `application/json` content-type the lane's content-type allowlist
# requires.


class _SingleResponseClient:
    """Returns one canned response, then errors on subsequent calls so a
    misbehaving service can't accidentally re-fetch."""

    def __init__(self, *, body: str, content_type: str = "application/json") -> None:
        self._body = body
        self._content_type = content_type
        self._used = False

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
        if self._used:
            raise AssertionError(
                "seed client was called twice for the same URL — the ingestion "
                "lane is supposed to be a single-request happy path."
            )
        self._used = True
        return _StubResponse(body=self._body, content_type=self._content_type)


class _StubResponse:
    """Minimal duck-typed httpx.Response substitute. The ingestion lanes
    only read `status_code`, `text`, and `headers`."""

    def __init__(self, *, body: str, content_type: str) -> None:
        self.status_code = 200
        self.text = body
        self.headers = {"content-type": content_type}


# -------- Seed steps --------


def _seed_openapi(store: ClaimStore) -> dict[str, Any]:
    spec_path = SEED_ARTIFACTS_DIR / "openapi" / "openai_api.json"
    body = spec_path.read_text()
    client = _SingleResponseClient(body=body)
    response = OpenApiIngestionService(store, client=client).ingest(
        tool_id="openai-api",
        url="https://api.openai.com/v1/openapi.json",
        submitted_by="agentatlas-seed",
    )
    return {
        "artifact": str(spec_path.relative_to(REPO_ROOT)),
        "status": response.status.value,
        "operation_count": response.operation_count,
        "claims": len(response.created_claim_ids),
    }


def _seed_json_schema(store: ClaimStore) -> dict[str, Any]:
    schema_path = SEED_ARTIFACTS_DIR / "json_schema" / "docker_compose.json"
    body = schema_path.read_text()
    client = _SingleResponseClient(body=body)
    response = JsonSchemaIngestionService(store, client=client).ingest(
        tool_id="docker",
        url="https://json.schemastore.org/docker-compose.json",
        submitted_by="agentatlas-seed",
    )
    return {
        "artifact": str(schema_path.relative_to(REPO_ROOT)),
        "status": response.status.value,
        "field_count": response.field_count,
        "claims": len(response.created_claim_ids),
    }


def _seed_headline_scenarios(store: ClaimStore) -> dict[str, Any]:
    """Submit hand-crafted multi-evidence claims for the demo's headline
    queries. Each claim ships with 2 evidence pieces so it clears the
    orchestrator's MODERATE confidence band and reaches ACCEPTED at L2."""
    claims_path = SEED_ARTIFACTS_DIR / "claims" / "headline_scenarios.json"
    raw_claims = json.loads(claims_path.read_text())
    now = datetime.now(timezone.utc)
    orchestrator = CanonOrchestrator(store)
    submitted: list[str] = []
    accepted: list[str] = []
    for entry in raw_claims:
        evidence_records = [
            normalize_evidence_trust(
                entry["tool_id"],
                Evidence(
                    evidence_id=generate_evidence_id(),
                    evidence_type=EvidenceType(ev["evidence_type"]),
                    source_uri=ev["source_uri"],
                    excerpt=ev["excerpt"],
                    hash=_evidence_hash(ev),
                    captured_at=now,
                    trust_level=TrustLevel(ev["trust_level"]),
                ),
            )
            for ev in entry["evidence"]
        ]
        claim = KnowledgeClaim(
            claim_id=generate_claim_id(),
            claim_type=ClaimType(entry["claim_type"]),
            subject=entry["subject"],
            statement=entry["statement"],
            tool_id=entry["tool_id"],
            submitted_by=entry["submitted_by"],
            evidence=evidence_records,
            risk_level=RiskLevel(entry["risk_level"]),
            created_at=now,
        )
        store.create(claim)
        submitted.append(claim.claim_id)
        result = orchestrator.verify_claim(claim)
        store.save_verification_result(result)
        if result.verification_status.value == "accepted":
            accepted.append(claim.claim_id)
    return {
        "artifact": str(claims_path.relative_to(REPO_ROOT)),
        "submitted": len(submitted),
        "accepted": len(accepted),
    }


def _evidence_hash(evidence: dict[str, Any]) -> str:
    """Deterministic sha256 of (source_uri + excerpt). The orchestrator
    requires evidence to have a hash; we compute it from the captured
    fields so identical artifacts produce identical hashes across runs."""
    payload = f"{evidence['source_uri']}::{evidence['excerpt']}".encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


# -------- CLI entry --------


def _reset_database(database_url: str) -> None:
    """Drop every table and rebuild it via alembic. Used by `--reset`.

    SQLAlchemy's `metadata.drop_all` covers every table the ORM knows
    about; alembic then re-applies its migrations to recreate them with
    every CHECK constraint and column the codebase has accumulated. We
    drop the `alembic_version` table separately because metadata doesn't
    own it."""
    from sqlalchemy import text

    engine = create_database_engine(database_url)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    # Use alembic programmatically so the seed script works without
    # requiring the maintainer to run `alembic upgrade head` separately.
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_cfg, "head")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed AgentAtlas with demo data.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "AGENTATLAS_DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'agentatlas.db'}"
        ),
        help="SQLAlchemy database URL (defaults to env or backend/agentatlas.db).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop the database and re-run all alembic migrations before seeding.",
    )
    args = parser.parse_args(argv)

    print(f"AgentAtlas seed → {args.database_url}")
    if args.reset:
        print("  --reset: dropping tables and re-applying migrations...")
        _reset_database(args.database_url)
    else:
        # Ensure schema exists even on an additive run.
        engine = create_database_engine(args.database_url)
        init_db(engine)

    store = ClaimStore(database_url=args.database_url)

    # Warn loudly if the DB already has data and the maintainer forgot
    # `--reset`. Re-seeding additively produces duplicate claims (each one
    # gets a fresh uuid claim_id, so the dedup at the orchestrator layer
    # treats them as new) and silently breaks acceptance of the headline
    # scenarios (the orchestrator marks duplicates as PENDING/L1).
    # Surfacing this here is cheaper than tracking down a corrupted demo
    # state later.
    existing = store.list(limit=1)
    if existing:
        print(
            "  WARNING: the database already contains claims and `--reset`\n"
            "  was not passed. Re-seeding additively will produce duplicate\n"
            "  rows and may break the headline-scenario verdicts. Pass\n"
            "  `--reset` to drop and rebuild for a clean demo state.\n"
        )

    summary: dict[str, Any] = {}
    print("  loading OpenAPI (openai-api)...")
    summary["openapi"] = _seed_openapi(store)
    print(
        f"    {summary['openapi']['status']} — "
        f"{summary['openapi']['operation_count']} operations, "
        f"{summary['openapi']['claims']} claims"
    )

    print("  loading JSON Schema (docker compose)...")
    summary["json_schema"] = _seed_json_schema(store)
    print(
        f"    {summary['json_schema']['status']} — "
        f"{summary['json_schema']['field_count']} fields, "
        f"{summary['json_schema']['claims']} claims"
    )

    print("  loading headline scenarios (multi-evidence claims)...")
    summary["headline"] = _seed_headline_scenarios(store)
    print(
        f"    submitted={summary['headline']['submitted']}, "
        f"accepted={summary['headline']['accepted']}"
    )

    total_claims = (
        summary["openapi"]["claims"]
        + summary["json_schema"]["claims"]
        + summary["headline"]["submitted"]
    )
    print(f"\n  total claims persisted: {total_claims}")
    print(
        "  validate-command demo target: "
        "`gh repo delete my-org/x --yes` → critical / blocked"
    )
    # Sanity: all ingestion responses must report COMPLETED, else the seed
    # produced an inconsistent state and we want the caller to know.
    for lane in ("openapi", "json_schema"):
        if summary[lane]["status"] != IngestionStatus.COMPLETED.value:
            print(
                f"  WARNING: {lane} lane finished with status={summary[lane]['status']}; "
                "the seed graph may be incomplete."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
