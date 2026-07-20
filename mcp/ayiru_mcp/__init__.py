"""ayiru-mcp — stdio MCP server bundled with Ayiru's machine-readable catalog.

`ayiru-mcp` is the install-and-go form of Ayiru. It bundles a pre-built
SQLite catalog inside the wheel; `pip install ayiru-mcp` followed by
adding the server to a Claude Desktop / Cursor / Cline config is all a
user has to do to get cited external knowledge from the bundled
structured catalog, with accepted answers preferred and review-pending
answers explicitly marked, without running a server.

The query engine, schemas, and risk classifier live in `ayiru-core`. This
package only ships the MCP protocol wrapper plus the bundled DB.
"""
