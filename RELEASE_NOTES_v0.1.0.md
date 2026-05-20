# Ayiru v0.1.0 — the first public release

> The verified, machine-readable knowledge layer for AI agents.
> *Wikipedia tells humans what things are. Ayiru tells AI agents what tools can do, how to use them, and whether they're safe.*

This is the first cut a stranger can install and run. 14 stages of development, 693 tests passing, ruff clean, end-to-end smoke verified from a clean venv.

---

## Install in 30 seconds

```bash
pip install ayiru
ayiru migrate
ayiru serve
```

API at `http://localhost:8000`. Interactive docs at `/docs`. To populate the demo graph:

```bash
ayiru seed --reset            # ~47 claims across git / gh / docker / vercel-cli / openai-api
ayiru query --tool github-cli --command 'gh repo delete my-org/x --yes'
# BLOCK  risk=critical  confidence=0.69
#   - Deleting a GitHub repository is an irreversible remote mutation.
#   - Safety policy blocks auto-execution at risk level 'critical'.
```

Docker:

```bash
docker build -t ayiru .
docker run --rm -p 8000:8000 ayiru
```

Claude Desktop / Cursor / Cline (MCP):

```json
{ "mcpServers": { "ayiru": { "command": "/abs/path/to/ayiru", "args": ["mcp"] } } }
```

---

## What's in this release

### The safety-arbiter core
- **`POST /v1/query/validate-command`** — the headline endpoint. Returns `{safe_to_auto_execute, risk_level, requires_human_confirmation, reasons, evidence, verification_level, confidence}`. Default-deny on no match.
- **Three-gate auto-execute** — a command is `safe_to_auto_execute=true` only when *all three* hold: safety policy permits the risk level ∧ confidence ≥ 0.70 ∧ verification level ≥ L2_source_verified. No two-out-of-three.
- **Deterministic risk engine** — contract-driven (`contracts/risk_model.v1.json`). No LLM in the safety path. Every classification is reproducible from the same input.
- **Six verification levels** — L0_unverified → L1_schema_valid → L2_source_verified → L3_runtime_verified → L4_cross_agent_verified → L5_human_audited. The orchestrator refuses to skip levels.

### Six ingestion lanes (SSRF-safe)
- CLI subprocess sandbox with argv allowlist (Stage 7a)
- HTTPS docs fetch with content-type allowlist (Stage 7b)
- OpenAPI / JSON Schema / GraphQL SDL parsers with byte-level provenance (Stage 7c.1–c.3)
- MCP server metadata via local stdio spawn (Stage 7d)

### Outbound MCP server (Stage 10)
Hand-rolled JSON-RPC over stdio. Six tools exposed to MCP clients: `validate_command`, `get_tool_spec`, `search_tools`, `explain_risk`, `get_safe_workflow`, `submit_claim`. Returns both legacy `content[]` text blocks and modern `structuredContent` objects so old and new clients both work.

### Human review + append-only audit log (Stage 13)
- `POST /v1/verification/human-review` — file an APPROVED / REJECTED / NEEDS_CHANGES decision against any claim.
- APPROVED against an L3+ claim promotes to **L5_human_audited** with a confidence bonus and reviewer attribution. Below L3, the review is recorded but the promotion is refused with an explanatory reason.
- REJECTED flips `verification_status` so the matcher excludes the claim from auto-execution surfaces immediately.
- `GET /v1/verification/review-queue` paginates pending reviews; `GET /v1/audit/events` and `GET /v1/audit/claims/{id}` expose the immutable audit trail.
- **Append-only by contract**: no `update_audit_event` or `delete_audit_event` method on the store. Introspection test fails the build if any future PR adds one.

### Hardening (Stage 14)
- **Wheel-bundled**: contracts, seed artifacts, and migrations all ship inside the package. Clean-venv `pip install ayiru` works without a checkout.
- **`/v1/` API versioning** with legacy paths kept for one release with RFC 8594 `Deprecation` / `Sunset` / `Link` headers.
- **Per-request structured logging** with `X-Request-ID` echo + JSON log line per request on the `ayiru.request` logger.
- **Optional API-key auth** (`AYIRU_API_KEY`). Off by default. When set: writes require Bearer token; reads + health stay public; timing-safe key comparison.
- **Optional reviewer allowlist** (`AYIRU_REVIEWER_REGISTRY`) gates the human-review endpoint.
- **Auto-migrate on `ayiru serve`** (`--no-migrate` opts out).
- **Postgres dialect smoke** — every table and the full alembic chain compiles cleanly against `postgresql+psycopg` offline (live PG test matrix arrives in v0.2).

### CLI + Docker (Stage 12)
One `ayiru` binary on PATH. Subcommands: `serve` · `mcp` · `seed` · `migrate` · `query` · `verify` · `tools` · `--version`. One-stage Dockerfile bundles everything.

### Demo dashboard (Stage 11b)
Minimal Next.js 14 UI with landing page, tools list, tool detail, and a `validate_command` playground. Same-origin proxy via `next.config.mjs` — no CORS configuration required.

---

## Validation in this release

| | Result |
|---|---|
| Backend tests | **693 passing**, hermetic, ~30 s |
| Lint | `ruff check app tests` clean |
| Migration roundtrip | `alembic upgrade → downgrade -5 → upgrade head` clean through `0015_human_review_and_audit_log` |
| Clean-venv install | `pip install ayiru-0.1.0-py3-none-any.whl` → `ayiru --version` → `ayiru seed --reset` populates 47 claims → headline demo query returns the expected critical-block verdict |
| Postgres dialect | DDL + full alembic chain render under `postgresql+psycopg` offline |
| Frontend | `next build` clean (no Python frontend tests in v0.1) |

---

## Known limitations

Being honest about the v0.1 gaps:

- **SQLite is the only tested backend.** Postgres compiles cleanly offline; live test matrix lands in v0.2.
- **API-key auth is single-tenant.** OAuth / OIDC / per-key rate limiting is v0.2.
- **Reviewer registry is a string allowlist.** Cryptographic per-reviewer identity is v0.2.
- **No `ask` (retrieval) endpoint yet** — the agent-search-box pitch in `roadmap_v0.2.md` ships in v0.2 after a measurement-spike gate.
- **Tool coverage is curated** (Stage 0 lock). 5 native tools + 5 MCP servers. Adding tools is a contract change, not code; see the "Add a tool" issue template.
- **Live Postgres CI matrix, native rate limiting, L4 cross-agent verification, adversarial pen-test** — all on the v0.2 / v0.3 backlog.

---

## What's next

`roadmap_v0.2.md` (in the repo) lays out the v0.2 plan: a pivot toward "the local search box your AI agent hits before the web — cuts tool-call costs by routing common queries to a verified knowledge graph instead of paying for `WebSearch` tokens." That pivot is **gated behind a Phase 0 measurement spike**; no v0.2 code lands until the spike passes the Decision Gate documented in the roadmap.

---

## Get involved

- **Issues** — bug reports, feature requests, "add a tool" requests have templates ready: <https://github.com/ruth411/ayiru/issues/new/choose>
- **Discussions** — questions, ideas, show-and-tell: <https://github.com/ruth411/ayiru/discussions>
- **Security** — private disclosure via GitHub Security Advisories. See `SECURITY.md`.
- **Contributing** — read `CONTRIBUTING.md`. The non-negotiables (safety rules don't weaken, contracts are versioned, migrations stay reversible) are not negotiable.

---

## Thanks

Built on FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, httpx, `openapi-spec-validator`, `jsonschema`, `graphql-core`. The MCP implementation follows the [Model Context Protocol](https://modelcontextprotocol.io/) specification.

— Ayiru contributors
