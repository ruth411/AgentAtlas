# Ayiru — Roadmap v0.2.1 (Council-Revised Pivot + Open-Source Launch)

> **Status:** v0.1 (Stages 0–14) is feature-complete. This document is the v0.2 plan — the product pivot from "safety arbiter for destructive commands" to "local search box that cuts agent API costs," plus the open-source launch.
>
> **Revision (v0.2.1) — what a 5-advisor council pressure-test changed:**
> 1. A new **Phase 0 measurement spike (Week 0)** is mandatory before any code in Phase A. The plan now refuses to assume agents actually waste meaningful tokens on web searches — it measures.
> 2. A **Decision Gate** sits between Phase 0 and Phase A with explicit go/no-go thresholds. If the spike fails, the entire pivot is dead and a different product needs to be found.
> 3. **Kill criteria** added to Phase D. Solo OSS without a kill date turns into 18 months of unread issues.
> 4. **Launch budget** priced (~$50/month floor; $300 over 6 months). Previously absent.
> 5. **Cost-savings math in A2 corrected.** Previous `statement_length × 1.3` was off by 5–50×. New formula accounts for the saved web_search input + output tokens minus the ask response tokens.
> 6. **Calendar adjusted from 5 → 9 weeks.** Solo-dev OSS plans run 1.8× their initial estimate. Weeks 2 and 3 (previously a single overstuffed Week 2) are split.
> 7. **B3 embeddings demoted to stretch goal.** Lexical + BM25 from A1 ships v0.2; embeddings are a v0.2.2 polish item.
> 8. **Hosted-demo estimate corrected** from 4h to 8–12h (first Fly.io deploy with secrets, persistent volumes, custom domain).
> 9. **PyPI name check + v0.1 release tag + 5 beta testers** are now Week-0 prerequisites, not Week-4 footnotes.
> 10. **Named first reviewer for launch day** added. Previously the launch relied on algorithmic luck.

## Context

Ayiru is currently 14 roadmap stages complete: 693 backend tests passing, ruff clean, wheel install verified end-to-end, MCP server speaking JSON-RPC, optional API-key auth + reviewer registry, append-only audit log with L0–L5 verification ladder. **The codebase is finished against its original roadmap.** What it lacks is a coherent product story, users, and distribution.

The product positioning is being deliberately reframed:

- **From:** "Verified machine-readable knowledge layer for AI agents" / safety arbiter for destructive commands (current README hero).
- **To:** "The local search box your AI agent hits before the web — cuts tool-call costs by routing common queries to a verified knowledge graph instead of paying for `WebSearch` tokens."

This reframe makes the buyer concrete (indie/startup agent developer paying for tokens), the value measurable ("you saved $X this month"), and the moat real (verified, cited answers vs generic scraped docs).

**Confirmed scope decisions:**
1. **Open core + hosted SaaS** — MIT-licensed server + optional paid hosted version (PostHog / Supabase / Cal.com model). This plan includes both.
2. **Safety machinery stays as a differentiator, demoted from headline** — `validate_command`, risk engine, audit log, human review remain in the API as moat features; the README leads with cost savings.

The current README and Next.js dashboard both still lead with the safety-arbiter pitch and must be rewritten. The MCP server has six tools today; a new `ask` tool becomes the seventh and the headline. There is no semantic-search infrastructure yet — clean slate, no conflicting code to remove.

---

## Current State Audit (what's already done)

### Build complete (don't touch)
- 14 stages: domain model, claim API, orchestrator, confidence scorer, deterministic risk engine, canonical specs, 6 ingestion lanes (CLI/docs/OpenAPI/JSON Schema/GraphQL/MCP), L2→L3 runtime verification, agent query surface, outbound MCP server, seed dataset, dashboard, CLI + Docker, human review + audit, hardening.
- 693 tests passing, ruff clean, alembic up/down/up clean.
- Wheel bundles contracts + seed artifacts + migrations. Clean-venv `pip install` works end-to-end.
- `/v1/` API versioning with legacy mount + RFC 8594 deprecation headers.
- `RequestObservabilityMiddleware` (request id + structured JSON log per request).
- `ApiKeyAuthMiddleware` (opt-in via `AYIRU_API_KEY`).
- LICENSE (MIT), CONTRIBUTING.md, SECURITY.md, `.github/workflows/ci.yml`.

### Verified gaps (confirmed by Phase 1 exploration)
- `QueryEngine` (`backend/app/services/query_engine.py`) has 5 methods; **no semantic search, no embeddings, no vector store**. Clean slot for an `ask()` method beside `search_tools()`.
- `command_matcher.py` is purely lexical (exact + prefix). No semantic component.
- `docs_ingestion.py` has `ingest_all_for_tool()` — batchable, but no bulk CLI harness exists.
- Stage 0 tool lock is one cache function (`_stage_0_tool_ids()` at `claim_store.py:60-74`) enforced once in `create()`. Needs a curated/uncurated split.
- MCP tool registry (`backend/app/mcp_server/tools.py`) is a simple list. New `ask` tool drops in cleanly.
- README hero says "verified, machine-readable knowledge layer." Frontend `frontend/app/page.tsx` centred on `validate_command`. Both need rewrites.
- `pyproject.toml` version `0.1.0`. No `dist/` directory — wheel not built yet, never uploaded to PyPI.

### Known structural gaps (from council session)
- **47 seed claims** is two orders of magnitude too small for the search-box pitch.
- **No SDK** — agents have to call REST or wire MCP manually.
- **No latency budget enforcement** — agents call hundreds of APIs/minute; the human-review path is incompatible with that cadence (relevant for the safety surface, not the ask surface).
- **No telemetry** — no way to measure queries served or API calls saved.
- **MCP stdio path has no auth** (Stage 14's API-key middleware only covers HTTP).
- **No adversarial pen-test** on SSRF / content-type / audit-log immutability claims.

---

## The Pivot at a Glance

| Dimension | Today (v0.1) | After this plan (v0.2) |
|---|---|---|
| Headline | "Verified knowledge layer for AI agents" | "Local search box that cuts your agent's API bill" |
| Primary endpoint | `POST /v1/query/validate-command` | `POST /v1/query/ask` |
| Primary MCP tool | `validate_command` | `ask` (validate_command still listed) |
| Buyer | Compliance officer / regulated co. | Indie / startup agent developer |
| Value prop | "Audit trail for AI decisions" | "$X saved on tool-call tokens this month" |
| Demo asset | "Try a query" playground | 15-second GIF: agent → Ayiru → cited answer → skips web |
| Tool coverage | 10 (Stage 0 lock) | 50 curated + 1000s uncurated (L0) |
| Distribution | Repo checkout / Docker | PyPI + `ayiru-client` + LangChain adapter + hosted demo |

---

## Phase 0 — Pre-flight Validation (Week 0)

**Five tasks. All required. No code in `backend/app/` is allowed until all five pass the Decision Gate below.**

### P0.1 — Agent measurement spike (the task the entire plan rests on)

The pivot to "agent search box" rests on an unmeasured assumption: that agents waste meaningful tokens on web searches for information that doesn't need the live internet. If that's false, every line of Phase A is building the wrong thing.

- Pick one off-the-shelf agent harness — LangChain, Claude Agent SDK, or AutoGen.
- Give it 10 realistic dev tasks: *"set up a new Next.js project,"* *"create a Postgres user with read-only access,"* *"deploy a Python script to Fly.io,"* etc.
- **Run 1 (baseline):** 100 total tool-call turns, `web_search` as the only knowledge tool. Record: how many turns invoked web search, what queries, what input + output tokens went over the wire.
- **Run 2 (intervention):** implement a *fake* `ask` tool — a hardcoded Python dict with answers for the top 20 queries observed in Run 1. Re-run the same 10 tasks. Record: tool-choice rate (`ask` vs `web_search`), token delta, observed agent behaviour.

**Output:** two numbers and one observation.
1. *Fraction of agent token cost that is web-search-replaceable lookups* (Run 1 metric).
2. *Tool-choice rate for `ask` when both are listed* (Run 2 metric).
3. *Did the LLM hallucinate plausible-but-wrong answers when `ask` returned hits?* (qualitative).

### P0.2 — PyPI name reservation

- Run `pip index versions ayiru` and `pip index versions ayiru-client`.
- If either is taken, this is a Week-0 blocker. Pick a fallback (`aatlas`, `agent-atlas`, `atlas-agent`) and grep the repo for replacements before any other Phase 0 task starts.

### P0.3 — Tag and ship v0.1.0

- `git tag v0.1.0 && git push --tags`.
- `python -m build backend` to produce the wheel.
- "v0.1 is complete" is not credible without a tag. This makes the existing 14 stages a real release before any v0.2 work touches them. Also surfaces any wheel-build regressions while there's still slack to fix them.

### P0.4 — Recruit 5 beta testers

- Five named agent developers who commit (in writing, even a DM screenshot) to running Ayiru v0.2 against their real agent within 7 days of A1 shipping. In exchange: early access + a direct Discord/email line to you.
- Source: r/LangChain, Anthropic / Cursor / MCP Discords, your Twitter network, dev.to / Substack comments. Cold-DM 30 people; expect 5 yeses.
- **If you cannot get 5 in a week, the launch has no audience.** Reduce scope to a Show-the-Code release or delay until recruitment works.

### P0.5 — Price the launch budget

- Itemise the monthly running cost (see Phase D § Launch Budget below). Floor is **~$50/month** sustained for 6 months = **$300 sunk cost minimum**.
- Decide who funds this. If self-funded with no income, the Phase D hosted-SaaS rollout is fantasy without a runway plan. Either set a 6-month kill date now, or line up sponsors during Phase 0.

---

## Decision Gate (between Phase 0 and Phase A)

**Proceed to Phase A only if all three are true:**

| # | Threshold | If false |
|---|---|---|
| 1 | P0.1 metric 1: **≥ 25%** of agent token cost is web-search-replaceable lookups. | Pitch is wrong. Pivot to the actual dominant cost (likely reasoning-loop output volume or tool-response normalisation). Burn this roadmap; write a new one. |
| 2 | P0.1 metric 2: LLM picked the fake `ask` over `web_search` in **≥ 50%** of opportunities. | The docstring / tool-choice problem dominates retrieval. Spend Week 0 on a docstring optimisation spike, then re-measure. |
| 3 | P0.4: 5 named beta testers committed in writing. | Launch has no audience. Reduce scope (no hosted demo, no Stripe) or delay until recruitment works. |

If **metrics 1 AND 2 both fail:** consider the council's expansionist branches — cost-observability dashboard, MCP server registry — as the real product. Phase A as written is dead.

This gate is the cheapest thing in the plan and the most expensive thing to skip.

---

## Phase A — Pivot Implementation (Weeks 1–5, post-gate)

> **Calendar realism note:** Previous version called this 3 weeks. Solo-dev OSS plans run 1.8× their initial estimate; Week 2 alone was three sprints crammed into one. New baseline: **5 weeks of focused work** (Weeks 1–5). If evenings/weekends only, double again.

### Week 1: Retrieval surface

**A1. Add `POST /v1/query/ask` endpoint.**

- File: `backend/app/api/routes_query.py` (add new route)
- Schema: new `AskRequest` and `AskResponse` in `backend/app/schemas/query.py`. Response includes `answers: list[Answer]`, `fallback_recommended: bool`, `estimated_tokens_saved: int`.
- Engine method: add `QueryEngine.ask(question: str, limit: int = 5)` in `backend/app/services/query_engine.py` next to `search_tools()` (line ~143).
- Implementation: **start with pure lexical** — `LIKE %term%` on `KnowledgeClaim.subject` + `statement` with token-overlap ranking. No embeddings yet. Ship v0 by Friday.
- Add to MCP server as 7th tool in `backend/app/mcp_server/tools.py`.
- Tests: 8–10 tests in new `backend/tests/test_query_ask.py`.

**A2. Cost-savings telemetry baked in (math corrected per Council).**

- **Corrected formula** — previous version used `statement_length × 1.3` which mismeasured by 5–50×. What the agent actually saves is `(web_search_input_tokens + web_search_output_tokens) − ask_response_tokens`. A typical web_search call costs ~30 input tokens (query) + ~800 output tokens (search results) = **~830 tokens replaced per hit**. Ayiru serves the same answer in ~150 tokens. Net saving: **~680 tokens per query**.
- Hardcode `_AVERAGE_WEB_SEARCH_TOKENS = 830` as a module constant in `app/services/query_engine.py`, with a code comment that says: *"Refine with real telemetry from Phase 0 / first 100 real queries. The constant is the only knob; do not scatter token-cost arithmetic across the codebase."*
- `estimated_tokens_saved = max(0, _AVERAGE_WEB_SEARCH_TOKENS - (len(json.dumps(answers)) // 4))`.
- Add `GET /v1/stats/savings` endpoint that aggregates by API key from the audit log (re-use Stage 13 `audit_events` table; emit a new `QUERY_SERVED` audit event type on every `ask`).
- New migration `0016_add_query_served_event_type.py` extending the CHECK constraint on `event_type`.
- **Calibration task:** after first 1k real queries land, recompute `_AVERAGE_WEB_SEARCH_TOKENS` from observed agent behaviour. The constant becomes a measured number, not a guess.

**A3. Update audit event taxonomy.**

- Add `QUERY_SERVED` to `AuditEventType` enum in `backend/app/schemas/enums.py`.
- Emit one per successful `ask` from inside the route. Details: `{question_length, answers_returned, tokens_saved, top_claim_id}`.
- Lockstep contract sync still required if any contract JSON changes (none expected for this step).

### Week 2: Curation split (B1 only — Week 2 in v0.2 was 3 sprints in 1, now split)

**B1. Relax Stage 0 lock — curated vs uncurated.**

- File: `backend/app/services/claim_store.py`. Split `_stage_0_tool_ids()` into `_curated_tool_ids()` and remove the hard reject.
- New behaviour: claims for non-curated tools persist at `verification_level=L0_UNVERIFIED` and `verification_status=PENDING`. Curated tool claims keep existing orchestrator path.
- `ToolNotAllowedError` is now only raised in **strict mode** (configurable via `AYIRU_STRICT_TOOL_LOCK=1`) — defaults off.
- Update `ayiru_stage_0.v1.json` contract: add `curated: true` to existing entries; bump to `v2.json` if behaviour changes. New file at `contracts/ayiru_stage_0.v2.json`. Old `v1.json` stays for replay.
- Update `app/contracts/ayiru_stage_0.v2.json` lockstep copy.
- Tests: `backend/tests/test_uncurated_claims.py` — confirm uncurated claims persist, are returned by `ask`, but excluded from `validate_command`.

### Week 3: Bulk ingestion (B2)

**B2. Bulk ingestion harness.**

- New CLI subcommand: `ayiru ingest --source docs --tool-list path/to/tools.yml` in `backend/app/cli.py`.
- `tools.yml` defines: `tool_id`, list of `urls`, `claim_type` defaults. 50 entries for the top dev tools.
- Wire to existing `DocsIngestionService.ingest_all_for_tool()` in a loop.
- Add resume support: track per-URL ingestion state in the audit log; skip already-ingested URLs unless `--force` is passed.
- Run against curated list: aws-cli, kubectl, terraform, helm, ansible, npm, pip, cargo, go, rust, postgresql-cli, mysql-cli, redis-cli, mongodb, sqlite3, curl, wget, jq, yq, ssh, rsync, tmux, vim, sed, awk, ffmpeg, imagemagick, openssl, gpg, htop, ps, lsof, netstat, dig, nmap, systemctl, journalctl, brew, apt, dnf, supabase-cli, fly, railway, vercel-cli (already), heroku, gh (already), git (already), docker (already), kubernetes-cli, openai-api (already). Target: **5,000 claims minimum**, 50 tools.
- **Realism note:** previous version put B1+B2+B3 in a single week. B2 alone is 5–10 days because (a) some docs are JS-rendered SPAs that `httpx` skips silently — drop those tools or queue Playwright as a v0.2.2 task; (b) per-vendor rate limits will throttle the crawl; (c) per-vendor ToS / robots.txt must be honoured. Budget the full week.
- **Legal pre-check:** for each of the 50 tools, confirm docs license. GitHub docs are CC BY 4.0 (✓), Docker docs are Apache 2.0 (✓), Stripe's API reference is © Stripe Inc (needs review). If a license is unclear, drop the tool from v0.2 rather than risk a DMCA after launch.

### Week 4: Distribution surface

**C1. Drop-in Python client SDK.**

- New package directory: `clients/python/ayiru_client/` (separate `pyproject.toml`, sibling to `backend/`).
- API:
  ```python
  from ayiru_client import Ayiru
  atlas = Ayiru(base_url="http://localhost:8000")  # or hosted URL
  answer = atlas.ask("how do I delete a docker volume")
  if answer.is_useful:
      return answer.statement
  ```
- `is_useful` is true when `confidence >= 0.6` and `verification_level >= L1`.
- Async variant: `AsyncAyiru` using httpx.AsyncClient.
- Tests: `clients/python/tests/test_client.py` against a TestClient-wrapped backend.

**C2. LangChain adapter.**

- New file: `clients/python/ayiru_client/langchain.py`.
- Export `AyiruTool` that subclasses `langchain_core.tools.BaseTool`.
- Docstring is what the LLM sees — write it carefully. Should mention "use this before web search for common dev questions."
- Example notebook: `clients/python/examples/langchain_demo.ipynb` — 10-question demo, shows 7 hits + 3 web-search fallbacks with cost-saved counter.

**C3. README pivot + GIF.**

- Rewrite `README.md` hero: one sentence, agent-search-box pitch. Drop "Wikipedia for AI agents" framing.
- New hero GIF (15s recording): LangChain agent → `atlas.ask("how do I delete a docker volume")` → cited answer → bottom of screen shows "saved 1,200 tokens / $0.014."
- New repo description (GitHub repo settings): "Local search box for AI agents — cuts tool-call costs by routing common queries to a verified knowledge graph."
- New topics: `ai-agents`, `mcp-server`, `llm-tools`, `langchain`, `agent-infrastructure`, `llm-cost-optimization`.
- Demote the "Stages" table — move out of headline into a collapsed `<details>` block at the bottom.
- Frontend `frontend/app/page.tsx` rewrite: new hero matches README, swap interactive component to `ask` (not `validate_command`).

### Week 5: Stretch — Hybrid retrieval (B3 — embeddings)

**Demoted from required to stretch goal per Council.** Lexical + token-overlap ranking from A1 ships v0.2 if B2 delivers 5,000 claims. Embeddings are a v0.2.2 polish item that materially helps paraphrased / synonymous queries but is not a launch blocker.

If you finish Weeks 1–4 with spare capacity, do this:

- Add dependency: `sentence-transformers>=3.0,<4.0` and `sqlite-vec>=0.1,<1.0` in `backend/pyproject.toml`.
- New table: `claim_embeddings` (claim_id PK + 384-dim vector). Migration `0017_create_claim_embeddings.py`.
- Background job at ingestion time: compute embedding for every new claim's `statement`. Use `sentence-transformers/all-MiniLM-L6-v2` (80MB, CPU-only, ~10ms/query).
- Rewrite `QueryEngine.ask()` as hybrid: lexical candidate pool (top 50 by `LIKE %term%`) → re-rank by cosine similarity to question embedding → return top K.
- Add backfill script: `ayiru reindex` regenerates all embeddings; safe to re-run.
- Tests: `backend/tests/test_hybrid_retrieval.py` — pin the property that semantic-only matches (synonyms, paraphrases) surface, not just keyword overlap.

**If you don't have time:** ship lexical-only. The Phase 0 measurement already validated that the basic pitch works; B3 is amplification.

---

## Phase B — OSS Pre-Launch Polish (Week 6)

Items in priority order. Aim for ~12 focused hours of work across the week.

| # | Item | File / location | Time |
|---|---|---|---|
| 1 | PyPI release — `python -m build && twine upload`. Tag `v0.1.0`. Verify `pip install ayiru` on a clean venv. | `backend/dist/` | 1h |
| 2 | Release `ayiru-client` to PyPI separately. | `clients/python/dist/` | 30m |
| 3 | `CHANGELOG.md` at repo root — Keep-a-Changelog format. v0.1.0 entry summarising 14 stages + pivot. | `/CHANGELOG.md` | 30m |
| 4 | Issue templates — bug report, feature request, "new tool" request (since this is your scope-creep firehose). | `.github/ISSUE_TEMPLATE/*.yml` | 1h |
| 5 | PR template — checklist mirrors CONTRIBUTING.md (tests, migration reversible, contract versioned). | `.github/pull_request_template.md` | 30m |
| 6 | Enable GitHub Discussions. Create welcome post + "show us what you built" thread. | repo settings | 30m |
| 7 | `.github/FUNDING.yml` — GitHub Sponsors, optional Buy Me a Coffee. | `.github/FUNDING.yml` | 15m |
| 8 | GitHub Project (roadmap board) with v0.2 candidate items: more tools, semantic improvements, cross-agent verification (L4), per-reviewer Ed25519 identity. | repo Projects | 1h |
| 9 | Repo social preview image — 1280×640 PNG with the pitch + logo. | repo settings | 1h |
| 10 | Hosted demo deploy — pick Fly.io or Railway. `ayiru serve` in a container at `try.ayiru.dev` (or `ayiru.fly.dev` for v0). Cap at 100 req/min/IP via Fly's built-in limits. **Council-revised estimate** (first deploy, with secrets management, persistent SQLite volume, custom domain, TLS, rate-limiting config). | `Dockerfile`, hosting | **8–12h** |
| 11 | "Try it" CTA in README pointing at the hosted demo, with the exact `curl` to fire the first query. | `README.md` | 30m |
| 12 | Replace `ruth411` placeholders in URLs with the actual repo path. Confirm or update. | grep `ruth411` repo-wide | 15m |
| 13 | Add maintainer contact — `security@ayiru.dev` or your real email in SECURITY.md + a "Get in touch" line in README. | `SECURITY.md`, `README.md` | 15m |
| 14 | Add `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1, one-time copy-paste. Reduces friction for some communities. | `/CODE_OF_CONDUCT.md` | 15m |
| 15 | Star History badge (star-history.com) in README to make the project look alive. | `README.md` | 15m |

---

## Phase C — Launch Day (One Tuesday, Week 7)

Pick a Tuesday. Block the whole day. Don't fire any of these before all of Phase A + Phase B is verified working *and* the new launch-day prerequisites below pass.

### Pre-launch checklist (must be true at 07:00 Tuesday)

- All 5 Phase 0.4 beta testers have run v0.2 against a real agent in the last 7 days and given written feedback.
- At least one of those 5 has agreed to be the **named first user** in the README (Phase D requirement; previously hand-waved).
- A **named first reviewer** outside the council — newsletter writer, podcast host, micro-influencer in the agent/MCP space — is briefed and primed to publish a same-day post or thread. Cold launches without seeded reviewers depend on algorithmic luck on a Tuesday afternoon, which the council called out as the single biggest distribution gap.
- The clean-venv `pip install ayiru` smoke completed in the last 24 hours (not just last week).
- `try.ayiru.dev` (or your fallback domain) responded to a real `ask` call in the last hour.

**If any of these is missing, delay one week.** A bad launch with seeded reviewers is recoverable; a clean launch with no audience is not. The "do not fire this sequence early" rule from v0.2 stands — and now has a checklist that operationalises it.

| Time | Action |
|---|---|
| **08:00** | Final smoke: `pip install ayiru` clean venv, run `ask`, verify GIF still plays, hosted demo responsive. |
| **09:00** | Publish blog post on your domain (or dev.to / Medium). Title: "I built Ayiru to cut my agent's API bill — here's how it routes queries to a local knowledge graph instead of the web." Include the GIF, install command, hosted-demo link. |
| **10:00** | Twitter/X thread (7–10 posts). Open with the itemized-bill screenshot showing dollars saved. End with the install command. |
| **11:00** | Post to **r/LocalLLaMA** — "Ayiru — local search box for AI agents, cuts tool-call costs." |
| **13:00** | Post to **r/LangChain** with the LangChain demo notebook. |
| **15:00** | **Hacker News** — Show HN: Ayiru — agent search box that cuts API costs. Stay online answering comments until 22:00. |
| **18:00** | Cross-post to Lobsters, the LangChain Discord, the Cursor Discord, the Anthropic Discord, the OpenAI Devs Discord, the MCP working group Slack. |
| **Wed–Fri** | Respond to every GitHub issue within 24h. Merge low-risk PRs same-day. Don't sleep on a Show HN thread. |

**Do not fire this sequence early.** A broken `pip install` on launch day costs you the only HN attempt you'll get for v0.1.

---

## Phase D — Post-Launch Sustainability (Ongoing)

### Launch Budget (added per Council — was missing from v0.2)

| Item | Monthly | Notes |
|---|---|---|
| Fly.io hosted demo (`ayiru.fly.dev`) | $10–30 | Shared-CPU instance, ~1 GB persistent volume for SQLite, custom domain |
| Domain registration (`ayiru.dev`) | $1–2 | Annual prepay; budget $20/yr if you grab a `.dev` |
| Transactional email (Resend / Postmark) | $0–10 | Free tier covers ~3k emails/month — enough for early Stripe webhooks + signups |
| Stripe | 2.9% + $0.30/txn | Zero until the first paid customer |
| Backup storage (S3 / Cloudflare R2) | $1–5 | Daily SQLite dump retention; cheap until corpus grows past 1 GB |
| Twitter / X Premium (optional, helps reach) | $0–8 | Skip for v0.2; revisit if launch needs amplification |
| **Realistic floor** | **~$50/month** | **6-month sustained commitment = ~$300 sunk cost minimum** |

Decide who funds this **before Phase B**, not after launch. If you're self-funded with no income, you've now signed up for a 6-month operating cost on top of your own time. Options:
1. Set an explicit 6-month kill date (see Kill Criteria below).
2. Find a sponsor during Phase 0 — one OSS-friendly company committing $50–200/month is enough.
3. Cap launch to no-hosted-demo mode (repo + PyPI only). Smaller surface, smaller bill, smaller upside.

### Kill Criteria (added per Council — was missing from v0.2)

A pivot needs an explicit failure mode, not "we'll see how it goes." Solo OSS without a kill date becomes 18 months of unread issues. Sunset the v0.2 pivot — and re-evaluate whether Ayiru should continue at all — if any of these is true at the marked checkpoint:

| Checkpoint | Failure trigger | Decision |
|---|---|---|
| **Week 8 (1 week post-launch)** | < 10 GitHub stars from non-personal-network sources | HN / Reddit / Discord didn't catch. No organic discovery. Stop launch amplification; debrief on positioning. |
| **Week 10 (1 month post-launch)** | < 3 unique users have called `ask` ≥ 10 times each | Installs exist but aren't producing usage. Either the value isn't real, or onboarding is broken. Talk to the 5 beta testers about why. |
| **Month 3** | Zero paying customers or sponsors on the hosted SaaS | The cost-savings pitch doesn't convert. Open-source can continue; the SaaS arm dies. |
| **Month 6** | Combined monthly active users < 50 across all install paths | The product hasn't found its audience. Either pivot again (with a fresh council pressure-test) or sunset. |

Hitting any of these isn't "keep grinding harder." It's "the product is wrong; pivot or stop." Define the kill criteria before launch so they're load-bearing decisions, not rationalisations after the fact.

### First 30 days
- **24–48h issue response SLA.** Self-imposed but non-negotiable. Stale issues kill OSS adoption signal.
- **Weekly digest tweet** — "This week in Ayiru: X tools added, Y queries served, Z PRs merged." Keeps the project visible.
- **One named user** in the README — first real production deployment, even if it's small.
- **Adoption metric** — public counter on the hosted demo: "X tokens saved across all users this month."

### Hosted SaaS rollout (Month 2–3)
- Provision `ayiru.dev` or similar. Stripe billing. Free tier (1k queries/month), Pro ($20/mo, 100k queries), Team ($100/mo, multi-key).
- Pre-ingest 50,000 tools using Phase A2's bulk harness running against curated lists.
- Add API-key issuance + per-key rate limiting (extend `ApiKeyAuthMiddleware` from per-process env var to a DB-backed registry).
- Cost-analytics dashboard — the buying signal made visible.

### Scope-creep firewall
- New tool requests go to the "Add a tool" issue template, which routes to a documented PR template at `docs/adding_a_tool.md`. **Never accept tool additions via comments.**
- Stage 0 contract changes require a contract version bump and a regression test. Document this in CONTRIBUTING.md.

### Hardening backlog (don't block launch on these)
- Live Postgres test matrix via `testcontainers`. Stage 14 has the offline smoke; live tests come post-launch.
- Per-reviewer Ed25519 identity replacing the string-allowlist registry.
- Native rate limiting (today: relies on reverse proxy).
- L4 cross-agent verification path.
- Adversarial pen-test on SSRF / content-type / audit-log immutability. **Schedule this in Month 2.**

---

## Critical Files to Modify

| Path | Why |
|---|---|
| `backend/app/services/query_engine.py` | Add `ask()` method beside line 143. |
| `backend/app/api/routes_query.py` | Add `POST /v1/query/ask` and `GET /v1/stats/savings`. |
| `backend/app/schemas/query.py` | New `AskRequest`, `AskResponse`, `Answer`, `SavingsResponse` schemas. |
| `backend/app/schemas/enums.py` | Add `QUERY_SERVED` to `AuditEventType`. |
| `backend/app/services/claim_store.py:60-74` | Relax `_stage_0_tool_ids()` into `_curated_tool_ids()` + strict-mode flag. |
| `backend/app/mcp_server/tools.py` | Add `ask` as the 7th MCP tool. |
| `backend/app/cli.py` | Add `ayiru ingest` subcommand for bulk ingestion. |
| `backend/app/services/docs_ingestion.py` | Already batchable via `ingest_all_for_tool()`; no changes. Re-use. |
| `backend/pyproject.toml` | Add `sentence-transformers`, `sqlite-vec` deps. |
| `backend/alembic/versions/0016_*.py`, `0017_*.py` | New migrations for audit event type + embeddings table. Mirror into `backend/app/_alembic/versions/`. |
| `backend/app/contracts/ayiru_stage_0.v2.json` | New contract version with `curated: true` flags. Lockstep copy in `app/contracts/`. |
| `clients/python/` | New SDK package directory. |
| `frontend/app/page.tsx` | Hero rewrite — agent-search-box pitch, `ask` interactive. |
| `README.md` | Hero rewrite + GIF + repo description + topics. |
| `CHANGELOG.md`, `CODE_OF_CONDUCT.md` | New files. |
| `.github/ISSUE_TEMPLATE/*.yml`, `pull_request_template.md`, `FUNDING.yml` | New OSS hygiene files. |

### Existing utilities to reuse (don't rebuild)
- `DocsIngestionService.ingest_all_for_tool()` — bulk ingestion already exists.
- `_add_audit_event()` in `claim_store.py` — append-only audit emitter; reuse for `QUERY_SERVED`.
- `make_alembic_config()` in `services/alembic_config.py` — already handles wheel + source-tree paths.
- `current_request_id()` in `app/observability.py` — attach to audit events from inside `ask`.
- `RequestObservabilityMiddleware` — already emits structured logs; new endpoints get telemetry for free.

---

## What NOT to Do

Explicit non-goals so the scope stays sharp:

- **No L4 cross-agent verification** in this pivot. Defer to v0.3.
- **No live Postgres CI matrix.** Stage 14's offline smoke is the v0.1 guarantee.
- **No rate limiting native to the API.** Hosted demo uses Fly.io's built-in limits.
- **No OAuth / OIDC.** API-key auth is sufficient for v0.2.
- **No dashboard rewrite beyond hero + `ask` UI.** Resist the urge to redesign `frontend/`.
- **No Stage 0 contract removal.** Curated tools still get verified, audited, runtime-checked. Uncurated is the *additional* layer, not a replacement.
- **No MCP-server-as-network-service.** Stdio-only for v0.2. The HTTP server is the network surface.
- **No deletion of `validate_command`.** It stays in `/v1/query/validate-command`, MCP tool list, audit log. It's just not the headline.
- **No new ingestion lanes.** Current six (CLI/docs/OpenAPI/JSON Schema/GraphQL/MCP) are enough.
- **No HN launch before all of Phase A + Phase B is shipped and smoke-tested.** One-shot, can't redo.

---

## Verification — How to know each phase is done

### Phase 0 done when (added per Council; gate for everything else):
1. P0.1 measurement spike has produced two numbers (web-search-replaceable token fraction; `ask`-vs-`web_search` tool-choice rate) AND those numbers cleared the Decision Gate (≥ 25% and ≥ 50% respectively).
2. P0.2 confirmed `ayiru` and `ayiru-client` are available on PyPI (or fallback names chosen and grep'd through the repo).
3. P0.3 — `git tag v0.1.0` is pushed, wheel built in `backend/dist/`.
4. P0.4 — 5 named beta testers committed in writing (DM screenshot, email, whatever — proof that survives a memory lapse).
5. P0.5 — launch budget itemised, monthly floor identified (~$50/month), funding source decided OR 6-month kill date written down.

### Phase A done when:
1. `curl -X POST localhost:8000/v1/query/ask -d '{"question":"how do I delete a docker volume"}'` returns a non-empty `answers` array with `confidence >= 0.6`.
2. `ayiru ingest --source docs --tool-list tools.yml` populates ≥ 5,000 claims across ≥ 50 tools.
3. `pip install ayiru-client && python -c "from ayiru_client import Ayiru; print(Ayiru('http://localhost:8000').ask('git status').statement)"` returns a cited answer.
4. LangChain demo notebook runs end-to-end, prints "saved $X.XX in this session" at the end.
5. Full test suite: ≥ 730 tests passing (current 693 + ~40 new). Ruff clean.

### Phase B done when:
1. `pip install ayiru` on a fresh `python3 -m venv /tmp/v` returns a working binary.
2. `https://try.ayiru.dev/v1/query/ask` responds with a real answer.
3. GitHub repo card shows description, topics, social preview image.
4. Issue templates render correctly on the "New Issue" page.
5. `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `FUNDING.yml` all present.

### Phase C done when:
1. PyPI release tagged and visible.
2. Show HN post is live; you are responding to comments.
3. ≥ 1 GitHub issue or discussion thread from a stranger within 48 hours.
4. ≥ 50 GitHub stars within 7 days (realistic mid-tier signal — adjust per your audience).

### Phase D ongoing — health checks:
- Weekly: issue response time < 48h, no PRs older than 7 days untriaged.
- Monthly: ≥ 1 new contributor, ≥ 1 community-added tool, hosted demo uptime > 99%.

---

## One Thing to Do First (revised per Council)

**This week, before any code in `backend/app/`: run the Phase 0.1 agent measurement spike.**

One LangChain or Claude Agent SDK agent. Two runs (web-search-only baseline, then web-search + fake `ask`). Ten realistic dev tasks. 100 tool-call turns total.

The number that comes out — *what fraction of agent token cost is web-search-replaceable lookups, and how often does the LLM actually pick `ask` when both are listed* — is the only number that determines whether this roadmap is a product plan or a confident wrong turn. Every line of Phases A through D is downstream of those two numbers.

**If the fraction is ≥ 25% AND the tool-choice rate is ≥ 50%:** ship A1 next Monday. The pivot is real.

**If either is below the threshold:** you just saved yourself five weeks of building the wrong thing. Re-read the Decision Gate; pivot to the actual cost driver (cost-observability dashboard, MCP registry, or whatever P0.1 surfaces) before any v0.2 code lands.

The previous version of this plan said "ship A1 by Friday." That assumed the pivot's premise was true. This revision refuses to assume — it measures. That's the single biggest change a 5-advisor council made to this roadmap, and it's the cheapest thing in the plan to do.

---

## Appendix — Where v0.2.1 Differs from v0.2

For maintainers comparing revisions:

| Area | v0.2 (original) | v0.2.1 (this revision) |
|---|---|---|
| Calendar | 5 weeks total | 9 weeks total (Phase 0 added; Phase A spread 1.8×) |
| Phase 0 | absent | 5 required tasks before any pivot code lands |
| Decision Gate | absent | Explicit go/no-go between Phase 0 and Phase A |
| Cost-savings math | `statement_length × 1.3` (wrong, off by 5–50×) | `_AVERAGE_WEB_SEARCH_TOKENS - response_tokens` with constant for calibration |
| B3 embeddings | Required Week 2 task | Week 5 stretch goal |
| Week 2 scope | B1 + B2 + B3 in 5 days (3× overstuffed) | B1 only (Week 2), B2 only (Week 3) |
| Hosted demo estimate | 4h | 8–12h |
| PyPI name check | Implicit Week 4 task | Explicit Week 0 blocker (P0.2) |
| v0.1 release tag | Never mentioned | P0.3 — required before any v0.2 work |
| Beta-tester recruitment | Hand-waved in Phase D | P0.4 — 5 named testers committed in writing before Phase A |
| Launch budget | absent | Itemised table; ~$50/month floor, $300 over 6 months |
| Kill criteria | absent | Week 8 / Week 10 / Month 3 / Month 6 explicit triggers |
| Named first reviewer | absent | Required pre-launch checklist item |
| Legal pre-check on doc scraping | absent | Required step in B2 (Week 3) |
| "One Thing to Do First" | "Write A1 by Friday" | "Run the Phase 0.1 measurement spike this week" |

Everything not in this table — file paths, architecture, OSS hygiene checklist, launch playbook, sustainability principles — is unchanged from v0.2.
