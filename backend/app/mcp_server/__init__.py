"""Stage 10: Ayiru MCP server.

Exposes the Stage 9 query surface (plus claim submission) as MCP tools over
stdio JSON-RPC. Clients are agent frameworks (Claude Desktop, Cursor, Cline,
etc.) that speak the Model Context Protocol natively.

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

If MCP protocol nuances ever bite us (or the SDK gains features we want),
the tool implementations don't change — only the transport wrapper does.
"""

__all__ = []
