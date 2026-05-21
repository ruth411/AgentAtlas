# Ayiru — v0.2 Stage Plan

> **Companion to [roadmap_v0.2.md](roadmap_v0.2.md).** That document is the prose / phased / week-based plan with full rationale and council-review history. This document is the **stage-by-stage execution plan** in the same numbered "Stage N — name" cadence the v0.1 codebase already uses (Stages 0 through 14 are shipped). Every stage below is self-contained: goal, preconditions, files, tests, contract impact, definition of done, and what it explicitly defers.
>
> **Reads from:**
> - The [v0.1.0 brutal audit report](#) recorded in conversation on 2026-05-20 (12 findings, 11-item punch list).
> - The shipped roadmap at [roadmap_v0.2.md](roadmap_v0.2.md) (council-reviewed v0.2.1).
> - Existing infrastructure in [backend/app/](backend/app/), in particular the 6 services that already exist and must be reused (DocsIngestionService, ClaimStore, CanonOrchestrator, ToolSpecCompiler, QueryEngine, MCP server).
>
> **Two decision gates** sit between stages and must be passed before the next stage starts:
> - **Gate 1** (between Stage 16 and Stage 17): the Phase 0 measurement spike's two metrics must clear the council thresholds. If they don't, Stages 17–23 as written are dead and the project pivots or sunsets.
> - **Gate 2** (between Stage 22 and Stage 23): the pre-launch checklist from `roadmap_v0.2.md §Phase C` must be true at 07:00 of the chosen Tuesday. Otherwise delay by one week.

---

## Stage Numbering

| Range | Owner | Status |
|---|---|---|
| Stages 0–14 | v0.1 (shipped) — see [docs/stage_report.md](docs/stage_report.md) | Tagged `v0.1.0`, wheel built |
| **Stage 15** | v0.1 credibility close-out (from the audit punch list) | **partially done in 2026-05-20 session** |
| **Stages 16–23** | v0.2 — the "agent search box" pivot | not started |
| Stages 24+ | reserved for v0.3 (L4 cross-agent verification, hosted SaaS rollout, embeddings) | n/a |

---

## Decision Gates

### Gate 1 — measurement gate (after Stage 16, before Stage 17)

Adapted from [roadmap_v0.2.md §Decision Gate](roadmap_v0.2.md). Originally a 3-criterion gate where the third required 5 named beta testers; **the project is solo-dev, no external testers for v0.2.** Criterion 3 is rewritten to a self-test that the maintainer can run alone, with Claude pair-testing the agent loop. The other two criteria stand unchanged — they're product-truth, not audience-truth.

| # | Threshold | If false |
|---|---|---|
| 1 | ≥ 25% of agent token cost is web-search-replaceable lookups (P0.1 metric 1) | Pitch is wrong; sunset Stages 17–23 and replan. |
| 2 | LLM picks the fake `ask` over `web_search` in ≥ 50% of opportunities (P0.1 metric 2) | Tool-choice / docstring problem dominates retrieval; spend one extra week on docstring optimisation and re-measure. |
| 3 | **Solo self-test (revised)**: maintainer runs the headline agent loop end-to-end against the v0.1 graph and the fake-`ask` harness from P0.1; at least 7 of 10 realistic dev questions produce a verdict the maintainer would have accepted as a useful answer (not just "matches something"). | Seed graph is too thin to be useful even to the person who designed it. Pause Stage 20 (bulk ingest) until at least 7/10 self-test passes, since a thicker graph is the actual remedy. |

### Gate 2 — launch-day prerequisites (between Stage 22 and Stage 23)

Originally 5 criteria including "all 5 beta testers gave feedback" and "a named first reviewer is primed." Solo-dev v0.2 collapses those: there are no beta testers and no seeded reviewers. The remaining criteria are technical (the install works, the demo responds). Honesty trumps theatre.

All four must be true at 07:00 of the launch day:

1. Clean-venv `pip install ayiru` smoke completed in the last 24 hours.
2. The hosted demo (`try.ayiru.dev` or fallback) responded to a real `ask` call in the last hour.
3. The maintainer's self-test (Gate 1 criterion 3) has been re-run against the full v0.2 graph in the last 24 hours and still passes 7/10.
4. README, CHANGELOG, and the hosted-demo "what to type" example all install-and-paste cleanly on the maintainer's secondary machine.

If any is false, delay one week. Solo-dev launches are recoverable; a clean launch that doesn't actually install is not.

---

# Stage 15 — v0.1 Credibility Close-out

**Goal.** Resolve the 11-item brutal-audit punch list so v0.1.0 is *defensible* under a senior-engineer eval. Stage 15 ships no new features — it pays down the credibility debt the audit found between the README's claims and the codebase's reality.

**Why this is a stage, not a footnote.** The roadmap_v0.2.md Phase 0 says "no `backend/app/` code until Phase 0 passes the gate." But Phase 0 itself depends on a credible v0.1 to recruit beta testers (P0.4) and on real wheel artifacts (P0.3) for distribution. If a tester clicks the Stage 14 badge and lands on a docs page covering Stages 0–8, recruitment fails before measurement begins. Fix v0.1's surface first.

**Preconditions.** v0.1.0 tag exists; wheel built; tests green; ruff clean. (All verified in audit.)

## Stage 15.1 — Seed publishes canonical ToolSpecs ✓ DONE 2026-05-20

The single biggest credibility lever in the audit. Status as of close of session 2026-05-20:

- ✓ Added `source_code/high` evidence to the 3 high/critical headline claims (`gh repo delete`, `vercel --prod`, `docker rm`) at [data/seed_artifacts/claims/headline_scenarios.json](data/seed_artifacts/claims/headline_scenarios.json) (with byte-identical mirror at [backend/app/seed_data/artifacts/claims/headline_scenarios.json](backend/app/seed_data/artifacts/claims/headline_scenarios.json) per the Stage 14 lockstep contract).
- ✓ Added `_publish_canonical_specs(store)` in [backend/app/seed_data/runner.py:182-208](backend/app/seed_data/runner.py#L182-L208), called from `main()` after `_seed_headline_scenarios`. Compiles+saves a `ToolSpec` for every tool with accepted claims via `ToolSpecCompiler(store).compile(tool_id)` + `store.save_canonical_tool_spec(...)`.
- ✓ Added test `test_seed_publishes_canonical_tool_specs` in [backend/tests/test_seed_script.py](backend/tests/test_seed_script.py). Asserts the 4 expected tools (`git`, `github-cli`, `docker`, `vercel-cli`) each have a published spec with non-empty capabilities. `openai-api` deliberately excluded — its 32 OpenAPI-derived claims sit in pending review until Stage 19's curated/uncurated split lets us downgrade strictness.
- ✓ Test suite at 694 passing (was 693).

**End state observable**: `ayiru tools` lists 4 tools; `/v1/query/search-tools` returns matches; `/v1/query/tools/git` returns full spec; `/v1/query/validate-command` for `gh repo delete` returns `confidence=1.00`, `band=strong`, no "informational" caveat.

## Stage 15.2 — README headline output matches reality ✓ DONE 2026-05-20

- ✓ [README.md:144](README.md#L144) updated `confidence=0.69` → `confidence=1.00`.
- ✓ [README.md:52-54](README.md#L52-L54) updated the Python verdict block: `confidence: 0.92` → `confidence: 1.0` plus `confidence_band: "strong"`. Claim ID elided to `claim_…`.
- ✓ [README.md:140](README.md#L140) reframed: removed "A fresh checkout is populated…" claim (misleading — fresh checkout has empty DB until `ayiru seed --reset`). New phrasing notes the 4-published / 1-pending tool breakdown so users know what to expect.

## Stage 15.3 — Stage report covers Stages 9–14

**Problem.** [docs/stage_report.md:3](docs/stage_report.md#L3) opens with *"This report consolidates the completion status for every shipped stage of Ayiru (currently 0 through 8)."* The README badge ([README.md:14](README.md#L14)) links to that file under the label "Stage 14 complete." Anyone clicking through to verify finds a doc 6 stages out of date.

**Work.**
- Add a section per stage 9 / 10 / 11a / 11b / 12 / 13 / 14 to `docs/stage_report.md`, matching the existing Stage 0–8 sections: required artifacts, pass-case audit, quality bar, deferred. Reference the existing test files in [backend/tests/](backend/tests/) as the pass-case audit (e.g., `tests/test_query_engine.py` for Stage 9, `tests/test_mcp_server.py` for Stage 10, `tests/test_seed_script.py` for Stage 11a, `tests/test_human_review.py` + `tests/test_audit_log.py` for Stage 13).
- Update the opening sentence: "(currently 0 through 14)."
- Add a top-level "Audit findings — 2026-05-20" subsection that records the v0.1 audit's 11-item punch list and points each item at the Stage 15.x subsection that closed it.

**Files touched.**
- [docs/stage_report.md](docs/stage_report.md)

**Tests.** None. Doc-only.

**Definition of done.**
- File length grows from ~200 lines (today's Stage 0–8 coverage) to ~600+ lines.
- README badge link no longer points at a stale doc.
- A senior engineer skimming `docs/stage_report.md` can reproduce the v0.1 audit punch list and see each item closed.

**Defers.** A separate "API reference" doc. The OpenAPI spec at `/openapi.json` serves that role for v0.2.

## Stage 15.4 — PyPI install line resolution

**Problem.** [README.md:156](README.md#L156) shows `pip install ayiru`. Verified during audit: `pip index versions ayiru` returns `ERROR: No matching distribution found`. The comment `# once published to PyPI` is easily missed by a copy-paster.

**Work.** Two options, decide *before* Stage 16 (because Stage 16's beta-tester recruitment links to the README).

| Option | What | When valid |
|---|---|---|
| **A (preferred)** | Reserve `ayiru` on PyPI now with the existing wheel. `python -m build backend && twine upload backend/dist/*`. Pin `0.1.0` in `pyproject.toml`. | If the maintainer is ready to maintain a published package surface from this moment. Frees Stage 23 (Phase C #1 in roadmap_v0.2.md) of a release-day risk. |
| **B** | Demote the `pip install ayiru` block to a callout: *"PyPI publication lands with v1.0; for now, install from source per the dev block above."* | If reserving PyPI now is premature. Lower-cost reversible. |

Either way, [README.md](README.md) changes. Option A also touches GitHub release artifacts.

**Tests.** None. (The existing `pip install -e backend[dev]` dev path already has CI coverage via [.github/workflows/ci.yml](.github/workflows/ci.yml).)

**Definition of done.** No `pip install ayiru` instruction in the README that fails on a clean venv against the live PyPI index.

**Defers.** `ayiru-client` PyPI publication — that's Stage 21's deliverable, not Stage 15's.

## Stage 15.5 — Docker image first-run experience

**Problem.** [Dockerfile:30-32](Dockerfile#L30-L32) sets `ENTRYPOINT ["ayiru"]` and `CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]`. The README quick-start ([README.md:166-167](README.md#L166-L167)) tells users `docker run --rm -p 8000:8000 ayiru` — which starts the API against an empty DB. `data/` is COPYed in but `seed --reset` is never run during build.

**Work.** Three options, ordered by preference:

| Option | What | Trade-off |
|---|---|---|
| **A** | `ayiru serve` auto-seeds on first start when `--auto-seed` is passed (off by default in dev, on in the Docker image's CMD). Add `--auto-seed` to [backend/app/cli.py:_cmd_serve](backend/app/cli.py). | Best UX. Adds one branch to `ayiru serve`. |
| **B** | Add `RUN ayiru seed --reset` to [Dockerfile](Dockerfile) right after `RUN pip install /app/backend`. Pre-seeds at build time. | DB shipped in image (bigger image, ~10MB). Read-only filesystem won't work; image must use a writable volume. |
| **C** | Document in README quick-start: "first run requires `docker run --rm -v $(pwd)/data:/app/data ayiru seed --reset` before `ayiru serve`." | Cheapest. Worst UX. |

Recommend **A**: add `--auto-seed` flag, default off, set to on in [Dockerfile:CMD](Dockerfile). Behavior: at server startup, count claims; if zero, run the seed runner inline.

**Files touched.**
- [backend/app/cli.py](backend/app/cli.py) — add `--auto-seed` to `serve` subparser.
- [backend/app/main.py](backend/app/main.py) — startup hook calls the seed runner when auto-seed is requested and the DB is empty.
- [Dockerfile](Dockerfile) — update `CMD` to `["serve", "--host", "0.0.0.0", "--port", "8000", "--auto-seed"]`.

**Tests.** New test in [backend/tests/test_cli.py](backend/tests/test_cli.py): `test_serve_auto_seeds_when_db_is_empty` and `test_serve_does_not_re_seed_when_db_is_populated`. Use FastAPI's TestClient + a tmp DB URL.

**Definition of done.** `docker run --rm -p 8000:8000 ayiru` followed by `curl localhost:8000/v1/query/tools/git` returns a full ToolSpec without any prior `seed` command.

**Defers.** Volume persistence between container restarts. v0.2 hosted-demo will use a Fly.io persistent volume — that's Stage 22's deliverable.

## Stage 15.6 — pytest CVE bump

**Problem.** Audit flagged `pytest 8.4.2` has `CVE-2025-71176`. Test-runner only, dev-dep, but the fix is one-line.

**Work.**
- [backend/pyproject.toml](backend/pyproject.toml): change `pytest>=8.0,<9.0` → `pytest>=8.5,<10.0` (8.5 contains the patch backport; 9.0.3 is the canonical fix; allowing both keeps compatibility with environments that haven't moved to pytest 9 yet).
- Re-run `pip-audit` after the change. Confirm zero findings.

**Files touched.**
- [backend/pyproject.toml](backend/pyproject.toml)

**Tests.** Existing 694 must still pass. No new tests.

**Definition of done.** `pip-audit` against a fresh venv install reports zero known vulnerabilities for project deps.

## Stage 15.7 — Python version consistency

**Problem.** [backend/pyproject.toml](backend/pyproject.toml) says `>=3.11`; [Dockerfile:9](Dockerfile#L9) uses `python:3.11-slim`; README's dev quick-start uses `python3.12 -m venv`. Not broken — just inconsistent.

**Work.** Pick 3.12 as the supported floor for v0.2 (Python 3.11 is approaching its EOL window; 3.12 is the active stable). Update all three references; bump `requires-python` to `>=3.12`; update Dockerfile base image to `python:3.12-slim`.

Alternative: keep 3.11 floor and just align Dockerfile + README. Lower-impact, but defers the version bump to v0.3.

Pick one. Document the decision in `docs/stage_report.md` under the Stage 15 addendum.

**Files touched.**
- [backend/pyproject.toml](backend/pyproject.toml)
- [Dockerfile](Dockerfile)
- [README.md](README.md) (quick-start)
- [.github/workflows/ci.yml](.github/workflows/ci.yml) — Python matrix entry

**Tests.** Existing 694 must pass under the chosen interpreter. CI matrix should run the chosen version (and optionally the next-newer one to catch forward-compat breakage early).

**Definition of done.** A grep for `python3\.\d\d` across the repo shows one and only one supported version. CI passes on that version.

## Stage 15.8 — MCP-stdio-no-auth disclosure

**Problem.** [backend/app/auth.py](backend/app/auth.py) gates HTTP via `ApiKeyAuthMiddleware` when `AYIRU_API_KEY` is set. The MCP stdio server (`ayiru mcp`) has no equivalent gate. Acceptable as a *local-only* assumption, but undisclosed.

**Work.**
- Add a "Security model" subsection to [README.md](README.md) between the "MCP Integration" and "Core Principles" sections. Three paragraphs: (1) auth applies to HTTP writes only, off by default; (2) MCP stdio assumes the caller is local and trusted (the typical Claude Desktop / Cursor config); (3) for network-exposed MCP, run via the HTTP API with auth on (not stdio).
- Add `WARNING` log on `ayiru mcp` start when stdin/stdout are not TTYs *and* `AYIRU_API_KEY` is set: "MCP stdio path is unauthenticated regardless of AYIRU_API_KEY; ensure callers are local."

**Files touched.**
- [README.md](README.md) — new subsection.
- [backend/app/mcp_server/server.py](backend/app/mcp_server/server.py) — add startup warning.
- [SECURITY.md](SECURITY.md) — mirror the disclosure.

**Tests.** New test in [backend/tests/test_mcp_server.py](backend/tests/test_mcp_server.py): asserts the warning is emitted when `AYIRU_API_KEY` is set and the process is started via the MCP entry point.

**Definition of done.** README has an explicit "MCP stdio runs unauthenticated by design" callout. SECURITY.md mirrors it.

## Stage 15.9 — DNS rebinding mitigation (deferred to v0.3)

Audit finding #7. SSRF guard at [backend/app/services/http_safety.py:68-85](backend/app/services/http_safety.py#L68-L85) resolves DNS once at validation time; httpx resolves again at connect. Window for DNS rebinding. Not exploited against the current `official_hosts` allowlist (legit docs hosts don't rebind), but the guard's *claim* is weaker than it reads.

**Decision for v0.2:** track in [GitHub Project](https://github.com/ruth411/ayiru/projects) but don't block v0.2 on it. Real fix requires custom httpx transport that pins the resolved IP. Schedule for v0.3 alongside the post-launch adversarial pen-test (roadmap_v0.2.md §Hardening backlog).

**Documentation.** Add a paragraph to [SECURITY.md](SECURITY.md) acknowledging the DNS-rebinding window so the residual risk is honestly stated.

## Stage 15.10 — `.gitignore` housekeeping

Minor: `.coverage` left untracked after the audit's coverage run. Add `.coverage` and `htmlcov/` to [.gitignore](.gitignore).

## Stage 15.11 — Mapping comment for `KnowledgeClaim` vs `KnowledgeClaimRecord`

Minor (audit finding). [backend/app/db/models.py](backend/app/db/models.py) defines `KnowledgeClaimRecord`; [backend/app/schemas/claim.py](backend/app/schemas/claim.py) defines `KnowledgeClaim`. Convention: `*Record` for SQLAlchemy persistence, bare name for Pydantic transport. Add a one-line module docstring to both files documenting the convention so new contributors don't trip on imports.

---

## Stage 15 — Definition of Done

All 11 substages closed. The brutal-audit punch list is empty. The README's "Stage 14 complete" badge now links to a doc that actually covers Stages 9–14. A senior engineer doing a 30-minute eval cannot find a documented claim that contradicts the codebase's behavior.

**Tests at Stage 15 close:** ~700 passing (current 694 + ~6 new from 15.5, 15.8). Ruff clean. Coverage ≥ 88% (no regression).

**Estimated effort:** 2–3 focused days. 15.1 and 15.2 are done. 15.3 (stage report) is the longest single piece (~4h prose). 15.5 (Docker auto-seed) is the only nontrivial code change (~3h).

---

# Stage 16 — Phase 0 Measurement + Pre-flight

**Goal.** Validate the v0.2 pivot's central assumption (*"agents waste meaningful tokens on web searches that a local knowledge layer can serve"*) before writing any `backend/app/` code. Plus tee up the four other pre-flight items (PyPI name, v0.1.0 tag, 5 beta testers, launch budget).

**Why this is a stage, not week-0.** Roadmap_v0.2.md frames Phase 0 as "Week 0." Calling it Stage 16 makes it explicit that the decision-gate output is a release artifact (committed `phase0_results.json` + a written go/no-go memo) — not a vibes check.

**Preconditions.** Stage 15 fully closed (the audit punch list is empty; the README is honest).

## Stage 16.1 — Run the measurement spike (P0.1)

**Existing asset.** [scripts/phase0_measurement_spike.py](scripts/phase0_measurement_spike.py) — 503 lines, ready to run. Audit confirmed: well-documented, idiomatic, supports `--provider anthropic|openai`, has a `--dry-run` that exercises the script without LLM calls.

**Work.**
- Set up isolated venv with LangChain + provider SDK:
  ```bash
  python3 -m venv /tmp/phase0-venv
  /tmp/phase0-venv/bin/pip install langchain langchain-anthropic langchain-community duckduckgo-search
  ```
- Run `--dry-run` first to confirm the harness is intact.
- Run the real spike: `ANTHROPIC_API_KEY=... /tmp/phase0-venv/bin/python scripts/phase0_measurement_spike.py --provider anthropic`. Budget: ~$5–20 in API charges (see roadmap_v0.2.md P0.1 budget note).
- Capture `phase0_results.json` (the script writes this to the repo root by default; `.gitignore` already excludes it).
- Manually copy the two headline numbers + the qualitative observation into a new file `docs/phase0_memo.md`.

**Output artifacts.**
- `phase0_results.json` (local, gitignored — never committed; the file contains LLM responses that may be noisy).
- `docs/phase0_memo.md` — a ≤ 1-page memo, committed. Contains:
  - Two numbers (web-search-replaceable token fraction; ask-vs-web_search tool-choice rate).
  - One qualitative paragraph (did the LLM hallucinate plausible-but-wrong when the fake `ask` returned hits?).
  - Decision Gate verdict: pass / borderline / fail per row of the gate table.
  - Date, model used, total spend.

**Definition of done.** `docs/phase0_memo.md` exists, committed, has a clear go/no-go verdict per Decision Gate row.

## Stage 16.2 — PyPI name reservation (P0.2)

Already validated free during the audit: `pip index versions ayiru` and `pip index versions ayiru-client` both return "No matching distribution found."

If Stage 15.4 picked **Option A** (publish `ayiru` immediately), this stage just confirms the upload was successful. Else, it reserves both names with empty/stub packages:
- Run `python -m build` against a stub package at `backend/` (the v0.1.0 wheel is a fine stub).
- `twine upload backend/dist/ayiru-0.1.0*` (requires PyPI API token; document the token rotation procedure in `docs/operations/pypi.md`).
- For `ayiru-client`: build a stub `clients/python/dist/` with the package skeleton from Stage 21, version `0.0.1`.

**Definition of done.** `pip install ayiru` and `pip install ayiru-client` both succeed on a clean venv. Names are reserved. PyPI tokens are stored in a password manager (not the repo).

## Stage 16.3 — v0.1.0 release artifacts (P0.3)

**Status.** v0.1.0 tag already pushed; wheel at `backend/dist/ayiru-0.1.0-py3-none-any.whl`.

Remaining work:
- Create a GitHub Release for the `v0.1.0` tag if one doesn't exist. Upload the wheel + sdist as release assets. Title: "v0.1.0 — initial public release." Body: link to `CHANGELOG.md` v0.1.0 entry + the brutal-audit punch list close-out from Stage 15.

**Definition of done.** [https://github.com/ruth411/ayiru/releases/tag/v0.1.0](https://github.com/ruth411/ayiru/releases/tag/v0.1.0) has wheel + sdist attached.

## Stage 16.4 — Solo self-test (replaces beta-tester recruitment)

The original plan called for recruiting 5 named beta testers — the council framed it as *"the single highest-risk pre-flight item."* **The project is solo-dev. There are no external testers for v0.2.** Stage 16.4 is rewritten to a self-test the maintainer can run alone with Claude pair-testing the agent loop.

This swap accepts a real cost: at launch, zero external humans will have run v0.2 against a real agent. The mitigations are (a) a stricter self-test threshold than the original 5-tester "feedback in" bar, and (b) running the self-test *twice* — once at the Gate 1 measurement, once on launch eve at Gate 2 — to catch regressions introduced by Stages 17–22.

**Work.**

1. Pick 10 realistic dev questions matching the Phase 0.1 task list (the [phase0_measurement_spike.py](scripts/phase0_measurement_spike.py) `DEV_TASKS` list is the canonical source — use those questions, not invented ones).
2. For each question, run the agent harness from P0.1 with the fake `ask` tool wired to the v0.1 graph (or a stub that returns canned answers from the headline scenarios — whichever P0.1 used).
3. Score each question on a 3-state rubric:
   - **PASS** — the verdict's `statement` is something I would have actually given a colleague who asked. Not "matches by keyword" but "this is the answer."
   - **WEAK** — keyword match, but I'd flag the answer as incomplete or imprecise.
   - **MISS** — no match, or the match is wrong.
4. Record the 10 verdicts in `docs/self_test_results.md` (committed). One row per question with the verdict, the rubric grade, and a one-sentence rationale.

**Threshold.** ≥ 7 PASS out of 10 → criterion 3 of Gate 1 met. ≤ 6 PASS → pause Stage 17 and start Stage 20 (bulk ingest) early; a thicker graph is the only remedy for "the seed is too thin."

**Output.** `docs/self_test_results.md` — 10 rows, committed. Re-run at Gate 2 against the post-Stage-22 graph.

**Why this works as a stand-in for recruitment.** A solo dev who can't satisfy their own use case won't satisfy anyone else's. The 7/10 bar is intentionally stricter than the council's "did they call ask() at all?" bar because there's no external feedback loop to catch a bad gate verdict — the maintainer's own honesty is the only check.

**Deferred from this substage:** [docs/beta_tester_outreach.md](docs/beta_tester_outreach.md) and the recruitment-tracking workflow it describes. The playbook is preserved in the repo for a future moment (v0.3 or post-launch) when recruitment becomes viable. It's not removed — it's *parked*.

**Definition of done.** `docs/self_test_results.md` exists, committed, with 10 rows. ≥ 7 PASS. The two PASS/WEAK/MISS counts and one verdict sentence are written down per question.

## Stage 16.5 — Launch budget memo (P0.5)

Itemise the monthly running cost per roadmap_v0.2.md §Launch Budget. Floor: ~$50/month sustained for 6 months = ~$300 minimum.

**Work.** Create `docs/launch_budget.md`:
- Itemised table (Fly.io / domain / email / Stripe / S3 / Twitter) matching roadmap_v0.2.md.
- Funding source: self-funded / sponsor / cap-to-no-hosted-mode.
- Explicit 6-month kill date written down (e.g., "If by 2026-11-20 the kill criteria fire, the SaaS arm sunsets and the OSS layer continues without hosted demo").

**Definition of done.** Memo committed. Funding decision is *written*, not implicit.

---

## Stage 16 — Definition of Done

All 5 substages closed. `docs/phase0_memo.md` exists with a written Decision Gate verdict. `docs/launch_budget.md` exists with a written funding decision. `docs/self_test_results.md` exists with ≥ 7 PASS out of 10. PyPI names reserved (or Stage 15.4 Option B's deferral is the explicit decision). v0.1.0 release published on GitHub.

**No code in `backend/app/` has been touched.** That's the point.

**Estimated effort:** Roadmap_v0.2.md says "Week 0." Realistic solo-dev estimate: 5–7 days for P0.1 (set up venv, run spike, write memo) + ongoing 1–2 weeks for P0.4 recruitment (the long-pole task). Run P0.4 in parallel with the rest.

---

## ⚠️  GATE 1 — Decision Gate

Per [Decision Gates](#decision-gates) above. **Do not start Stage 17 unless all three gate criteria are met.**

If Gate 1 fails:
- Metric 1 fails → pitch is wrong. Pivot Stages 17–23 to whatever the spike surfaces as the real dominant cost (cost-observability dashboard? MCP registry? something else?). Rewrite this plan from Stage 17 onward.
- Metric 2 fails → docstring problem dominates. Spend a 1-week sub-stage on docstring optimisation, re-run the spike, re-evaluate.
- Self-test fails (< 7/10 PASS) → seed is too thin. Start Stage 20 (bulk ingest) before Stage 17; re-run the self-test against the post-Stage-20 graph.

Write the gate verdict into [docs/phase0_memo.md](docs/phase0_memo.md). Sign it with date + maintainer name.

---

# Stage 17 — `/v1/query/ask` Endpoint (Phase A1)

**Goal.** Add the headline endpoint: agents call `ask(question)` and receive cited, ranked answers from the knowledge graph. Pure lexical ranking (no embeddings yet; B3 in roadmap_v0.2.md is demoted to stretch).

**Preconditions.** Gate 1 passed.

## Stage 17.1 — New Pydantic schemas

**Files touched.**
- [backend/app/schemas/query.py](backend/app/schemas/query.py) — add:
  - `AskRequest`: `{question: str (1–512 chars), limit: int (1–20, default 5), tool_id_hint: str | None}`
  - `Answer`: `{claim_id, subject, statement, tool_id, confidence, verification_level, evidence: list[EvidenceCitation], match_reason: str}`
  - `AskResponse`: `{question, answers: list[Answer], fallback_recommended: bool, estimated_tokens_saved: int, generated_at: datetime}`

## Stage 17.2 — `QueryEngine.ask()`

**Files touched.**
- [backend/app/services/query_engine.py](backend/app/services/query_engine.py) — add `ask(question, limit, tool_id_hint)` next to existing `search_tools()` (~line 143).

**Implementation.**
- Pure lexical: `LIKE %term%` against `KnowledgeClaim.subject` + `statement` columns, with token-overlap ranking. Stop-word filtering against a small embedded set (~30 common English words).
- Tiered ranking like the existing `search_tools` does: exact subject match > prefix subject match > statement substring match.
- Filter to claims at `verification_status='accepted'` only — uncurated/pending claims (Stage 19 territory) are excluded from `ask` until Stage 19 introduces a flag.
- If 0 hits: `fallback_recommended=True`, empty `answers[]`.

**Tests.**
- [backend/tests/test_query_ask.py](backend/tests/test_query_ask.py) — new file, 8–12 tests:
  - Exact subject match returns top hit.
  - Substring statement match returns ranked correctly.
  - Stop-word query (`"how do I"`) returns empty + `fallback_recommended=True`.
  - Empty question → 422.
  - Question > 512 chars → 422.
  - `limit` clamped to range.
  - `tool_id_hint` narrows results.
  - Pending claims excluded from results.

## Stage 17.3 — `POST /v1/query/ask` route

**Files touched.**
- [backend/app/api/routes_query.py](backend/app/api/routes_query.py) — add the route. Reuse the existing `Depends(get_claim_store)` pattern.

**Behavior.**
- Default-deny: malformed body → structured 422 via `INVALID_CLAIM_SCHEMA` (already wired). No `ask` request should reach 500.
- Read endpoint — does **not** require auth even with `AYIRU_API_KEY` set. Matches the existing `/v1/query/*` precedent.

## Stage 17.4 — MCP tool #7 (`ask`)

**Files touched.**
- [backend/app/mcp_server/tools.py](backend/app/mcp_server/tools.py) — add `ask` to the tool registry list. The other 6 tools are already there; the list is a single declarative array.

**Docstring** (the LLM sees this — written carefully):
> *"Look up a verified, cited answer from the local knowledge graph before invoking web search. Returns ranked answers with confidence and source citations. Faster and cheaper than web search for common dev questions about tools, CLIs, APIs, and SDKs. Returns `fallback_recommended: true` on a miss — only then should you escalate to web search."*

**Tests.**
- [backend/tests/test_mcp_server.py](backend/tests/test_mcp_server.py) — extend the existing roundtrip tests to include `ask`.
- Schema validation: ensure `inputSchema` declares `question` (required), `limit` (optional, integer 1–20), `tool_id_hint` (optional).

## Stage 17.5 — Integration smoke

After implementing 17.1–17.4, verify end-to-end:
```bash
curl -X POST localhost:8000/v1/query/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"how do I delete a docker container"}'
```
Should return at least one answer matching the `docker rm` headline claim.

Also via MCP:
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ask","arguments":{"question":"how do I delete a docker container"}}}' | ayiru mcp
```

## Stage 17 — Definition of Done

- `POST /v1/query/ask` returns non-empty answers for the headline scenarios.
- `ask` is the 7th MCP tool, schema-valid, returns `structuredContent` per MCP 2024-11-05 spec.
- ≥ 10 new tests passing. Total ~710. Ruff clean.

**Estimated effort.** Roadmap_v0.2.md says "Week 1." 3–4 days realistic.

**Defers.** Embeddings (Stage 22.x stretch). Per-IP rate limiting (post-launch hardening). Multi-language stop-words (English-only for v0.2).

---

# Stage 18 — Cost-Savings Telemetry (Phase A2)

**Goal.** Make the cost savings observable. Every `ask` emits an audit event; a new aggregated endpoint exposes "X tokens saved this month." This is the moat-as-data: agents using Ayiru can prove the savings.

**Preconditions.** Stage 17 closed.

## Stage 18.1 — `QUERY_SERVED` audit event type

**Migration.**
- New file: `backend/alembic/versions/0016_add_query_served_event_type.py`.
- Mirror file: `backend/app/_alembic/versions/0016_add_query_served_event_type.py` (Stage 14 lockstep contract).
- DDL: extend the CHECK constraint on `audit_events.event_type` to include `QUERY_SERVED`.
- Pattern to follow: any of the existing `0010`/`0011`/`0012` extend-checks-for-X migrations. They're 30–50 lines each. Copy the structure.

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

## Stage 18.3 — `_AVERAGE_WEB_SEARCH_TOKENS` constant + math

**Files touched.**
- [backend/app/services/query_engine.py](backend/app/services/query_engine.py) — add module-level constant near the top:
  ```python
  # Tokens replaced when an agent picks ask() over web_search.
  # Typical web_search costs ~30 input tokens (query) + ~800 output
  # tokens (search results) ≈ 830 tokens. Ayiru's answer averages
  # ~150 tokens. Net savings ≈ 680 tokens per query.
  # Recalibrate from observed audit data after the first 1k real
  # queries land. The constant is the only knob; do not scatter
  # token-cost arithmetic across the codebase.
  _AVERAGE_WEB_SEARCH_TOKENS = 830
  ```

- Cost-savings calculation lives inside `ask()`:
  ```python
  response_tokens = len(json.dumps([a.dict() for a in answers])) // 4
  estimated_tokens_saved = max(0, _AVERAGE_WEB_SEARCH_TOKENS - response_tokens)
  ```

**Calibration sub-stage (post-launch).** After 1k real queries: compute observed mean `response_tokens` and adjust the constant. This becomes a measured number, not a guess.

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
- `_USD_PER_MILLION_INPUT_TOKENS` constant (default $3, Anthropic Claude Sonnet input rate). Make it configurable via env var `AYIRU_PRICE_PER_MTOK_INPUT` for projects on other models.
- Optional query params: `window=24h|7d|30d|all`, `api_key=...` (filter to one caller). When `AYIRU_API_KEY` is set, the endpoint becomes write-aware: any caller can read the aggregate, but only callers with the API key can filter by `api_key`.

## Stage 18.5 — Tests

- [backend/tests/test_query_ask.py](backend/tests/test_query_ask.py) — extend: assert `QUERY_SERVED` audit event is emitted per `ask`.
- [backend/tests/test_savings_endpoint.py](backend/tests/test_savings_endpoint.py) — new file. Tests: aggregation correctness, window filtering, USD calculation, empty-graph response.

## Stage 18 — Definition of Done

- Every `ask` call appends one `QUERY_SERVED` row to `audit_events`.
- `GET /v1/stats/savings` returns a structured aggregate.
- Migration `0016` applied; alembic drift test passes.
- ~720 tests passing. Ruff clean. Coverage ≥ 88%.

**Estimated effort.** 2–3 days.

**Defers.** Per-API-key telemetry dashboard UI (Stage 22). Multi-currency conversion (USD only for v0.2).

---

# Stage 19 — Curated vs Uncurated Tool Split (Phase B1)

**Goal.** Relax the Stage 0 tool lock so the seed graph can hold the 5,000+ uncurated claims Stage 20 is about to add. Curated tools keep the full orchestrator path (claims → accepted → spec → validate_command). Uncurated tools land at `L0_unverified` / `pending` and are visible to `ask` but excluded from `validate_command`.

**Preconditions.** Stage 18 closed.

## Stage 19.1 — Contract version bump

**Files touched.**
- New file: [contracts/ayiru_stage_0.v2.json](contracts/ayiru_stage_0.v2.json).
- Mirror file: [backend/app/contracts/ayiru_stage_0.v2.json](backend/app/contracts/ayiru_stage_0.v2.json) (Stage 14 lockstep).
- Schema: add `"curated": true` to each of the existing 5 entries (git, github-cli, docker, vercel-cli, openai-api). Bump `"version": 2`.

**Test.**
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
- [backend/app/services/command_matcher.py](backend/app/services/command_matcher.py) — extend the existing exclusion logic. `validate_command` already filters to `verification_status='accepted'` (per the audit's observation). Confirm this; if not, add the filter.
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
  - Migration not required for this stage (no schema change).

## Stage 19 — Definition of Done

- Uncurated claims persist and surface in `ask`.
- Curated tools still get the full Stage 6 pipeline (Stage 0 → 14 behavior unchanged).
- ~730 tests passing.

**Estimated effort.** 2 days. Council called this "one week" in roadmap_v0.2.md; that was bundled with B2 and B3 — B1 alone is faster.

**Defers.** A reviewer UI for promoting uncurated claims (continues to use the existing `POST /verification/human-review` endpoint). Per-tool ingestion rate limits.

---

# Stage 20 — Bulk Ingestion Harness (Phase B2)

**Goal.** Populate the graph from ~47 claims (Stage 15.1 end state) to ≥ 5,000 claims across ≥ 50 tools. Without this, the `ask` endpoint is a toy.

**Preconditions.** Stage 19 closed (so uncurated claims can land without orchestrator rejection).

## Stage 20.1 — `ayiru ingest` CLI subcommand

**Files touched.**
- [backend/app/cli.py](backend/app/cli.py) — new `ingest` subparser with args:
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

**Work.** For each of the 50 tools, document the docs license in `tools/v0.2_seed_licenses.md`:
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

**Realism note from roadmap_v0.2.md.** Some docs sites (e.g., Vercel's, parts of AWS) are SPA-rendered. httpx alone won't fetch the content. Two options:
- **For v0.2:** drop those tools from `tools/v0.2_seed.yml` and queue a Playwright-based fetcher for v0.2.2.
- **Stretch:** integrate Playwright behind a new ingestion lane (Stage 7e). Too much scope for v0.2.

Decision: drop the JS-only sites. Document the dropped tools in `tools/v0.2_seed_dropped.md` with reasoning.

## Stage 20.7 — Rate limit + ToS compliance

For each docs host: honor `robots.txt`, set `User-Agent: Ayiru-Bulk-Ingestion/1.0 (+https://github.com/ruth411/ayiru)`, cap to 1 req / sec per host. Already enforced by the existing httpx client's defaults; this stage adds the per-host rate limiter.

**Files touched.**
- [backend/app/services/docs_ingestion.py](backend/app/services/docs_ingestion.py) — extend the existing client with a `httpx.Limits` configuration.

## Stage 20.8 — Run the bulk ingest

After 20.1–20.7 land:
```bash
ayiru ingest --source docs --tool-list tools/v0.2_seed.yml
```
Expected: 50 tools × ~100 claims/tool = ~5,000 claims. Realistic: 35–45 tools clear legal review × ~80 claims = 2,800–3,600 claims. Both numbers comfortably above the "≥ 5,000 claims" target if you also include the existing 47 curated claims and the existing OpenAPI/JSON Schema bulk.

**Definition of done.**
- `tools/v0.2_seed.yml` committed with 35–50 entries.
- `tools/v0.2_seed_licenses.md` committed with per-tool license review.
- `ayiru ingest` runs end-to-end against the file, populates ≥ 2,800 claims.
- Audit events log every ingestion run.

**Estimated effort.** Roadmap_v0.2.md says "Week 3" — 5–10 days. Realistic: 4 days CLI/contract work + 2–3 days legal review + 1 day rate-limited crawl run.

**Defers.** Playwright lane. Per-tool freshness re-ingestion schedule. OpenAPI / GraphQL bulk variants (only docs lane for v0.2; existing OpenAPI / JSON Schema / GraphQL lanes still work one-off via their existing endpoints).

---

# Stage 21 — Python Client SDK (Phase C1)

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

atlas = AsyncAyiru(base_url="https://try.ayiru.dev")
answer = await atlas.ask("...")
```

**Methods on each:** `ask(question, limit=5)`, `validate_command(tool_id, command)`, `get_tool_spec(tool_id)`, `search_tools(query)`, `savings(window="30d")`.

**Computed properties on `Answer`:**
- `is_useful` — `confidence >= 0.6 and verification_level != L0_UNVERIFIED`.
- `tokens_saved_estimate` — pulled from the server response.

## Stage 21.3 — Tests

**Files touched.**
- [clients/python/tests/test_client.py](clients/python/tests/test_client.py) — runs against a FastAPI `TestClient`-wrapped backend (no network).
- Same hermeticity contract as backend tests.

## Stage 21.4 — Documentation

- `clients/python/README.md` — quickstart, full method reference, examples.
- Cross-link from the main repo README.

## Stage 21 — Definition of Done

- `pip install -e clients/python` works.
- `pip install ayiru-client` works (PyPI publication via Stage 22.3).
- Documented examples run.
- ~750 tests total (backend 730 + client 20).

**Estimated effort.** 2 days.

**Defers.** TypeScript / JS client (v0.3). Streaming `ask` responses (no use case yet).

---

# Stage 22 — LangChain Adapter + Hosted Demo + OSS Hygiene (Phases C2, B, README Pivot)

**Goal.** Make Ayiru reachable to LangChain users with zero glue code, host a live demo at `try.ayiru.dev` (or fallback), and tidy the OSS surface (issue templates, CHANGELOG, social preview, etc.).

**Preconditions.** Stage 21 closed.

## Stage 22.1 — LangChain `AyiruTool`

**Files touched.**
- New file: `clients/python/ayiru_client/langchain.py`.
- Class: `AyiruTool(BaseTool)` subclassing `langchain_core.tools.BaseTool`.
- Critical: the docstring is what the LLM sees as the tool description. Should explicitly say *"use this before invoking web search for common dev questions about CLIs, APIs, and tools."*

**Files touched.**
- `clients/python/examples/langchain_demo.ipynb` — 10-question notebook. Show 7 hits + 3 fallbacks with cost-saved counter at the end.

## Stage 22.2 — README pivot

The audit acknowledged the current README sells "verified knowledge layer" — the v0.2 pitch is "agent search box that cuts API costs." Time to rewrite the hero.

**Files touched.**
- [README.md](README.md) — hero rewrite:
  - Drop "Wikipedia for AI agents" framing.
  - New first sentence: *"Ayiru is the local search box your AI agent hits before the web — cuts tool-call costs by routing common queries to a verified knowledge graph instead of paying for `WebSearch` tokens."*
  - Replace the headline `validate_command` example with an `ask` example.
  - Demote the stages table from the headline into a collapsed `<details>` block at the bottom.
  - New 15-second GIF: LangChain agent → `atlas.ask("how do I delete a docker volume")` → cited answer → cost-saved counter on screen.
- GitHub repo description (settings): one sentence matching the new hero.
- GitHub topics: `ai-agents`, `mcp-server`, `llm-tools`, `langchain`, `agent-infrastructure`, `llm-cost-optimization`.
- [frontend/app/page.tsx](frontend/app/page.tsx) — hero rewrite matching README; swap interactive component from `validate_command` to `ask`.

## Stage 22.3 — PyPI publication

Two packages, two uploads:
- `python -m build backend && twine upload backend/dist/ayiru-0.2.0*`
- `python -m build clients/python && twine upload clients/python/dist/ayiru-client-0.2.0*`

Tag `v0.2.0` first. Confirm a clean-venv `pip install ayiru` and `pip install ayiru-client` both work.

## Stage 22.4 — Hosted demo at `try.ayiru.dev`

**Realism note from roadmap_v0.2.md:** 8–12h, not the original 4h. First Fly.io deploy with secrets, persistent SQLite volume, custom domain, TLS, rate-limiting config.

**Work.**
- `fly.toml` at the repo root configuring shared-CPU 1GB instance, persistent volume of 1 GB for `ayiru.db`, custom domain `try.ayiru.dev` (or `ayiru.fly.dev` as fallback).
- `Dockerfile` already exists from Stage 12 (Stage 15.5 adds auto-seed).
- Fly secrets: `AYIRU_API_KEY` for any private endpoints (defaults remain readable per Stage 19's design).
- Rate limit: 100 req/min/IP using Fly's built-in `services.concurrency`.
- TLS via Fly's automatic Let's Encrypt.

## Stage 22.5 — OSS hygiene (Phase B from roadmap_v0.2.md)

- [CHANGELOG.md](CHANGELOG.md) — v0.2.0 entry, Keep-a-Changelog format.
- New: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1.
- New: `.github/ISSUE_TEMPLATE/bug_report.yml`, `feature_request.yml`, `new_tool_request.yml`.
- New: `.github/pull_request_template.md` — checklist mirrors CONTRIBUTING.md (tests, migration reversible, contract versioned).
- New: `.github/FUNDING.yml`.
- Enable GitHub Discussions.
- Repo social preview image (1280×640 PNG) uploaded via repo settings.
- Star History badge in README.
- Maintainer contact in SECURITY.md + README.
- Replace any `ruth411` placeholders with the canonical repo path.

## Stage 22 — Definition of Done

- `pip install ayiru` and `pip install ayiru-client` both work on clean venv.
- `https://try.ayiru.dev/v1/query/ask` responds with a real answer (smoke-tested in the last hour).
- LangChain demo notebook runs end-to-end, prints "saved $X.XX" footer.
- GitHub repo card shows new description, topics, social image.
- All 15 Phase B hygiene items complete.

**Estimated effort.** Roadmap_v0.2.md says "Week 6," ~12h focused work. Realistic with hosting: 4–5 days. The Fly.io deploy is the long pole.

**Defers.** Authenticated multi-tenant hosted SaaS (Stage 24+). Self-service signup. Stripe billing (Phase D, post-launch).

---

## ⚠️  GATE 2 — Launch-Day Prerequisites

See [Gate 2](#gate-2--launch-day-prerequisites-between-stage-22-and-stage-23). All 5 prerequisites must be true at 07:00 of the chosen launch Tuesday. If any is false, delay one week.

---

# Stage 23 — Launch Day + Sustainability (Phase C, D)

**Goal.** Execute the launch playbook from `roadmap_v0.2.md §Phase C` on a single chosen Tuesday. Then enter sustainability mode with explicit kill criteria.

**Preconditions.** Gate 2 passed.

## Stage 23.1 — Launch-day timeline

Follow [roadmap_v0.2.md §Phase C](roadmap_v0.2.md) verbatim:

| Time | Action |
|---|---|
| **08:00** | Final smoke — clean-venv `pip install ayiru`, run `ask`, verify GIF still plays, hosted demo responsive. |
| **09:00** | Publish blog post: *"I built Ayiru to cut my agent's API bill"*. |
| **10:00** | Twitter/X thread (7–10 posts). Open with itemized-bill screenshot. |
| **11:00** | r/LocalLLaMA post. |
| **13:00** | r/LangChain post with LangChain demo notebook. |
| **15:00** | **Hacker News — Show HN.** Stay online until 22:00 answering comments. |
| **18:00** | Cross-post to Lobsters, the LangChain / Cursor / Anthropic / OpenAI Discords, the MCP working group Slack. |
| **Wed–Fri** | 24h response SLA on every issue. Merge low-risk PRs same-day. |

## Stage 23.2 — Post-launch first 30 days

- 24–48h issue response SLA (self-imposed, non-negotiable). Solo-dev SLA is harder to honour — if real life intervenes, post one line on the relevant issue *("oncall this week, will respond by [date]")* rather than going silent.
- Weekly digest post: *"This week in Ayiru: X tools added, Y queries served, Z PRs merged."*
- Identify the first named user from the post-launch traffic for the README's "Used by" section. (There are no pre-launch testers to cite.)
- Public counter on hosted demo: *"X tokens saved across all users this month."* Includes the maintainer's own use against `try.ayiru.dev` so the number doesn't sit at zero on launch day.

## Stage 23.3 — Kill criteria checkpoints

Adapted from [roadmap_v0.2.md §Kill Criteria](roadmap_v0.2.md). Original Week-10 criterion referenced "the 5 beta testers" — rewritten below for the solo-dev path. The other rows stand unchanged.

| Checkpoint | Failure trigger | Decision |
|---|---|---|
| **Week 8** (1 week post-launch) | < 10 GitHub stars from non-personal-network sources | HN/Reddit/Discord didn't catch. Stop launch amplification; debrief on positioning. |
| **Week 10** (1 month post-launch) | < 3 unique API-key holders called `ask` ≥ 10 times each on the hosted demo, AND zero new GitHub Discussions threads from strangers | Installs exist but no usage. Talk to the *first 1-2 users who did call it* — if any — and ask why retention dropped. Without beta testers, the only feedback loop is real users. |
| **Month 3** | Zero paying customers or sponsors | Cost-savings pitch doesn't convert. OSS continues; SaaS arm dies. |
| **Month 6** | Combined MAUs < 50 | Audience not found. Pivot again (council pressure-test) or sunset. |

Hitting any of these is **not** "grind harder." It's "the product is wrong; pivot or stop."

**Solo-dev addendum.** The original plan had a 5-tester feedback loop catching problems *before* launch. The solo-dev path has no such loop, so the first real failure signal will come from post-launch usage data. The Week-8 and Week-10 checkpoints are therefore *more* load-bearing than they would be with seeded testers — they're the only feedback before the project either grows or stops.

## Stage 23.4 — Hosted SaaS rollout (Month 2–3 only if Stage 23.3 doesn't trigger)

Reserved for v0.3 / post-launch. Stripe + signup + per-key rate limiting + cost-analytics dashboard. Not part of v0.2 proper.

## Stage 23 — Definition of Done

v0.2.0 is on PyPI, on the hosted demo, on HN, and on the kill-criteria board. v0.2 ships.

---

# Cross-Cutting Concerns

These apply to every stage above. Codify here so each stage's checklist stays short.

## CC.1 — Contract version + lockstep mirror

Every contract JSON change must:
- Land in [contracts/](contracts/) (source of truth).
- Mirror byte-identically into [backend/app/contracts/](backend/app/contracts/).
- Increment the version filename if behavior changes (`*.v1.json` → `*.v2.json`). Old version stays for replay.
- Be covered by [backend/tests/test_bundled_contracts_in_sync.py](backend/tests/test_bundled_contracts_in_sync.py) (auto-passes if files match).

## CC.2 — Seed artifact lockstep

Same lockstep contract for [data/seed_artifacts/](data/seed_artifacts/) ↔ [backend/app/seed_data/artifacts/](backend/app/seed_data/artifacts/). Enforced by [backend/tests/test_bundled_seed_in_sync.py](backend/tests/test_bundled_seed_in_sync.py). The Stage 15.1 work surfaced this — both locations must move together.

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
- New env vars get a row in `docs/operations/env_vars.md` (new file in Stage 15.5 or earlier).
- Every stage's close-out adds a section to `docs/stage_report.md`.

## CC.7 — Backward compat

- v0.1 API (`/v1/query/validate-command`, the 6 existing MCP tools) is **frozen**. v0.2 only adds.
- Legacy unversioned routes (`/query/...` without `/v1` prefix) keep working until v1.0. RFC 8594 deprecation headers stay attached per Stage 14.

---

# Estimated Total Effort

| Stage | Work item | Solo-dev focused-days |
|---|---|---|
| 15.1, 15.2 | DONE ✓ | 0 |
| 15.3 | Stage report doc | 0.5 |
| 15.4 | PyPI install decision | 0.25 |
| 15.5 | Docker auto-seed | 1 |
| 15.6 | pytest CVE bump | 0.1 |
| 15.7 | Python version | 0.5 |
| 15.8 | MCP-stdio disclosure | 0.5 |
| 15.10, 15.11 | gitignore + naming | 0.1 |
| **Stage 15 total** | | **~3 days** |
| **Stage 16** | Phase 0 + recruitment | **5–7 days (P0.1) + 7–14 days (P0.4 in parallel)** |
| **Stage 17** | `/v1/query/ask` | **3–4 days** |
| **Stage 18** | Cost telemetry | **2–3 days** |
| **Stage 19** | Curated split | **2 days** |
| **Stage 20** | Bulk ingest | **5–8 days** |
| **Stage 21** | Python SDK | **2 days** |
| **Stage 22** | LangChain + hosted + hygiene | **4–5 days** |
| **Stage 23** | Launch + sustain | **1 launch day + ongoing** |

**Realistic total to v0.2.0 launch:** 9–11 weeks (~2.5 months) of solo-dev focused work, matching roadmap_v0.2.md's 9-week council-revised estimate. Calendar time will be longer because evenings/weekends/recruitment-wait.

---

# v0.2 — Definition of Done (entire release)

Composite of every stage's DoD. v0.2.0 ships when **all** of:

1. Stage 15 closed (audit punch list empty).
2. Stage 16 closed (Phase 0 memo + budget + recruitment + tags).
3. Gate 1 passed in writing.
4. Stage 17 closed (`ask` endpoint live; MCP tool #7 listed).
5. Stage 18 closed (`QUERY_SERVED` audit + `/v1/stats/savings` live).
6. Stage 19 closed (curated split shipped; uncurated claims allowed at L0).
7. Stage 20 closed (≥ 2,800 claims across ≥ 35 tools).
8. Stage 21 closed (`ayiru-client` on PyPI).
9. Stage 22 closed (LangChain adapter; hosted demo; OSS hygiene complete).
10. Gate 2 passed.
11. Stage 23.1 executed (launch day).

Test count at v0.2 close: **≥ 730 backend + ~20 client = ~750**. Ruff clean. Coverage ≥ 88%.

Migrations: 0001 through ~0017. Alembic drift test green.

Contracts: v1 (legacy, replay) + v2 (Stage 19 curated split). Lockstep mirror enforced.

**The product test:** A LangChain agent in a fresh project, given an Ayiru tool with the v0.2 docstring, picks `ask` over `web_search` on ≥ 50% of common dev questions and saves ≥ 25% of the corresponding token budget. That's the spike's Decision Gate threshold made real, post-launch.

---

# What This Plan Is Not

- **Not the prose roadmap.** That's [roadmap_v0.2.md](roadmap_v0.2.md). This document is its stage breakdown.
- **Not an estimate for paid contractors.** Solo-dev focused-day estimates assume context retention from prior stages.
- **Not a marketing plan.** Stage 23.1 references the launch playbook, but the brand / GTM strategy lives in the launch blog draft at [docs/launch_blog_post.md](docs/launch_blog_post.md).
- **Not a substitute for talking to the 5 beta testers.** Every stage from 17 onward is wrong by default until they say otherwise.
- **Not a v1.0 plan.** v1.0 owns L4 cross-agent verification, embeddings, Stripe billing, multi-tenant SaaS, and the post-launch hardening backlog. None of that is in scope here.

---

*Plan authored 2026-05-20 based on the brutal v0.1.0 audit + roadmap_v0.2.md (v0.2.1 council revision). Stages 15.1 and 15.2 completed in the same session.*
