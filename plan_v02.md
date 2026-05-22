# Ayiru — v0.2 Stage Plan (Build-First, Solo-Dev)

> **Companion to [roadmap_v0.2.md](roadmap_v0.2.md).** That document is the prose / phased / week-based plan with full rationale and council-review history. This document is the **stage-by-stage execution plan** in the same numbered "Stage N — name" cadence the v0.1 codebase already uses (Stages 0 through 14 are shipped). Every stage below is self-contained: goal, preconditions, files, tests, contract impact, definition of done, and what it explicitly defers.
>
> ## Maintainer-driven re-sequencing (2026-05-21 — 2026-05-22)
>
> The maintainer made three load-bearing choices that change which stages are "in scope for v0.2":
>
> 1. **Solo-dev path.** No external beta testers. Stage 16.4 is rewritten to a self-test the maintainer can run alone. `docs/beta_tester_outreach.md` is parked for v0.3+. Gate 1 criterion 3 and Gate 2's "named tester / named reviewer" rows are dropped.
> 2. **Build > publish.** v0.2's scope collapses to the build cycle — making the product *powerful* — and explicitly **defers** PyPI publication, hosted demo, OSS hygiene polish, and the Hacker News launch playbook to a separate **v0.2.5 publish cycle**. Stage 22 splits accordingly: 22.1 (LangChain adapter) stays in v0.2 build; 22.2–22.5 (README pivot / PyPI / Fly.io / hygiene) move to v0.2.5. Stage 23 (launch day) is entirely v0.2.5.
> 3. **Stage 18 promoted ahead of 19/20.** Telemetry has to ship *before* the bulk ingest in Stage 20 so the calibration window starts collecting data from day 1; otherwise the maintainer loses forensic insight into the first thousand `ask` calls forever.
>
> ## Resulting active sequence (v0.2 build cycle)
>
> | # | Stage | Status |
> |---|---|---|
> | 1 | **Stage 15** — credibility close-out | ✅ shipped, committed `fc96bad` |
> | 2 | **Stage 16.5** — launch budget memo | ✅ drafted (`docs/launch_budget.md`), uncommitted, 3 `🛑 DECIDE:` blocks left |
> | 3 | **Stage 17** — `/v1/query/ask` endpoint + 7th MCP tool + CLI | ✅ shipped, uncommitted, includes Stage 17.6 audit-fix substage |
> | 4 | **Stage 18** — `QUERY_SERVED` audit event + `GET /v1/stats/savings` | ⏭ next active |
> | 5 | **Stage 19** — curated / uncurated tool split | precondition for 20 |
> | 6 | **Stage 20** — bulk ingest 50 tools (~5,000 claims) | **the power moment** |
> | 7 | **Stage 21** — `ayiru-client` Python SDK | reach |
> | 8 | **Stage 22.1** — LangChain `AyiruTool` adapter | reach |
> | 9 | Stretch — embeddings / hybrid retrieval, freshness re-ingestion | post-Stage-20 polish |
> | — | **Stages 22.2 – 22.5 + Stage 23** | **DEFERRED to v0.2.5** (publish cycle) |
> | — | **Stages 16.1 – 16.4** | **DEFERRED** (not on the build-cycle critical path; do alongside or after Stage 20) |
>
> ## One gate, not two
>
> Gate 2 (launch-day prerequisites) is irrelevant to the build cycle — there is no launch in v0.2. Only Gate 1 remains, and even Gate 1 is reduced to its self-test criterion since metrics 1+2 need the Phase 0.1 spike which is deferred. Both Gate 1 and Gate 2 are preserved in this document for the eventual v0.2.5 publish cycle; they're just not blocking anything right now.

---

## Stage Numbering

| Range | Owner | Status |
|---|---|---|
| Stages 0–14 | v0.1 (shipped) — see [docs/stage_report.md](docs/stage_report.md) | Tagged `v0.1.0`, wheel built |
| **Stage 15** | v0.1 credibility close-out (from the audit punch list) | ✅ shipped, committed `fc96bad` |
| **Stage 16.5** | Launch budget memo | ✅ drafted (`docs/launch_budget.md`), uncommitted |
| **Stage 17** | `/v1/query/ask` endpoint + MCP + CLI + audit-fix substage | ✅ shipped, uncommitted (721 tests passing) |
| **Stage 18** | Cost-savings telemetry | ⏭ next active |
| **Stages 19–21** | Power build (curated split, bulk ingest, SDK) | in-scope |
| **Stage 22.1** | LangChain adapter | in-scope |
| **Stages 22.2–22.5** | Publish / hosted / hygiene | **DEFERRED to v0.2.5** |
| **Stage 23** | Launch day + sustainability | **DEFERRED to v0.2.5** |
| **Stages 16.1 – 16.4** | Phase 0 spike + PyPI reservation + GitHub release + self-test | **DEFERRED** (off the critical path) |
| Stages 24+ | Reserved for v0.3 (L4 cross-agent verification, embeddings hardening, hosted SaaS) | n/a |

---

## Decision Gates

### Gate 1 — measurement gate (between Stage 18 and Stage 19, in build-cycle terms)

Originally a 3-criterion gate that required (1) ≥ 25% web-search-replaceable token cost, (2) ≥ 50% tool-choice rate for a fake `ask`, (3) 5 named beta testers. The solo-dev + build-first re-sequencing turns this into a softer checkpoint:

| # | Threshold | Status under build-first | If false |
|---|---|---|---|
| 1 | ≥ 25% of agent token cost is web-search-replaceable lookups (P0.1 metric 1) | **deferred** — the spike is gated on API spend; not blocking the build | Re-evaluate before any v0.2.5 publish work. If the spike eventually shows < 25%, the publish cycle is wrong. |
| 2 | LLM picks the fake `ask` over `web_search` in ≥ 50% of opportunities (P0.1 metric 2) | **deferred** — same reason | Same as #1. |
| 3 | **Solo self-test**: ≥ 7 of 10 realistic dev questions produce a verdict the maintainer would accept as useful. | **active**; re-run after Stage 20 against the bulk-ingest graph | Stage 20 didn't add enough useful coverage. Audit `tools/v0.2_seed.yml` for the gaps; expand the ingest list; re-run. |

In the build cycle, only criterion 3 is enforced — and it's evaluated *after* Stage 20, not before Stage 17 (since Stage 17 already shipped against the v0.1 graph and works on the headline questions).

### Gate 2 — launch-day prerequisites (DEFERRED to v0.2.5)

Preserved for the eventual v0.2.5 publish cycle. Originally 5 criteria; collapsed to 4 under solo-dev path. **Not enforced in v0.2 build cycle** because no launch is happening.

All four must be true at 07:00 of the launch day, *if and when v0.2.5 is scheduled*:

1. Clean-venv `pip install ayiru` smoke completed in the last 24 hours.
2. The hosted demo (`try.ayiru.dev` or fallback) responded to a real `ask` call in the last hour.
3. The maintainer's self-test (Gate 1 criterion 3) has been re-run against the full v0.2 graph in the last 24 hours and still passes 7/10.
4. README, CHANGELOG, and the hosted-demo "what to type" example all install-and-paste cleanly on the maintainer's secondary machine.

If any is false at v0.2.5 time, delay one week.

---

# Stage 15 — v0.1 Credibility Close-out ✅ SHIPPED (committed `fc96bad`)

**Goal.** Resolve the 11-item brutal-audit punch list so v0.1.0 is *defensible* under a senior-engineer eval. Stage 15 shipped no new features — it paid down the credibility debt the audit found between the README's claims and the codebase's reality.

**Why this was a stage, not a footnote.** A senior reviewer clicking the "Stage 14 complete" badge needs to land on a doc that actually covers Stages 9–14. If the audit punch list isn't closed, every downstream stage starts with an unpaid trust deficit.

**Outcome.** All 11 substages closed. Test suite 693 → 699 passing. Ruff clean. Migration roundtrip clean. Committed 2026-05-21 in `fc96bad "Credibility Close-out"`.

| # | Title | Outcome |
|---|---|---|
| 15.1 | Seed publishes canonical ToolSpecs ✓ DONE 2026-05-20 | 47 claims, 6 accepted, 4 published ToolSpecs |
| 15.2 | README headline output matches reality ✓ DONE 2026-05-20 | Confidence 1.00, band=strong, no "informational" caveat |
| 15.3 | Stage report covers Stages 9–14 ✓ DONE 2026-05-21 | Opening sentence + Stage 15 section + audit-pointer note |
| 15.4 | PyPI install ambiguity ✓ DONE 2026-05-21 | **Option B chosen** — README explicitly states PyPI ships with v0.2 |
| 15.5 | Docker image first-run experience ✓ DONE 2026-05-21 | `ayiru serve --auto-seed` + idempotent skip when DB has claims |
| 15.6 | pytest CVE-2025-71176 bump ✓ DONE 2026-05-21 | `pytest>=8.5,<10.0`; pip-audit clean |
| 15.7 | Python version consistency ✓ DONE 2026-05-21 | **3.12 floor chosen**; Dockerfile + CI matrix + ruff aligned |
| 15.8 | MCP-stdio auth disclosure ✓ DONE 2026-05-21 | README Security section + SECURITY.md residual risks + stderr warning |
| 15.9 | DNS rebinding mitigation | **Deferred to v0.3** (acknowledged in SECURITY.md) |
| 15.10 | `.gitignore` housekeeping ✓ DONE 2026-05-21 | `.coverage`, `htmlcov/` ignored |
| 15.11 | Naming convention docstrings ✓ DONE 2026-05-21 | `*Record` vs bare-name pattern documented |

For the per-substage detail, see [docs/stage_report.md](docs/stage_report.md) §Stage 15.

---

# Stage 16 — Phase 0 Pre-flight (mostly DEFERRED)

**Original goal.** Validate the v0.2 pivot's central assumption (*"agents waste meaningful tokens on web searches that a local knowledge layer can serve"*) before writing any `backend/app/` code. Plus tee up the four other pre-flight items.

**Build-cycle reality.** Build > publish means the spike + the pre-launch artifacts aren't on the critical path. Stage 17 has already shipped; the build path continues to Stage 18 next. Stage 16 substages are individually deferred or done as below:

| Substage | Status | Notes |
|---|---|---|
| **16.1** Phase 0 measurement spike (P0.1) | **DEFERRED** | `scripts/phase0_measurement_spike.py` is ready to run. Requires Anthropic key + ~$5–20 spend. Run before any v0.2.5 publish work to validate the pitch. |
| **16.2** PyPI name reservation (P0.2) | **DEFERRED** to v0.2.5 | Per Stage 15.4 Option B — PyPI publication ships with the v0.2.5 publish cycle, not v0.2 build. |
| **16.3** v0.1.0 release artifacts (P0.3) | **DEFERRED** (optional) | Mechanical: `gh release create v0.1.0 backend/dist/*` when ready. Not blocking the build. |
| **16.4** Solo self-test (replaces beta-tester recruitment) | **DEFERRED** until after Stage 20 | Re-run against the post-bulk-ingest graph; that's when the test has signal. Pre-Stage-20 the seed is too thin to give a useful PASS/FAIL distribution. |
| **16.5** Launch budget memo (P0.5) | ✅ **DRAFTED** (uncommitted) | `docs/launch_budget.md` exists with itemised cost table; 3 `🛑 DECIDE:` blocks waiting on the maintainer: funding source, 6-month kill date, hosted-demo-or-skip. |

For the original detailed work breakdown of each 16.x substage, see the git history of this file (commit `a9a60e7`) or pull from `roadmap_v0.2.md §Phase 0`. The deferral is recorded here so a future scheduler knows the work exists; the detail is preserved upstream.

---

# Stage 17 — `/v1/query/ask` Endpoint ✅ SHIPPED (uncommitted)

**Goal.** Add the headline v0.2 endpoint: agents call `ask(question)` and receive cited, ranked answers from the verified knowledge graph. Pure lexical ranking (no embeddings yet; B3 from roadmap_v0.2.md is the stretch-goal at the end of v0.2).

**Outcome.** Five substages shipped + a sixth audit-fixes substage shipped same day. Test suite 699 → 721 passing. Ruff clean. End-to-end smoke against the v0.1 graph confirms the 6 headline dev questions hit (rank, statement, citations, exit codes) correctly.

## Stage 17.1 — Pydantic schemas ✓ DONE 2026-05-21

Three models added to [backend/app/schemas/query.py](backend/app/schemas/query.py):
- `AskRequest` `{question (1–512), limit (1–20 default 5), tool_id_hint}` — pattern-validated.
- `Answer` — projected from a `KnowledgeClaim` with `confidence`, `verification_level`, `evidence`, `match_reason`.
- `AskResponse` `{question, answers, fallback_recommended, estimated_tokens_saved, generated_at}` — same shape on hit OR miss.

## Stage 17.2 — `QueryEngine.ask()` ✓ DONE 2026-05-21

In [backend/app/services/query_engine.py](backend/app/services/query_engine.py). Token-overlap ranking, weights: subject ×3, tool_id ×2, statement ×1. Threshold `_ASK_SCORE_THRESHOLD = 0.30` for fallback. ACCEPTED-only filter. Batch-fetches verification levels for top-N candidates only (one extra query regardless of fan-out).

## Stage 17.3 — `POST /v1/query/ask` route ✓ DONE 2026-05-21

In [backend/app/api/routes_query.py](backend/app/api/routes_query.py). Read endpoint — no auth required even with `AYIRU_API_KEY` set. Malformed body → structured 422.

## Stage 17.4 — 7th MCP tool (`ask`) ✓ DONE 2026-05-21

In [backend/app/mcp_server/tools.py](backend/app/mcp_server/tools.py). Registered as the **first** tool in the registry so LLMs see it at position #1 (tool-choice is order-sensitive). Description written for LLM consumption: *"Look up a verified, cited answer from the local knowledge graph before invoking web search."*

## Stage 17.5 — Tests + end-to-end smoke ✓ DONE 2026-05-21

[backend/tests/test_query_ask.py](backend/tests/test_query_ask.py) — 14 tests covering happy path, ranking, fallback, accept-only filter, tool_id_hint, telemetry. End-to-end smoke run against real seeded DB: 4/4 headline questions return the right top hit.

## Stage 17.6 — Audit fixes (second-pass senior-dev review) ✓ DONE 2026-05-22

A senior-dev audit on 2026-05-22 found 5 bugs and a code smell. All fixed same day.

| # | Severity | Bug | Fix |
|---|---|---|---|
| 17.6.1 | 🔴 critical | `_MIN_TOKEN_LENGTH = 3` dropped half the Unix CLI vocabulary (`rm`, `ls`, `cd`, `gh`, `jq`, `mv`, `cp`…) — the headline pitch breaks for any 2-char command | Removed the length filter; stop-words already drop short noise particles |
| 17.6.2 | 🔴 critical | Repeated keywords (`"docker docker docker"`) inflated the score by ~70% — gameable, non-deterministic for verbose LLMs | Dedupe question tokens before scoring + normalising |
| 17.6.3 | 🔴 critical | README API Surface didn't list `/v1/query/ask`; MCP table didn't list `ask` | Added bullet at top of Agent Query Surface; added `ask` as row 1 in MCP table |
| 17.6.4 | 🟠 high | No `ayiru ask` CLI subcommand — install-and-try users couldn't hit the headline endpoint without curl | Added `ayiru ask "question"` with `--limit`, `--tool`, `--json` flags + `_print_ask_response` + 4 tests |
| 17.6.5 | 🟡 medium | `PACKAGE_VERSION = "0.1.0"` hardcoded in `cli.py` — bumping pyproject silently drifts | Derive from `importlib.metadata.version("ayiru")` at startup; verified by bumping pyproject 0.1.0 → 0.1.99 → CLI tracks |
| smell-17.6 | 🟡 medium | Lazy `import re` inside `_tokenize_question` | Moved to module top |

Three regression tests added to lock the fixes: `test_short_command_tokens_survive_tokenization`, `test_repeated_keywords_do_not_inflate_score`, `test_repeated_single_token_question_scores_same_as_one`. All Bug 1, 2, 4, 11 reverified independently with concrete behavioral probes (not just "tests pass").

## Stage 17 — Definition of Done ✅

- `POST /v1/query/ask` returns non-empty answers for the 6 headline dev questions.
- `ask` is the 1st MCP tool, MCP 2024-11-05 spec-compliant.
- `ayiru ask` CLI subcommand with proper exit codes (0=hit, 1=fallback — distinct from `ayiru query`'s 0/2).
- 17 new test_query_ask.py tests + 4 new ask CLI tests + 3 audit-regression tests + 2 MCP ordering tests = 22 new total. Backend tests 699 → 721 passing.
- Ruff clean. Wheel still builds. Migration roundtrip clean.

**Estimated effort (actual).** Stage 17 + audit fixes shipped in one focused day. Faster than the original 3–4 day estimate because Claude pair-built each substage rather than the plan's solo-from-spec sequencing.

**Deferred to Stage 22.1 stretch / v0.3.** Embeddings hybrid retrieval. Per-IP rate limiting. Multi-language stop-words. `ask` response caching.

---

# Stage 18 — Cost-Savings Telemetry ⏭ NEXT ACTIVE

**Goal.** Make the cost savings observable. Every `ask` emits an audit event; a new aggregated endpoint exposes "X tokens saved this month." This is the moat-as-data: agents using Ayiru can prove the savings. Equally important: telemetry has to ship *before* Stage 20's bulk ingest so the calibration window starts collecting data from the first `ask` call.

**Preconditions.** Stage 17 closed ✓.

## Stage 18.1 — `QUERY_SERVED` audit event type

**Migration.**
- New file: `backend/alembic/versions/0016_add_query_served_event_type.py`.
- Mirror file: `backend/app/_alembic/versions/0016_add_query_served_event_type.py` (Stage 14 lockstep contract).
- DDL: extend the CHECK constraint on `audit_events.event_type` to include `QUERY_SERVED`.
- Pattern to follow: any of the existing `0010`/`0011`/`0012` extend-checks-for-X migrations (~30–50 lines each). Copy the structure.

**Enum.**
- [backend/app/schemas/enums.py](backend/app/schemas/enums.py) — add `QUERY_SERVED = "query_served"` to `AuditEventType`.

**Tests.**
- [backend/tests/test_alembic_metadata_alignment.py](backend/tests/test_alembic_metadata_alignment.py) — should auto-pass; the drift-locked check picks up the new constraint value.
- [backend/tests/test_audit_log.py](backend/tests/test_audit_log.py) — add `test_query_served_event_persists` round-trip.

## Stage 18.2 — Emit `QUERY_SERVED` from inside `ask`

**Files touched.**
- [backend/app/api/routes_query.py](backend/app/api/routes_query.py) — after a successful `ask`, call `_add_audit_event` from [claim_store.py](backend/app/services/claim_store.py) with `event_type=QUERY_SERVED` and details:
  ```python
  {
    "question_length": len(question),
    "answers_returned": len(answers),
    "tokens_saved": estimated_tokens_saved,
    "top_claim_id": answers[0].claim_id if answers else None,
    "fallback_recommended": fallback_recommended,
    "request_id": current_request_id(),  # from observability.py
  }
  ```
- Avoid logging the raw `question` text — for v0.2 we don't want freeform PII / proprietary code snippets in the audit log. Length + the matched claim_id are enough to debug a session.

## Stage 18.3 — `_AVERAGE_WEB_SEARCH_TOKENS` constant

**Status.** Already shipped in Stage 17.2 as `_AVERAGE_WEB_SEARCH_TOKENS = 830` in `query_engine.py`. Comment in the code points at the post-launch calibration step.

**Calibration sub-stage (after Stage 20 + 1k real queries).** Recompute observed mean `response_tokens` from the audit log; adjust the constant. This becomes a measured number, not a guess. The audit memo at [docs/stage_report.md](docs/stage_report.md) §Stage 15 calls the current value out as "fiction until Stage 18 calibrates it."

## Stage 18.4 — `GET /v1/stats/savings`

**Files touched.**
- [backend/app/api/routes_query.py](backend/app/api/routes_query.py) — new route. Reads `audit_events` filtered to `event_type=QUERY_SERVED`, aggregates `details.tokens_saved`, returns:
  ```json
  {
    "total_queries_served": 1234,
    "total_tokens_saved": 838720,
    "estimated_usd_saved": 11.42,
    "window_start": "2026-05-01T00:00:00Z",
    "window_end": "2026-05-20T18:00:00Z",
    "by_tool": {"docker": 312, "git": 287, "github-cli": 245, ...}
  }
  ```
- `_USD_PER_MILLION_INPUT_TOKENS` constant (default $3, Anthropic Claude Sonnet input rate). Configurable via env var `AYIRU_PRICE_PER_MTOK_INPUT` for projects on other models.
- Optional query params: `window=24h|7d|30d|all`, `api_key=...` (filter to one caller). When `AYIRU_API_KEY` is set, any caller can read the aggregate, but only callers with the API key can filter by `api_key`.

## Stage 18.5 — Tests

- [backend/tests/test_query_ask.py](backend/tests/test_query_ask.py) — extend: assert `QUERY_SERVED` audit event is emitted per `ask`.
- [backend/tests/test_savings_endpoint.py](backend/tests/test_savings_endpoint.py) — new file. Tests: aggregation correctness, window filtering, USD calculation, empty-graph response.

## Stage 18 — Definition of Done

- Every `ask` call appends one `QUERY_SERVED` row to `audit_events`.
- `GET /v1/stats/savings` returns a structured aggregate.
- Migration `0016` applied; alembic drift test passes.
- ~730 tests passing. Ruff clean. Coverage ≥ 88%.

**Estimated effort.** 2–3 days.

**Defers.** Per-API-key telemetry dashboard UI (v0.2.5). Multi-currency conversion (USD only for v0.2). Refining the heuristic against measured data (post-Stage-20 calibration sub-stage).

---

# Stage 19 — Curated vs Uncurated Tool Split

**Goal.** Relax the Stage 0 tool lock so the graph can hold the 5,000+ uncurated claims Stage 20 is about to add. Curated tools keep the full orchestrator path (claims → accepted → spec → validate_command). Uncurated tools land at `L0_unverified` / `pending` and are visible to `ask` but excluded from `validate_command`.

**Preconditions.** Stage 18 closed.

## Stage 19.1 — Contract version bump

**Files touched.**
- New file: [contracts/ayiru_stage_0.v2.json](contracts/ayiru_stage_0.v2.json).
- Mirror file: [backend/app/contracts/ayiru_stage_0.v2.json](backend/app/contracts/ayiru_stage_0.v2.json) (Stage 14 lockstep).
- Schema: add `"curated": true` to each of the existing 5 entries (git, github-cli, docker, vercel-cli, openai-api). Bump `"version": 2`.

**Tests.**
- [backend/tests/test_bundled_contracts_in_sync.py](backend/tests/test_bundled_contracts_in_sync.py) — should auto-pass.
- New: [backend/tests/test_contract_v2_schema.py](backend/tests/test_contract_v2_schema.py) — validates v2 has the `curated` field and v1 doesn't (old contract preserved for replay).

## Stage 19.2 — Relax `_stage_0_tool_ids()`

**Files touched.**
- [backend/app/services/claim_store.py:60-74](backend/app/services/claim_store.py#L60-L74) — split:
  ```python
  def _curated_tool_ids() -> frozenset[str]:
      """Tools that get the full orchestrator path."""
      ...

  def _all_known_tool_ids() -> frozenset[str]:
      """Tools recognized at all (curated + uncurated)."""
      ...
  ```
- The `create()` method in `ClaimStore` no longer raises `ToolNotAllowedError` for unknown tools unless `AYIRU_STRICT_TOOL_LOCK=1`. Default behavior: persist at `verification_level=L0_UNVERIFIED`, `verification_status=PENDING`.
- The `ToolSpecCompiler` (Stage 6) still requires `accepted` claims, so uncurated tools can't accidentally publish specs.

## Stage 19.3 — Matcher behavior for uncurated claims

**Files touched.**
- [backend/app/services/command_matcher.py](backend/app/services/command_matcher.py) — extend the existing exclusion logic. `validate_command` already filters to `verification_status='accepted'`; confirm + lock.
- [backend/app/services/query_engine.py](backend/app/services/query_engine.py) — `ask()` includes uncurated claims at `L0_unverified` but flags them in `match_reason`. `validate_command` does not.

## Stage 19.4 — Strict-mode flag

**Files touched.**
- [backend/app/services/claim_store.py](backend/app/services/claim_store.py) — read `os.environ.get("AYIRU_STRICT_TOOL_LOCK")`. When truthy, `create()` raises `ToolNotAllowedError` for non-curated tools as today.
- [README.md](README.md) — document the flag in the env-var table.

## Stage 19.5 — Tests

- [backend/tests/test_uncurated_claims.py](backend/tests/test_uncurated_claims.py) — new file:
  - Uncurated claim persists at `L0`.
  - `ask` returns uncurated claims with a "low confidence" reason in match_reason.
  - `validate_command` excludes uncurated claims (default-deny).
  - `AYIRU_STRICT_TOOL_LOCK=1` restores Stage 0 lock behavior.

## Stage 19 — Definition of Done

- Uncurated claims persist and surface in `ask`.
- Curated tools still get the full Stage 6 pipeline (Stage 0 → 14 behavior unchanged).
- ~740 tests passing.

**Estimated effort.** 2 days.

**Defers.** A reviewer UI for promoting uncurated claims (continues to use the existing `POST /verification/human-review` endpoint). Per-tool ingestion rate limits.

---

# Stage 20 — Bulk Ingestion Harness — **THE POWER MOMENT**

**Goal.** Populate the graph from ~47 claims (Stage 15.1 end state) to ≥ 5,000 claims across ≥ 50 tools. Without this, the `ask` endpoint is a toy. With this, the v0.2 pitch becomes real: an agent asking *"how do I configure terraform state in s3"* gets a verified answer instead of paying for web_search.

**Preconditions.** Stage 19 closed (so uncurated claims can land without orchestrator rejection).

## Stage 20.1 — `ayiru ingest` CLI subcommand

**Files touched.**
- [backend/app/cli.py](backend/app/cli.py) — new `ingest` subparser:
  ```
  ayiru ingest --source docs --tool-list path/to/tools.yml [--force] [--resume]
  ```
- Wires to `DocsIngestionService.ingest_all_for_tool()` (already exists at [backend/app/services/docs_ingestion.py:285-294](backend/app/services/docs_ingestion.py#L285-L294)).

## Stage 20.2 — `tools.yml` schema + initial 50-tool list

**Files touched.**
- New file: `tools/v0.2_seed.yml`. Format:
  ```yaml
  version: 1
  tools:
    - tool_id: aws-cli
      official_hosts: [docs.aws.amazon.com]
      urls:
        - https://docs.aws.amazon.com/cli/latest/reference/index.html
      claim_type_default: cli_command_exists
    - tool_id: kubectl
      ...
  ```
- Target list (from roadmap_v0.2.md B2): aws-cli, kubectl, terraform, helm, ansible, npm, pip, cargo, go, rust, postgresql-cli, mysql-cli, redis-cli, mongodb, sqlite3, curl, wget, jq, yq, ssh, rsync, tmux, vim, sed, awk, ffmpeg, imagemagick, openssl, gpg, htop, ps, lsof, netstat, dig, nmap, systemctl, journalctl, brew, apt, dnf, supabase-cli, fly, railway, heroku, kubernetes-cli, and the 5 already-curated tools.

## Stage 20.3 — Per-tool legal pre-check

For each of the 50 tools, document the docs license in `tools/v0.2_seed_licenses.md`:
- GitHub docs: CC BY 4.0 ✓
- Docker docs: Apache 2.0 ✓
- Stripe API reference: © Stripe Inc — drop from v0.2 list (re-evaluate post-launch).
- For tools with unclear licenses: drop. Better to ship 35 tools cleanly than 50 tools with one DMCA-risk surface.

## Stage 20.4 — Resume + force flags

**Files touched.**
- [backend/app/cli.py](backend/app/cli.py) — track per-URL ingestion state by querying `audit_events` filtered to `INGESTION_RUN_COMPLETED` (use the existing audit event type from Stage 13). Skip URLs already ingested unless `--force`.

## Stage 20.5 — Trust contract updates

**Files touched.**
- [contracts/tool_trust_sources.v1.json](contracts/tool_trust_sources.v1.json) — add `official_hosts` and `source_repositories` for the new 45 tools. Bump to v2 if any breaking change (none expected).
- [contracts/docs_ingestion_sources.v1.json](contracts/docs_ingestion_sources.v1.json) — extend the SSRF allowlist for the new docs hosts.
- Mirror files at [backend/app/contracts/](backend/app/contracts/) (lockstep).

## Stage 20.6 — JS-rendered docs handling

**Realism note.** Some docs sites (Vercel's, parts of AWS) are SPA-rendered; httpx alone won't fetch the content. For v0.2 build, drop those tools from `tools/v0.2_seed.yml` and queue a Playwright-based fetcher for v0.3. Document the dropped tools in `tools/v0.2_seed_dropped.md`.

## Stage 20.7 — Rate limit + ToS compliance

For each docs host: honor `robots.txt`, set `User-Agent: Ayiru-Bulk-Ingestion/1.0 (+https://github.com/ruth411/ayiru)`, cap to 1 req / sec per host.

**Files touched.**
- [backend/app/services/docs_ingestion.py](backend/app/services/docs_ingestion.py) — extend the existing client with a `httpx.Limits` configuration.

## Stage 20.8 — Run the bulk ingest

After 20.1–20.7 land:
```bash
ayiru ingest --source docs --tool-list tools/v0.2_seed.yml
```
Expected: 50 tools × ~100 claims/tool = ~5,000 claims. Realistic after license-drops: 35–45 tools × ~80 claims = 2,800–3,600 claims. Either number is the v0.2 power moment.

## Stage 20.9 — Re-run the solo self-test (Gate 1 criterion 3)

After Stage 20.8 finishes, run Stage 16.4's self-test against the bulk-ingest graph. Record in `docs/self_test_results.md`. **≥ 7/10 PASS** → Gate 1 criterion 3 cleared; v0.2 build cycle is on track. **< 7/10 PASS** → expand the ingest list or fix the tokenizer / matcher gaps the failures expose.

## Stage 20 — Definition of Done

- `tools/v0.2_seed.yml` committed with 35–50 entries.
- `tools/v0.2_seed_licenses.md` committed with per-tool license review.
- `ayiru ingest` runs end-to-end against the file, populates ≥ 2,800 claims.
- Audit events log every ingestion run.
- `docs/self_test_results.md` committed with ≥ 7/10 PASS.
- Stage 18 calibration sub-stage triggered: recompute `_AVERAGE_WEB_SEARCH_TOKENS` from the first 1k post-Stage-20 `QUERY_SERVED` audit events.

**Estimated effort.** 5–8 days. Realistic: 4 days CLI/contract work + 2–3 days legal review + 1 day rate-limited crawl run.

**Defers.** Playwright lane (v0.3). Per-tool freshness re-ingestion schedule (v0.2 stretch — see below). OpenAPI / GraphQL bulk variants (only docs lane for v0.2; existing one-off lanes still work).

---

# Stage 21 — Python Client SDK

**Goal.** A drop-in `ayiru_client` package so an agent dev's code goes from "wire raw httpx" to two lines.

**Preconditions.** Stage 20 closed (the bulk graph exists so the SDK has something to query).

## Stage 21.1 — Package skeleton

**Files touched.**
- New directory: [clients/python/](clients/python/) (sibling to `backend/`).
- New file: `clients/python/pyproject.toml`:
  ```toml
  [project]
  name = "ayiru-client"
  version = "0.2.0"
  dependencies = ["httpx>=0.27,<1.0", "pydantic>=2.8,<3.0"]
  ```
- New module: `clients/python/ayiru_client/__init__.py`.

## Stage 21.2 — Sync + async API

**Public surface (sync):**
```python
from ayiru_client import Ayiru
atlas = Ayiru(base_url="http://localhost:8000", api_key=None)
answer = atlas.ask("how do I delete a docker volume")
if answer.is_useful:
    return answer.statement
```

**Public surface (async):**
```python
from ayiru_client import AsyncAyiru
atlas = AsyncAyiru(base_url="http://localhost:8000")
answer = await atlas.ask("...")
```

**Methods:** `ask`, `validate_command`, `get_tool_spec`, `search_tools`, `savings`.

**Computed properties on `Answer`:**
- `is_useful` — `confidence >= 0.6 and verification_level != L0_UNVERIFIED`.
- `tokens_saved_estimate` — pulled from the server response.

## Stage 21.3 — Tests

[clients/python/tests/test_client.py](clients/python/tests/test_client.py) — runs against a FastAPI `TestClient`-wrapped backend (no network). Same hermeticity contract as backend tests.

## Stage 21.4 — Documentation

- `clients/python/README.md` — quickstart, full method reference, examples.
- Cross-link from the main repo README.

## Stage 21 — Definition of Done

- `pip install -e clients/python` works.
- Documented examples run end-to-end against a locally-running backend.
- ~760 tests total (backend ~740 + client ~20).

**Estimated effort.** 2 days.

**Defers.** PyPI publication of `ayiru-client` — that's v0.2.5 (Stage 22.3). TypeScript / JS client (v0.3). Streaming `ask` responses (no use case yet).

---

# Stage 22 — Reach + Publish (split between v0.2 build and v0.2.5)

**Goal.** Make Ayiru reachable to LangChain users with zero glue code (in v0.2 build) and prepare the public-launch surface (in v0.2.5 publish).

## Stage 22.1 — LangChain `AyiruTool` (v0.2 build, in-scope)

**Preconditions.** Stage 21 closed.

**Files touched.**
- New file: `clients/python/ayiru_client/langchain.py`.
- Class: `AyiruTool(BaseTool)` subclassing `langchain_core.tools.BaseTool`.
- Critical: the docstring is what the LLM sees as the tool description. Should explicitly say *"use this before invoking web search for common dev questions about CLIs, APIs, and tools."* Same framing as the MCP tool description from Stage 17.4.
- `clients/python/examples/langchain_demo.ipynb` — 10-question notebook. Show ~7 hits + ~3 fallbacks with cost-saved counter at the end.

**Definition of done.** LangChain demo notebook runs end-to-end against a locally-running backend, prints a "saved $X.XX" footer derived from the `GET /v1/stats/savings` endpoint.

**Estimated effort.** 1–2 days.

## Stage 22.2 — README pivot ⏸ **DEFERRED to v0.2.5**

Originally in-scope; deferred because the maintainer's *build > publish* directive means we're not running a launch cycle yet. The hero rewrite (drop "Wikipedia for AI agents" framing, swap to "agent search box that cuts API costs") lands when v0.2.5 actually publishes. Until then, the v0.1 README is the documented surface — internally consistent thanks to Stage 15.4 + 15.8.

## Stage 22.3 — PyPI publication ⏸ **DEFERRED to v0.2.5**

`twine upload backend/dist/ayiru-0.2.0*` and the same for `ayiru-client`. Gated on the maintainer's PyPI token + a clean-venv smoke test. Not part of the build cycle.

## Stage 22.4 — Hosted demo at `try.ayiru.dev` ⏸ **DEFERRED to v0.2.5**

Fly.io deploy, custom domain, TLS, persistent SQLite volume, rate-limiting. Council-revised estimate is 8–12 hours when it does land. Skipped in v0.2 build because the cost (~$15–35/month per `docs/launch_budget.md`) doesn't pay until users exist.

## Stage 22.5 — OSS hygiene ⏸ **DEFERRED to v0.2.5**

CHANGELOG.md v0.2 entry, CODE_OF_CONDUCT.md, issue + PR templates, FUNDING.yml, GitHub Discussions enable, social preview image, Star History badge, repo-wide replace of `ruth411` placeholders. All low-individual-cost; bundled with the launch cycle so they hit at once.

---

# Stage 22 stretch — Embeddings / hybrid retrieval (v0.2 build, stretch goal)

**Goal.** Replace lexical-only `ask()` with hybrid lexical + semantic ranking so paraphrased / synonymous queries surface the right claim. The B3 stretch from `roadmap_v0.2.md`.

**Preconditions.** Stage 20 closed (bulk graph populated). Optional — only run this if there's slack after the core build is done.

## Stage 22-stretch.1 — Embedding column + migration

- New table column: `claim_embeddings.claim_id → vector(384)`.
- Migration `0017_create_claim_embeddings.py` (+ lockstep mirror).
- Deps: `sentence-transformers>=3.0,<4.0`, `sqlite-vec>=0.1,<1.0`.

## Stage 22-stretch.2 — Background embed job

- At ingestion time, compute the `all-MiniLM-L6-v2` embedding (80MB, CPU-only, ~10ms/query) for every new claim's `statement`.
- Add a backfill script: `ayiru reindex` regenerates embeddings; safe to re-run.

## Stage 22-stretch.3 — Hybrid `ask()`

- Rewrite `QueryEngine.ask` as: lexical candidate pool (top 50 by token-overlap) → re-rank by cosine similarity to question embedding → return top K.
- Tests: `backend/tests/test_hybrid_retrieval.py` — pin the property that semantic-only matches (synonyms, paraphrases) surface, not just keyword overlap.

**Definition of done.** A question like *"how do I free up disk space from container layers"* matches the `docker system prune` claim even when no keyword overlaps.

**Estimated effort.** 2–3 days.

**If skipped.** v0.2 build ships lexical-only. Self-test (Stage 20.9) is the indicator: if the self-test passes 7/10 on lexical, the embedding stretch can wait until v0.3.

---

# Stage 22 stretch — Per-claim freshness re-ingestion (v0.2 build, stretch goal)

**Goal.** Re-ingest each tool's docs on a schedule so stale claims don't accumulate.

**Preconditions.** Stage 20 closed.

**Files touched.**
- [backend/app/cli.py](backend/app/cli.py) — `ayiru ingest --refresh-stale --older-than 30d`.
- New `audit_events` query: find URLs whose last `INGESTION_RUN_COMPLETED` is older than the threshold; re-ingest them.

**Definition of done.** Cron-scheduled `ayiru ingest --refresh-stale` keeps the graph current without manual intervention.

**Estimated effort.** 1 day.

---

# Stage 23 — Launch day + sustainability ⏸ **DEFERRED to v0.2.5**

Entirely a publish-cycle concern. The original plan's launch-day timeline (08:00 final smoke → 09:00 blog post → 10:00 X thread → 11:00 r/LocalLLaMA → 13:00 r/LangChain → 15:00 Show HN → 18:00 Discord cross-posts) runs when v0.2.5 ships, not before.

Kill criteria from the original plan are preserved for v0.2.5 evaluation (Week 8 stars, Week 10 usage, Month 3 paying customers, Month 6 MAU). The Stage 23.3 *Solo-dev addendum* about Week-8 / Week-10 being the only feedback loop (no pre-launch testers to catch problems) still applies.

For full detail of the deferred Stage 23 substages, see commit `a9a60e7` in this file's git history or `roadmap_v0.2.md §Phase C–D`.

---

# Cross-Cutting Concerns

These apply to every stage above. Codify here so each stage's checklist stays short.

## CC.1 — Contract version + lockstep mirror

Every contract JSON change must:
- Land in [contracts/](contracts/) (source of truth).
- Mirror byte-identically into [backend/app/contracts/](backend/app/contracts/).
- Increment the version filename if behavior changes (`*.v1.json` → `*.v2.json`). Old version stays for replay.
- Be covered by [backend/tests/test_bundled_contracts_in_sync.py](backend/tests/test_bundled_contracts_in_sync.py).

## CC.2 — Seed artifact lockstep

Same lockstep contract for [data/seed_artifacts/](data/seed_artifacts/) ↔ [backend/app/seed_data/artifacts/](backend/app/seed_data/artifacts/). Enforced by [backend/tests/test_bundled_seed_in_sync.py](backend/tests/test_bundled_seed_in_sync.py). Stage 15.1 caught this contract; both locations must move together.

## CC.3 — Alembic migration lockstep

Every migration must:
- Land in `backend/alembic/versions/`.
- Mirror into `backend/app/_alembic/versions/`.
- Have a reversible `downgrade()` unless explicitly justified.
- Be covered by [backend/tests/test_alembic_metadata_alignment.py](backend/tests/test_alembic_metadata_alignment.py).
- Survive `alembic upgrade head && alembic downgrade base && alembic upgrade head` cleanly.

## CC.4 — Observability

- Every new HTTP endpoint emits a request id via the existing `RequestObservabilityMiddleware`.
- Every new write operation emits an audit event via `_add_audit_event()` in `claim_store.py`.
- Structured logs use the existing JSON formatter — no `print()` calls in `backend/app/`.

## CC.5 — Test hygiene

- Tests must be hermetic — no real network, no real subprocess (use the Protocol-based DI pattern documented in [README.md](README.md) §Architecture).
- New ingestion lanes / write paths must include adversarial tests: SSRF redirects, oversized responses, content-type bypasses, malformed inputs, structured 422s.
- No `@pytest.mark.skip` / `@pytest.mark.xfail` for known failures. Either fix or delete.

## CC.6 — Documentation

- Public-facing endpoint changes update [README.md](README.md) `§API Surface`.
- New CLI subcommands update the README CLI reference table.
- New env vars get a row in the README configuration table (or `docs/operations/env_vars.md` once that file is created).
- Every stage's close-out adds a section to [docs/stage_report.md](docs/stage_report.md).

## CC.7 — Backward compat

- v0.1 API (`/v1/query/validate-command`, the 6 existing MCP tools) is **frozen**. v0.2 only adds.
- Legacy unversioned routes (`/query/...` without `/v1` prefix) keep working until v1.0. RFC 8594 deprecation headers stay attached per Stage 14.
- v0.2's new `ask` endpoint (Stage 17) ships on `/v1/query/ask` *and* `/query/ask` (legacy mount) for the same reason.

---

# Estimated Total Effort — build cycle only

| Stage | Work item | Status | Solo-dev focused-days |
|---|---|---|---|
| Stage 15 | Credibility close-out | ✅ shipped | 0 (done) |
| Stage 16.5 | Launch budget memo | ✅ drafted | 0.25 |
| Stage 17 | `ask` endpoint + audit fixes | ✅ shipped | 0 (done) |
| **Stage 18** | Cost-savings telemetry | ⏭ next active | **2–3 days** |
| **Stage 19** | Curated split | active | **2 days** |
| **Stage 20** | Bulk ingest (the power moment) | active | **5–8 days** |
| **Stage 21** | Python SDK | active | **2 days** |
| **Stage 22.1** | LangChain adapter | active | **1–2 days** |
| Stage 22 stretch | Embeddings / freshness re-ingest | optional | 0–4 days |

**Realistic total to v0.2 build complete:** ~2–3 focused weeks (≤ 20 days). Calendar time will be longer because evenings/weekends.

## Deferred to v0.2.5 publish cycle

| Stage | Item | Estimate when scheduled |
|---|---|---|
| 16.1 | Phase 0 measurement spike | 1 day + $5–20 API spend |
| 16.2 | PyPI name reservation | 30 min |
| 16.3 | v0.1.0 GitHub release | 5 min |
| 16.4 | Solo self-test (full Gate 1) | re-runs at Stage 20.9 |
| 22.2 | README pivot | 0.5 day |
| 22.3 | PyPI publication | 1 day |
| 22.4 | Hosted demo at try.ayiru.dev | 1–2 days (Fly.io setup) |
| 22.5 | OSS hygiene (CHANGELOG / CoC / templates / FUNDING / etc.) | 1 day |
| 23.1 | Launch-day playbook | 1 launch day |
| 23.2 | Post-launch first 30 days | ongoing |
| 23.3 | Kill criteria checkpoints | quarterly |

**v0.2.5 total:** ~1 focused week + 1 launch day, *if and when scheduled*.

---

# v0.2 Build-Cycle Definition of Done

v0.2 build is **done** when **all** of:

1. ✅ Stage 15 closed (audit punch list empty; committed `fc96bad`).
2. ✅ Stage 16.5 launch budget memo committed.
3. ✅ Stage 17 closed (`ask` endpoint live; 7th MCP tool; CLI subcommand; 17.6 audit fixes).
4. Stage 18 closed (`QUERY_SERVED` audit + `/v1/stats/savings` live; migration 0016).
5. Stage 19 closed (curated split shipped; uncurated claims allowed at L0; contract v2).
6. Stage 20 closed (≥ 2,800 claims across ≥ 35 tools; self-test 7/10 PASS).
7. Stage 21 closed (`ayiru_client` package installable from source).
8. Stage 22.1 closed (LangChain `AyiruTool` adapter + demo notebook).

**Test count at v0.2 build close:** ≥ 760 backend + ~20 client = ~780 total. Ruff clean. Coverage ≥ 88%.

**Migrations:** 0001 through 0016 (or 0017 if the embeddings stretch lands).

**Contracts:** v1 (legacy, replay) + v2 (Stage 19 curated split). Lockstep mirror enforced.

**The product test:** A LangChain agent in a fresh project, given Ayiru's `AyiruTool` with the v0.2 docstring, picks `ask` over `web_search` on ≥ 50% of common dev questions covering the bulk-ingest tool list, and the resulting `tokens_saved` aggregate in `/v1/stats/savings` is non-trivially positive. The maintainer self-test in `docs/self_test_results.md` (Stage 20.9) is the canonical verification.

## v0.2.5 publish-cycle Definition of Done (for when it eventually runs)

1. Stages 22.2 – 22.5 closed.
2. Stage 23.1 executed (launch day).
3. Stage 23.3 kill-criteria checkpoint dates entered in the maintainer's calendar.
4. Gate 1 + Gate 2 both passed in writing (memos committed).

---

# What This Plan Is Not

- **Not the prose roadmap.** That's [roadmap_v0.2.md](roadmap_v0.2.md). This document is its stage breakdown.
- **Not an estimate for paid contractors.** Solo-dev focused-day estimates assume context retention from prior stages.
- **Not a marketing plan.** The launch-day playbook lives in roadmap_v0.2.md and the launch blog draft at [docs/launch_blog_post.md](docs/launch_blog_post.md). Reads from those when v0.2.5 actually schedules.
- **Not a substitute for talking to users.** Stage 16.4 was originally about recruiting 5 testers; under the solo-dev path it's a self-test. Real users only show up at v0.2.5 launch.
- **Not a v1.0 plan.** v1.0 owns L4 cross-agent verification, Stripe billing, multi-tenant SaaS, native rate limiting, and the post-launch hardening backlog. None of that is in scope here.
- **Not a publish plan.** Stages 22.2 – 22.5 + 23 are explicitly deferred to v0.2.5. The build cycle and the publish cycle are different scopes with different definitions of done.

---

*Plan authored 2026-05-20. Re-sequenced for solo-dev path 2026-05-21. Re-sequenced for build > publish 2026-05-22 (this revision). Stages 15.1–15.11, 16.5, 17.1–17.6 shipped between 2026-05-20 and 2026-05-22.*
