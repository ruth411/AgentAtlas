# MCP Release Checklist

Release target: `ayiru-mcp` as a zero-setup, bundled, read-only MCP server.

## Frozen v1 Contract

Visible tools in `tools/list` must be exactly:

- `resolve_subject`
- `get_subject_spec`
- `get_capabilities`
- `get_constraints`
- `get_effects`
- `resolve_action`
- `get_workflow_plan`

Registered but hidden back-compat tools:

- `ask`
- `validate_command`
- `search_tools`
- `explain_risk`
- `get_safe_workflow`
- `get_tool_spec`
- `submit_claim`

## Host Matrix

| Host | Required check | Status for release |
|---|---|---|
| Claude Desktop | Install, discover tools, execute one happy-path structured call | Required |
| Cursor | Install, discover tools, execute one happy-path structured call | Required |
| Cline | Install, discover tools, execute one happy-path structured call | Required |

## Bundled Catalog Gates

- Bundled catalog file exists at `mcp/ayiru_mcp/data/catalog.db`
- Structured tables are non-empty
- Visible demo subjects resolve from the bundled catalog
- Typed capability records stay `source="structured"`
- Destructive and mutating surfaces carry typed effects
- Version metadata matches package metadata

## Repo Verification

Run:

```bash
make mcp-release-check
```

This must complete cleanly before a release candidate or GA tag.
It rebuilds the bundled catalog into `/tmp/ayiru-mcp-release-catalog.db`,
then runs a fresh-wheel-install smoke for `ayiru-core` and `ayiru-mcp`
against that temporary bundle.

## Release Candidate Checks

- Fresh venv install path is exercised manually
- Fresh-wheel install smoke resolves third-party runtime wheels cleanly
- `ayiru-mcp` starts cleanly on stdin EOF
- `initialize` returns the expected protocol version and package version
- `tools/list` advertises only the seven structured tools
- One end-to-end structured query succeeds in each supported host
- Known catalog gaps are documented in `mcp/README.md`

## GA Exit Criteria

- Structured seven-tool surface is stable
- Bundled catalog quality gates are green
- Supported-host checks are complete
- README and release notes reflect the exact shipped catalog scope
- No hidden dependency on repo-only paths remains in the MCP wheel path
- Release automation does not rewrite tracked DB artifacts in place
