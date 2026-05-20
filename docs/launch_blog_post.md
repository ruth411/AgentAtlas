# Launch post — Ayiru v0.1.0

> **Draft for:** personal blog / dev.to / Substack on launch day.
>
> **Pairs with:** the Show HN title `"Show HN: Ayiru — verified safety verdicts for AI agents running shell commands"` and a Twitter/X thread that opens with the headline GIF.
>
> **Read this aloud before publishing.** If a sentence sounds like a press release, cut it. The HN audience reads in 90 seconds and bounces if they smell marketing.

---

## Ayiru: the safety arbiter that says "no" before your AI agent runs `rm -rf /`

If you've watched an AI agent run shell commands for more than ten minutes, you've watched it make a confident, well-reasoned, totally wrong decision. The model "thought" `gh repo delete my-org/production-critical --yes` was the right next step. It wasn't. You found out after.

I built **Ayiru** to be the layer the agent calls *before* it does the thing.

```python
verdict = atlas.validate_command(
    tool_id="github-cli",
    command="gh repo delete my-org/production-critical --yes",
)
# {
#   "safe_to_auto_execute": false,
#   "risk_level": "critical",
#   "requires_human_confirmation": true,
#   "verification_level": "L2_source_verified",
#   "confidence": 0.92,
#   "reasons": [
#     "Deleting a GitHub repository is an irreversible remote mutation.",
#     "Safety policy blocks auto-execution at risk level 'critical'."
#   ],
#   "evidence": [{
#     "evidence_type": "official_docs",
#     "source_uri": "https://docs.github.com/en/manual/gh_repo_delete",
#     "trust_level": "high"
#   }]
# }
```

That verdict is deterministic. There is no LLM in the safety path. The risk classification comes from a versioned JSON contract; the citation traces back to the byte of the docs page that grounded it; the verification level only goes up when evidence justifies it.

## Why this needed to exist

The two ways an agent currently decides what's safe:

1. **Guess from training data.** Outdated commands, hallucinated flags, fabricated behaviour. Confident wrong answers are the worst kind.
2. **Read scraped docs at runtime.** Slow, expensive in tokens, and the docs themselves can host prompt-injection attacks dressed as instructions.

Neither is good enough for an agent that's allowed to touch your filesystem or your billing account. There needed to be a structured layer between "the LLM thought about it" and "the shell ran it."

## How it works (the 90-second version)

Six ingestion lanes pull evidence from trusted sources — CLI `--help` output, official docs over HTTPS-with-SSRF-guards, OpenAPI specs, JSON Schemas, GraphQL SDL, MCP server `tools/list`. A deterministic orchestrator validates each claim's schema, classifies its risk, scores its confidence, deduplicates, detects conflicts. Accepted claims compile into canonical `ToolSpec` and `WorkflowSpec` records with byte-level provenance. A runtime sandbox can promote claims to `L3_runtime_verified` by safely running things like `git --version`.

When your agent asks "is this command safe?", Ayiru:

1. Matches the command against accepted claims using strict exact + prefix matching.
2. Re-classifies the risk independently (defends against understated-risk attacks).
3. Gates auto-execution behind **three** independent conditions: safety policy permits the risk level **AND** confidence ≥ 0.70 **AND** verification level ≥ L2. Not two-out-of-three.
4. Returns the verdict with full citation trail.

If anything's missing, the verdict is `safe_to_auto_execute=false` with a structured reason. Default-deny on no match. The matcher won't make things up.

## What's in the box

- **`POST /v1/query/validate-command`** — the headline endpoint.
- **`POST /v1/verification/human-review`** — file APPROVED / REJECTED / NEEDS_CHANGES decisions against claims. APPROVED + L3-or-better promotes to `L5_human_audited`.
- **Append-only audit log** — every claim submission, verification result, review decision, and spec publication writes one immutable row. No `update` or `delete` method exists on the store. A test fails the build if anyone tries to add one.
- **Outbound MCP server** — same six tools as the HTTP surface, exposed over stdio JSON-RPC. Drop the config block into Claude Desktop / Cursor / Cline / Continue and the agent can ask Ayiru about safety inline.
- **CLI** — `ayiru serve` / `ayiru mcp` / `ayiru migrate` / `ayiru query` / `ayiru verify` / `ayiru tools` / `ayiru seed`. One binary on PATH after `pip install`.
- **Minimal Next.js dashboard** for visual exploration.

693 backend tests passing, ruff clean. Wheel-bundled contracts + seed + migrations — `pip install ayiru` works in a clean venv with zero checkout.

## What's *not* in v0.1

Being honest about the gaps:

- Tool coverage is curated: 5 native tools (git, gh, docker, vercel-cli, openai-api) + 5 MCP servers. Adding a tool is a contract change with a real review process, not a code change. The roadmap calls this a feature, not a bug — every tool that gets verdicts has been thought about.
- SQLite is the only tested backend. Postgres compiles cleanly offline; the live test matrix is the v0.2 work.
- No `ask` (retrieval) endpoint yet. That's the [v0.2 pivot](./../roadmap_v0.2.md) — gated behind a measurement spike because I refuse to build it without checking that the underlying premise is true.
- API-key auth is single-tenant. OAuth / SSO / per-key rate limiting is v0.2.

## Try it

```bash
pip install ayiru
ayiru migrate
ayiru serve
```

Or, if you'd rather see the safety verdict before installing anything:

```bash
curl -X POST http://localhost:8000/v1/query/validate-command \
  -H 'Content-Type: application/json' \
  -d '{"tool_id": "github-cli", "command": "gh repo delete my-org/x --yes"}'
```

Repo: <https://github.com/ruth411/ayiru>
Docs (after `ayiru serve`): <http://localhost:8000/docs>

## What I want from you

Three things:

1. **Try it against a real agent.** Wire the MCP server into Claude Desktop or Cursor. Run a destructive command. Tell me what happened.
2. **File an issue.** Bug, missing tool, weird verdict, surprising behaviour. The templates are ready. I'll respond within 48 hours.
3. **Tell me if the safety story holds up under attack.** SSRF guards, content-type allowlists, audit-log immutability, contract integrity — the security policy in `SECURITY.md` describes what counts as a vuln and how to report it privately.

I'm a solo dev. I'll keep this responsive for as long as I can. If you find this useful, a star + a "yeah this worked" issue is more useful than silence.

---

*Ayiru is MIT-licensed. The name is Tamil. The product was originally called AgentAtlas during development; we renamed because a different `agentatlas` shipped on PyPI before us. The pivot, the council pressure-test, and the renamed package are all in the open at `roadmap_v0.2.md` and `CHANGELOG.md` if you want the messy story.*
