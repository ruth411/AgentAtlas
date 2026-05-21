"""Stage 14: in-package seed runner.

This is the wheel-friendly successor to `scripts/seed_examples.py`. The
script at `scripts/seed_examples.py` now imports `main` from here and
delegates; the CLI's `ayiru seed` calls into this module directly.

Artifact lookup mirrors `contract_paths.contract_path`: source-tree
first (`<repo>/data/seed_artifacts/...`) so dev edits are visible
immediately, falling back to the bundled copy at
`app/seed_data/artifacts/...` for pip installs.

The reset path also auto-detects `alembic.ini`: source-tree
`<repo>/backend/alembic.ini` first, then the env-var override
`AYIRU_ALEMBIC_INI`. Both modes work; from a clean PyPI install
the user passes `AYIRU_ALEMBIC_INI` (or the CLI's `migrate`
subcommand is used instead).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from app.db.models import Base
from app.db.session import create_database_engine, init_db
from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    IngestionStatus,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.services.canonical_compiler import (
    CanonicalPublicationError,
    ToolSpecCompiler,
)
from app.services.claim_store import ClaimStore
from app.services.evidence_trust import normalize_evidence_trust
from app.services.ids import generate_claim_id, generate_evidence_id
from app.services.json_schema_ingestion import JsonSchemaIngestionService
from app.services.openapi_ingestion import OpenApiIngestionService
from app.services.orchestrator import CanonOrchestrator


# Resolved once per process — repo-root `data/seed_artifacts/` if running
# from a checkout, `None` if installed from wheel (the fallback path
# uses importlib.resources to read directly from the bundled package).
_SOURCE_TREE_ARTIFACTS: Path | None = (
    Path(__file__).resolve().parents[3] / "data" / "seed_artifacts"
)
if _SOURCE_TREE_ARTIFACTS is not None and not _SOURCE_TREE_ARTIFACTS.is_dir():
    _SOURCE_TREE_ARTIFACTS = None


def _artifact_bytes(*parts: str) -> bytes:
    """Return the raw bytes of one bundled / source-tree seed artifact."""
    if _SOURCE_TREE_ARTIFACTS is not None:
        src = _SOURCE_TREE_ARTIFACTS.joinpath(*parts)
        if src.is_file():
            return src.read_bytes()
    bundled = resources.files("app.seed_data").joinpath("artifacts", *parts)
    return bundled.read_bytes()


# -------- Fake HTTP clients --------


class _SingleResponseClient:
    """Single-shot client used by the offline-safe replay path."""

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
    def __init__(self, *, body: str, content_type: str) -> None:
        self.status_code = 200
        self.text = body
        self.headers = {"content-type": content_type}


# -------- Seed steps --------


def _seed_openapi(store: ClaimStore) -> dict[str, Any]:
    body = _artifact_bytes("openapi", "openai_api.json").decode("utf-8")
    client = _SingleResponseClient(body=body)
    response = OpenApiIngestionService(store, client=client).ingest(
        tool_id="openai-api",
        url="https://api.openai.com/v1/openapi.json",
        submitted_by="ayiru-seed",
    )
    return {
        "artifact": "openapi/openai_api.json",
        "status": response.status.value,
        "operation_count": response.operation_count,
        "claims": len(response.created_claim_ids),
    }


def _seed_json_schema(store: ClaimStore) -> dict[str, Any]:
    body = _artifact_bytes("json_schema", "docker_compose.json").decode("utf-8")
    client = _SingleResponseClient(body=body)
    response = JsonSchemaIngestionService(store, client=client).ingest(
        tool_id="docker",
        url="https://json.schemastore.org/docker-compose.json",
        submitted_by="ayiru-seed",
    )
    return {
        "artifact": "json_schema/docker_compose.json",
        "status": response.status.value,
        "field_count": response.field_count,
        "claims": len(response.created_claim_ids),
    }


def _seed_headline_scenarios(store: ClaimStore) -> dict[str, Any]:
    raw_claims = json.loads(
        _artifact_bytes("claims", "headline_scenarios.json").decode("utf-8")
    )
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
        store.save_verification_result(result, actor="ayiru-seed")
        if result.verification_status.value == "accepted":
            accepted.append(claim.claim_id)
    return {
        "artifact": "claims/headline_scenarios.json",
        "submitted": len(submitted),
        "accepted": len(accepted),
    }


def _publish_canonical_specs(store: ClaimStore) -> dict[str, Any]:
    """Compile and persist a canonical ToolSpec for every seeded tool
    that has at least one accepted claim. Tools with no accepted claims
    are reported as skipped so the seed output stays honest — agents
    querying `get_tool_spec` for them will still see a default-deny 404.

    This is the step that makes the Stage 9 agent query surface
    (`search_tools`, `get_tool_spec`, `canonical/tools`) return data on a
    fresh seed. Without it, the orchestrator accepts claims but no
    canonical specs ever land in the graph and four of the six MCP query
    tools return empty results.
    """
    compiler = ToolSpecCompiler(store)
    tool_ids = sorted({claim.tool_id for claim in store.list(limit=10_000)})
    published: list[str] = []
    skipped: dict[str, str] = {}
    for tool_id in tool_ids:
        try:
            compiled = compiler.compile(tool_id)
        except CanonicalPublicationError as exc:
            skipped[tool_id] = str(exc)
            continue
        store.save_canonical_tool_spec(compiled.spec)
        published.append(tool_id)
    return {"published": published, "skipped": skipped}


def _evidence_hash(evidence: dict[str, Any]) -> str:
    payload = f"{evidence['source_uri']}::{evidence['excerpt']}".encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


# -------- Reset + main --------


def _reset_database(database_url: str) -> None:
    from sqlalchemy import text

    engine = create_database_engine(database_url)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    from alembic import command

    from app.services.alembic_config import make_alembic_config

    prior_cwd = os.getcwd()
    try:
        cfg = make_alembic_config(database_url)
        command.upgrade(cfg, "head")
    finally:
        os.chdir(prior_cwd)


def _default_database_url() -> str:
    override = os.environ.get("AYIRU_DATABASE_URL")
    if override:
        return override
    # If running from a checkout, default to backend/ayiru.db; the
    # SessionLocal default points there too, so behaviour matches.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").is_file():
            return f"sqlite:///{parent / 'ayiru.db'}"
        if (parent / ".git").exists():
            break
    return "sqlite:///./ayiru.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Ayiru with demo data.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL (defaults to $AYIRU_DATABASE_URL or backend/ayiru.db).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop the database and re-run all alembic migrations before seeding.",
    )
    args = parser.parse_args(argv)
    database_url = args.database_url or _default_database_url()

    print(f"Ayiru seed → {database_url}")
    if args.reset:
        print("  --reset: dropping tables and re-applying migrations...")
        _reset_database(database_url)
    else:
        engine = create_database_engine(database_url)
        init_db(engine)

    store = ClaimStore(database_url=database_url)
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

    print("  publishing canonical ToolSpecs from accepted claims...")
    summary["canonical"] = _publish_canonical_specs(store)
    print(
        f"    published={len(summary['canonical']['published'])} "
        f"({', '.join(summary['canonical']['published']) or '—'})"
    )
    for tool_id, reason in summary["canonical"]["skipped"].items():
        print(f"    skipped {tool_id}: {reason}")

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
    for lane in ("openapi", "json_schema"):
        if summary[lane]["status"] != IngestionStatus.COMPLETED.value:
            print(
                f"  WARNING: {lane} lane finished with status={summary[lane]['status']}; "
                "the seed graph may be incomplete."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
