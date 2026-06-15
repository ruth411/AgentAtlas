# ayiru-mcp

**Machine-readable external knowledge for AI agents — Model Context Protocol server.**

Your coding agent calls 7 typed MCP tools and gets back typed records:
`subject_id`, `capability_type`, `argv_schema`, `flag_schema`, `effect_kind`,
`verification_level`. No prose, no webpage surfing, no hallucinated flags.
Ships with a structured-first **`gh` catalog** parsed from real
`gh ... --help` output — 74 subjects, 779 typed capabilities, 82 typed
constraints, 74 typed effects. No server, no database to set up, no API key.

## Install

```bash
pip install ayiru-mcp
```

That gives you the `ayiru-mcp` console script. Add it to your MCP client's
config:

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) / `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "ayiru": { "command": "ayiru-mcp" }
  }
}
```

Restart Claude Desktop. The seven structured Ayiru tools (`resolve_subject`,
`get_subject_spec`, `get_capabilities`, `get_constraints`, `get_effects`,
`resolve_action`, `get_workflow_plan`) appear in the tool list.

### Cursor

Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ayiru": { "command": "ayiru-mcp" }
  }
}
```

### Cline

Edit `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
(macOS) or your platform's equivalent:

```json
{
  "mcpServers": {
    "ayiru": { "command": "ayiru-mcp" }
  }
}
```

## Live example

Ask the agent: *"what flags does `gh pr create` take?"*

It calls `resolve_subject` then `get_capabilities` and gets back:

```jsonc
// tools/call get_capabilities {"subject_id":"gh-pr-create","limit":1}
{
  "subject_id": "gh-pr-create",
  "capabilities": [{
    "capability_id": "gh-pr-create#invocation",
    "capability_type": "invocation",
    "source": "structured",
    "title": "gh pr create invocation",
    "verification_level": "L3_runtime_verified",
    "detail": {
      "command": "gh pr create",
      "source_url": "https://cli.github.com/manual/gh_pr_create",
      "argv_schema": [],
      "flag_schema": [
        {"name":"--assignee","short":"-a","value_type":"string","value_name":"login",
         "takes_value":true,"required":false,"deprecated":false,
         "description":"Assign people by their login. Use \"@me\" to self-assign."},
        {"name":"--base","short":"-B","value_type":"string","value_name":"branch",
         "takes_value":true,"required":false,"deprecated":false,
         "description":"The branch into which you want your code merged"},
        /* …20 more typed flags… */
      ]
    }
  }],
  "total": 25
}
```

`L3_runtime_verified` means we actually spawned `gh pr create --help` and
parsed the output. Every flag has `value_type`, `takes_value`, `required`,
`deprecated`, and `description` populated. The agent constructs the command
from typed metadata; there's no prose for it to misread.

## What's in the catalog

The bundled wheel ships **structured `gh` only** — every `gh` subcommand
parsed from real `--help` output into typed rows:

| Table | Rows | What's in it |
|---|---|---|
| `subjects` | 74 | One row per `gh` subcommand (`gh-pr-create`, `gh-repo-delete`, …) including the destructive `delete` / `remove` / `archive` leaves |
| `capabilities` | 779 | Typed `invocation` / `configuration` / `metadata` rows with `argv_schema` + `flag_schema` |
| `constraints` | 82 | Typed auth-scope and environment-precondition rows |
| `effects` | 74 | Typed `destructive` / `mutates_remote_state` / `reversible` booleans |

Total wheel size: ~2 MB (bundled SQLite catalog with pre-computed embeddings).
Each row carries its own `verification_level`: the flag/argv `capabilities`
are `L3_runtime_verified` (the parser ran the binary), while the inferred
`effects` and text-derived `constraints` are `L2_source_verified` — the
safety classification is grounded in the help text but not asserted by an
experiment, and the catalog says so rather than over-claiming L3.

The wider catalog (38 tool families, prose-projection fallback for the 37
without structured ingestion yet) lives in the FastAPI backend — see the
main repo's [README](https://github.com/ruth411/ayiru) for the self-hosted
path.

## Optional: semantic re-rank

```bash
pip install ayiru-mcp[semantic]
```

Pulls `fastembed` (~130 MB ONNX model on first use) and enables semantic
re-ranking of search results. Without this extra the server runs in pure
lexical mode — already useful, but query phrasings that don't share
tokens with the catalog rank worse.

## Tools

The server advertises seven read-only structured tools via `tools/list`:

| Tool | Returns |
|---|---|
| `resolve_subject` | Typed `SubjectSummary` records from a fuzzy hint. **Call this first.** |
| `get_subject_spec` | Full `SubjectSpec` for a known `subject_id` |
| `get_capabilities` | Typed `CapabilityRecord` rows — invocations, configs, constraints, effects |
| `get_constraints` | Typed constraint records — auth scopes, env preconditions, deprecation |
| `get_effects` | Typed effect profile — `destructive` / `mutates_remote_state` / `reversible` booleans |
| `resolve_action` | End-to-end grounding — top capability + constraints + effects + risk verdict |
| `get_workflow_plan` | Goal-matched workflow plans, safest-first |

The legacy prose surfaces (`ask`, `validate_command`, `search_tools`,
`explain_risk`, `get_safe_workflow`, `get_tool_spec`) remain registered for
backward compatibility but are hidden from `tools/list`. Pinned external
callers that invoke them by name still work; fresh tool discovery only
shows the typed surfaces above.

The catalog is read-only — there's no write surface on the bundled wheel
because `site-packages` isn't user-writable on most systems. Self-hosters
who want the writeable `submit_claim` surface should use the FastAPI
backend instead.

## License

MIT. Same as the rest of Ayiru.
