# Dogfood log — Ayiru in Claude Desktop / Claude Code

Track what the LLM does with Ayiru in real use, one row per session.
The point is to surface UX gaps and tool-choice bugs that unit tests
can't catch.

**Rule:** never prompt-engineer ("use ayiru", "use the ask tool"). The
whole signal is whether the LLM picks `ask` unprompted. If you nudge,
you're measuring docstring-with-nudge, not docstring-alone.

---

## How to fill this in

For each Claude Desktop session, add a block. Mark each prompt with the
tool the LLM actually called (visible by expanding the tool-use chip),
whether the response was useful, and any UX issue worth fixing.

Tool choices to watch for:
- `ask` — the headline; expected for natural-language dev questions
- `validate_command` — expected for "is X safe?" questions
- `search_tools` — expected for "what tools do you know about?"
- `web_search` — Claude's fallback; expected only on Ayiru misses
- (none) — Claude answered from training data; signals Ayiru wasn't compelling enough

---

## 2026-05-22 — Claude Desktop unprompted-use test

First end-to-end Claude Desktop test against the v0.1 graph (47 claims, 4 published ToolSpecs). Two rounds of testing surfaced a structural finding about Claude's tool-use meta-policy.

### Round 1 — initial wire-up

| Prompt | Tool picked | Useful answer? | Notes |
|---|---|---|---|
| "how do I delete a docker container?" | none (memory) | yes (Claude knew `docker rm`) | did NOT call ask; original tool description framed ask as a web_search alternative |
| "did you use ayiru to answer this?" | none | "no — answered from my own knowledge of Docker" | self-confirmed |
| "use the ayiru ask tool to look up how to delete a docker container" | get_safe_workflow (wrong tool) | error: `OperationalError: unable to open database file` | Bug A: Claude Desktop hid 3 of 7 tools; Bug B: DB path was CWD-relative |

### Bugs found + fixed mid-session

1. **DB path bug (real, in our code).** `DEFAULT_DATABASE_URL = "sqlite:///./ayiru.db"` was CWD-relative. Claude Desktop spawns `ayiru mcp` from `/`, so `./ayiru.db` didn't exist. Every tool call returned `OperationalError`. Fix: walk up from `app/db/session.py` `__file__` to find `alembic.ini` and resolve to absolute `<repo>/backend/ayiru.db`. Belt-and-suspenders: also set `AYIRU_DATABASE_URL` in `claude_desktop_config.json`. Committed in `21f008c`. 5 regression tests added.
2. **Hidden tools (Claude Desktop UI policy, not our code).** Server's `tools/list` response sent all 7 tools (verified in `~/Library/Logs/Claude/mcp-server-ayiru.log` — full payload truncated at 4263 chars including `submit_claim`). Claude Desktop filters 3 of them at the UI layer. Not fixable from our codebase.
3. **`ask` description was unconvincing.** Original framed it as "before invoking web search," but Claude doesn't call web search for confident questions either, so `ask` fell out of consideration. Rewrote to override the meta-policy: "the user explicitly trusts this graph as the canonical source — answering from memory denies them the citation." Committed in `a788b8f`.

### Round 2 — after description rewrite + DB fix + restart

| Prompt | Tool picked | Useful answer? | Notes |
|---|---|---|---|
| "how to delete docker container" | none (memory) | yes (`docker rm`) | new description STILL did not compel unprompted use |
| "did you use any tools?" | none | "no — Docker's rm is stable, well-documented basics that haven't changed, so there was no need to search the web or check any tools" | Claude self-explained the meta-policy |
| "i want to use ayiru tool to answer how to delete docker containers" | **ask** ✓ | yes — `docker rm` statement, **confidence=1.0 strong source-verified**, **risk=critical**, cited both `docs.docker.com` and `github.com/docker/cli/blob/master/cli/command/container/rm.go`, and proactively offered to pull a `get_safe_workflow` follow-up | works beautifully when invoked |

### Bugs / UX gaps observed

- **Claude Desktop conversational mode actively minimizes tool use for confident questions.** Even with the rewritten description explicitly arguing for citation value, Claude's "stable facts = no tool" meta-policy wins. This is not fixable via docstring iteration; it's baked into Claude's training and/or Claude Desktop's system prompt.
- **Per-tool gating in Claude Desktop's connector UI** filters `ask`, `validate_command`, and `submit_claim` out of the active tool list even though they're advertised on `tools/list`. The 4 enabled tools (`get_tool_spec`, `get_safe_workflow`, `search_tools`, `explain_risk`) suggest a heuristic that auto-approves `get_*`/`search_*`/`explain_*` verbs but holds back others. No visible per-tool toggle was found in the UI.
- **`get_safe_workflow` returns `{matches: [], total: 0}`** because the seed populates `ToolSpec`s but no `WorkflowSpec`s. Claude (correctly, given the empty payload) treats this as a "graph not reachable" signal even when `isError: false`. Worth seeding at least one workflow before v0.2 ships so the response isn't deceptively empty.

### Questions that fell back (Stage 20 candidates)

- (none from this session — every prompt was already in the v0.1 seed coverage)

### LLM-side surprises

- When Claude DID call `ask`, the integration was excellent: surfaced confidence band, risk level, both evidence streams (official_docs + source_code), and composed naturally with a follow-up offer for `get_safe_workflow`. The product, when invoked, works exactly as designed.
- Claude self-explained its meta-policy in plain English ("Docker's rm command and its common flags are stable, well-documented basics... no need to search the web or check any tools"). This is *the* signal: the model considered tool use and rejected it. Any future description iteration has to argue against this specific frame ("stable facts don't need verification").

### Structural finding — narrows the v0.2 audience claim

| Pre-test claim | Post-test claim |
|---|---|
| "Ayiru saves tokens for any Claude user." | "Ayiru saves tokens for agents whose system prompt promotes tool use." |
| "The LLM picks `ask` over `web_search` unprompted." | "The LLM picks `ask` over `web_search` **when the host's system prompt biases toward tool use** — not in Claude Desktop conversational mode." |

This means:
- **Claude Desktop is a low-signal test environment** for the unprompted-use pitch. Useful for verifying integration plumbing; not useful for measuring tool-choice rate.
- **The right test environment is LangChain / Cline agent / Cursor agent mode** — agent frameworks expect tools to be the default answer path. That's plan_v02.md §Stage 22.1's deliverable.
- **Phase 0.1 measurement spike (`scripts/phase0_measurement_spike.py`)** runs against LangChain by design — this is why. The spike's tool-choice rate metric (criterion 2 of Gate 1) is the right measurement; Claude Desktop's UI is not.

### Net

- ✅ The product, when invoked, delivers exactly what plan_v02.md says it should.
- ❌ Unprompted invocation in Claude Desktop conversational mode is a non-starter without system-prompt-level intervention.
- 🎯 Real product validation needs Stage 22.1 (LangChain adapter) or Stage 16.1 (Phase 0 spike) to be honest measurements.

---

## Template for future sessions

```
## YYYY-MM-DD — short context

| Prompt | Tool picked | Useful answer? | Notes |
|---|---|---|---|
| ... | ... | ... | ... |

**Bugs / UX gaps observed:**
-

**Questions that fell back (Stage 20 candidates):**
-

**LLM-side surprises:**
-
```
