"""Entry point: `python -m app.mcp_server`.

Wires the production `McpServer` and runs the blocking stdio loop. Used by
Claude Desktop / Cursor when Ayiru is registered as an MCP server in
their config file.
"""

from app.mcp_server.server import build_default_server


def main() -> None:
    build_default_server().serve()


if __name__ == "__main__":
    main()
