"""Stage 10: Ayiru MCP server (back-compat shim).

The implementation now lives in `ayiru_mcp._internal` (shipped with the
`ayiru-mcp` wheel). This package re-exports the same surface so callers
that still spell things `app.mcp_server.*` keep working: the CLI dev
path (`backend/app/cli.py` → `python -m app.mcp_server`), the existing
test suite, and any third-party config that registers the legacy entry
point.

Why we roll our own MCP server instead of using the official `mcp` Python
SDK:

- The codebase is sync-throughout (FastAPI sync routes, sync ClaimStore,
  sync QueryEngine). The SDK is asyncio-based and would force us to bridge
  every tool call across the sync/async boundary for no real benefit.
- We already implemented an MCP *client* in Stage 7d (`mcp_ingestion`'s
  stdio JSON-RPC client). The server is the inverse direction over the
  same protocol — most of the framing concerns are already understood.
- We control the test surface: every tool is a plain function we can
  unit-test in-process; the subprocess integration test is one focused
  smoke check, not 30 fragile end-to-end tests.
"""

__all__: list[str] = []
