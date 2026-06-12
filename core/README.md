# ayiru-core

Shared library for the Ayiru ecosystem. Contains the typed schemas, the
SQLAlchemy claim store, the deterministic risk classifier, and the
`QueryEngine` that powers `ask()`. No FastAPI, no alembic, no ingestion.

Used by:

- `ayiru` — the full FastAPI backend (REST API, ingestion lanes, CLI).
- `ayiru-mcp` — the slim stdio MCP server bundled with a pre-built catalog.

This package isn't useful on its own; install one of the consumers above.
