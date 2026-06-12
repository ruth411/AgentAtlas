"""ayiru-mcp — stdio MCP server bundled with a verified knowledge catalog.

`ayiru-mcp` is the install-and-go form of Ayiru. It bundles a pre-built
SQLite catalog inside the wheel; `pip install ayiru-mcp` followed by
adding the server to a Claude Desktop / Cursor / Cline config is all a
user has to do to get cited, verified answers for `gh` (the MVP catalog)
without running a server.

The query engine, schemas, and risk classifier live in `ayiru-core`. This
package only ships the MCP protocol wrapper plus the bundled DB.
"""
