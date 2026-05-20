# Ayiru — Backend Package

> This is the installable Python package for [Ayiru](https://github.com/ruth411/ayiru), a verified, machine-readable knowledge layer for AI agents.

After `pip install ayiru`, a single `ayiru` binary is on PATH:

```bash
ayiru seed --reset       # populate a demo graph
ayiru serve              # run the FastAPI app
ayiru mcp                # speak MCP/JSON-RPC over stdio (for Claude Desktop, Cursor, …)
ayiru query --tool github-cli --command 'gh repo delete x --yes'
ayiru tools              # list every published ToolSpec
ayiru verify --claim-id claim_abc  # promote L2 → L3 via runtime check
ayiru migrate            # alembic upgrade head
ayiru --version
```

The full project README — including architecture, demo scenarios, MCP
integration setup, and contribution guidelines — lives at the repo root:
<https://github.com/ruth411/ayiru/blob/main/README.md>.

## What ships in this wheel

- `app.api` — FastAPI routes (claims, canonical, ingestion, verification, query)
- `app.mcp_server` — hand-rolled stdio JSON-RPC MCP server (6 tools)
- `app.schemas` — Pydantic v2 typed models for every domain entity
- `app.services` — orchestrator, risk engine, ingestion lanes, runtime verifier, query engine
- `app.db` — SQLAlchemy 2.0 models + session
- `app.cli` — the `ayiru` console script
- Alembic migrations under `alembic/`

The seed data (`data/seed_artifacts/`), versioned trust contracts
(`contracts/`), demo dashboard (`frontend/`), and operator scripts
(`scripts/`) live in the parent repo, not in the wheel. Clone the repo
to run the seed script or build the dashboard.

## License

MIT — see the repo root for details.
