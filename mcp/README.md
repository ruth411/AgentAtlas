# ayiru-mcp

**Machine-readable external knowledge for AI agents — Model Context Protocol server.**

Your coding agent calls 7 typed MCP tools and gets back typed records:
`subject_id`, `capability_type`, `argv_schema`, `flag_schema`, `effect_kind`,
`verification_level`. No prose, no webpage surfing, no hallucinated flags.
Ships with a bundled structured catalog covering **28 tool families**,
3,237 subjects, 32,733 typed capabilities, 3,988 typed constraints, and
3,084 typed effects. No server, no database to set up, no API key.

## v1 Contract

The general-release target for `ayiru-mcp` is a small, stable, read-only
surface. The visible MCP contract is the seven structured tools below:

- `resolve_subject`
- `get_subject_spec`
- `get_capabilities`
- `get_constraints`
- `get_effects`
- `resolve_action`
- `get_workflow_plan`

Legacy prose tools stay registered only for back-compat and remain hidden from
`tools/list`. Fresh MCP hosts should discover only the typed structured tools.
The behavior-level contract is documented in
[`docs/mcp_v1_contract.md`](../docs/mcp_v1_contract.md).

## Compatibility

| Host | Status | Notes |
|---|---|---|
| Claude Desktop | Supported | Primary local-first target; uses stdio JSON-RPC with no extra config beyond `command: "ayiru-mcp"` |
| Cursor | Supported | Same stdio MCP shape as Claude Desktop |
| Cline | Supported | Same stdio MCP shape; configure under the extension's MCP settings |

Ayiru only promises the structured seven-tool surface above across those hosts.
If a host depends on hidden legacy tools, treat that as a back-compat path, not
as the product contract.

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

The bundled wheel ships the current structured Ayiru catalog, not a
single-family demo slice. The checked-in bundle currently includes 28
families:

`ansible`, `awk`, `brew`, `cargo`, `curl`, `docker`, `ffmpeg`, `gh`,
`git`, `go`, `helm`, `jq`, `kubectl`, `magick`, `openssl`, `pip`, `pnpm`,
`poetry`, `psql`, `rsync`, `rustc`, `sed`, `sqlite3`, `ssh`, `supabase`,
`terraform`, `vercel`, `vim`.

At the time of this README revision the bundled row counts are:

| Table | Rows | What's in it |
|---|---|---|
| `subjects` | 3,237 | One row per structured subject in the bundled families |
| `capabilities` | 32,733 | Typed `invocation` / `configuration` / `metadata` rows with `argv_schema` + `flag_schema` |
| `constraints` | 3,988 | Typed auth-scope and environment-precondition rows |
| `effects` | 3,084 | Typed `destructive` / `mutates_remote_state` / `reversible` booleans |

Total wheel size: ~6 MB (bundled SQLite catalog with pre-computed embeddings).
Each row carries its own `verification_level`: the flag/argv `capabilities`
are `L3_runtime_verified` (the parser ran the binary), while the inferred
`effects` and text-derived `constraints` are `L2_source_verified` — the
safety classification is grounded in the help text but not asserted by an
experiment, and the catalog says so rather than over-claiming L3.

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

The bundled catalog currently ships no published workflow specs, so
`get_workflow_plan` is part of the stable public surface but may honestly
return zero plans until workflow data is populated.

The catalog is read-only — there's no write surface on the bundled wheel
because `site-packages` isn't user-writable on most systems. Self-hosters
who want the writeable `submit_claim` surface should use the FastAPI
backend instead.

## Release Check

From a checkout, run:

```bash
make mcp-release-check
```

That runs the MCP contract tests, bundled-catalog tests, structured product
rebuild + smoke, a fresh-wheel-install smoke for `ayiru-core` +
`ayiru-mcp` against a temporary rebuilt bundle, and the Python client
integration suite against the current repo state. It requires PyPI access
to resolve third-party runtime wheels.

## License

MIT. Same as the rest of Ayiru.
