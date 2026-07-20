# Ayiru MCP v1 Contract

Ayiru's public MCP contract is a small, read-only stdio server intended to
feel normal in Claude Desktop, Cursor, and Cline.

## Public Surface

`tools/list` must advertise exactly these seven tools, in this order:

1. `resolve_subject`
2. `get_subject_spec`
3. `get_capabilities`
4. `get_constraints`
5. `get_effects`
6. `resolve_action`
7. `get_workflow_plan`

All seven are read-only. They must declare MCP tool annotations with
`readOnlyHint: true` and return JSON-object `structuredContent`.

## Tool Roles

- `resolve_subject`: first-step discovery from a fuzzy user hint to stable `subject_id`s.
- `get_subject_spec`: full typed record for a chosen `subject_id`.
- `get_capabilities`: structured command, flag, argument, config, and metadata rows.
- `get_constraints`: prerequisites and gating conditions before execution.
- `get_effects`: typed change/risk profile for the subject or action.
- `resolve_action`: one-shot grounding for a concrete intended action.
- `get_workflow_plan`: published multi-step plans for a goal, when available.

## Stability Rules

- Tool names above are the v1 contract and must not change casually.
- Hidden legacy tools are compatibility-only and are not part of the public v1 surface.
- Public tools must forbid unexpected input fields with `additionalProperties: false`.
- Public tools may honestly return empty matches or plans; empty results are valid contract behavior, not protocol failure.

## Hidden Compatibility Tools

These may remain registered for pinned callers, but they must stay hidden from
`tools/list` in the bundled `ayiru-mcp` distribution:

- `ask`
- `validate_command`
- `search_tools`
- `explain_risk`
- `get_safe_workflow`
- `get_tool_spec`
- `submit_claim`

## Current Honest Residual

The bundled catalog currently ships no published `canonical_workflow_specs`, so
`get_workflow_plan` is part of the stable public surface but may return zero
plans for every goal. That is expected until workflow data is populated.
