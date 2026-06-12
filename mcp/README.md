# ayiru-mcp

**Verified, cited knowledge for AI agents — Model Context Protocol server.**

Your coding agent asks a question. `ayiru-mcp` returns a cited answer from
official docs, not a guess from training data months out of date. Ships
with a pre-built **`gh` (GitHub CLI) catalog** inside the wheel — no
server, no database to set up, no API key.

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

Restart Claude Desktop. The six Ayiru tools (`ask`, `validate_command`,
`get_tool_spec`, `search_tools`, `explain_risk`, `get_safe_workflow`)
appear in the tool list.

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

Ask the agent: *"how do I open a pull request from my current branch?"*

It calls `ask()` and gets back:

```text
Subject:      gh recipe: open a pull request from current branch
Statement:    Quick: `gh pr create --fill` (auto-fills title + body from commit
              messages). Full control: `gh pr create --title 'feat: add X'
              --body 'closes #42' --base main --reviewer alice,bob --label
              feature`. Draft: add `--draft`. Web (open in browser to finish):
              `gh pr create --web`. From a fork: gh detects upstream
              automatically.
Citation:     https://cli.github.com/manual/gh_pr_create
Confidence:   0.84  (high band)
Verification: L1_schema_valid
```

No hallucinated flag. No paraphrased blog post. The current canonical docs
sentence, with the source URL, a confidence score, and a verification
level — agents can decide how strict to be on a per-claim basis.

## What's in the catalog

The bundled wheel ships **`gh` only** — 129 claims across the five
GitHub-CLI surfaces:

| Surface | Claims | Examples |
|---|---|---|
| `gh-cli` | 41 | per-command pages from `cli.github.com/manual` |
| `gh-recipes` | 36 | real workflows (`gh pr create --fill`, `gh repo fork --clone`) |
| `gh-errors` | 30 | error messages with diagnoses + fixes |
| `gh-workflows` | 14 | Actions + workflow-dispatch patterns |
| `gh-config` | 8 | config/env knobs |

Total wheel size: ~2 MB (including the bundled SQLite catalog with
pre-computed embeddings).

The full multi-tool catalog (60+ tools, 2,800+ claims across `git`,
`docker`, `kubectl`, `ffmpeg`, …) lives in the FastAPI backend — see the
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

The server advertises six read-only tools via `tools/list`:

| Tool | Description |
|---|---|
| `ask` | Natural-language question → ranked, cited answers. The headline surface. |
| `validate_command` | Advisory risk verdict for a literal command string. *Not* a security boundary against an adversarial agent — useful as a second opinion. |
| `get_tool_spec` | Full canonical spec for a tool (currently empty for `gh` — no specs published yet). |
| `search_tools` | Search across known tools by id / capability. |
| `explain_risk` | Deterministic risk classification with reasons + dimensions. |
| `get_safe_workflow` | Goal-matched workflows, safest-first. |

The catalog is read-only — there's no write surface on the bundled wheel
because `site-packages` isn't user-writable on most systems. Self-hosters
who want the writeable `submit_claim` surface should use the FastAPI
backend instead.

## License

MIT. Same as the rest of Ayiru.
