# Phase 0.4 — Beta-tester outreach playbook (DEFERRED for v0.2)

> **Status:** *Parked for v0.2.* The project is solo-dev; v0.2 ships without external beta testers. This playbook stays in the repo for a future moment (v0.3 or post-launch) when recruitment becomes viable.
>
> Stage 16.4 of [plan_v02.md](../plan_v02.md) has been rewritten to a solo self-test ([docs/self_test_results.md](self_test_results.md)) in place of recruitment. The Gate 1 measurement still has to clear two product-truth metrics (P0.1) — the audience-truth criterion is now self-honesty, not external signal.
>
> ---
>
> **Original purpose (preserved for reference):** Recruit 5 named agent developers who commit (in writing) to running v0.2 against their real agent within 7 days of A1 shipping. If you can't get 5 in a week, the launch has no audience.

---

## Sourcing (where to fish)

The pitch is *"I'll save you tokens / API spend on your agent."* Go where agent
developers already complain about that.

| Channel | Volume | Conversion guess | Notes |
|---|---|---|---|
| **r/LangChain** | medium | low (~3%) | Pin a thread; don't DM mods. Watch for replies asking "where can I try this?" — those are warm leads. |
| **r/LocalLLaMA** | high | very low (~1%) | Cost-focused crowd. The cost-savings pitch lands. |
| **Anthropic Discord — #building** | medium | medium (~10%) | Highest signal-to-noise. People here already pay for Claude tool calls. |
| **Cursor Discord — #plugins** | medium | low (~3%) | Tangential audience; useful if you ship MCP first. |
| **MCP working-group Slack** | small | high (~20%) | Tiny group, all relevant. Don't spam. |
| **Twitter/X DM** | unbounded | very low (~1%) | Search recent posts about "Anthropic bill" or "Claude tool cost" — DM authors. |
| **GitHub Issues on related repos** | low | medium (~10%) | Be careful — don't hijack other people's issues. Reply to "want to reduce LLM cost" threads with a one-line link. |

Cold-DM **30 people** to expect 5 yeses.

---

## DM template (Discord / Twitter)

Short, specific, asks for one explicit yes/no. **Be honest that v0.2 isn't shipped yet** — you're pre-recruiting so testers are ready the moment the `ask` endpoint lands.

```
hey [name] — saw your post about [specific thread / project that names a real
LLM cost or tool-choice problem].

I'm building Ayiru — an MIT-licensed local search box for AI agents that
cuts your tool-call bill by routing common dev/CLI/API questions to a
verified knowledge graph instead of paying for web_search tokens.

v0.1 is shipped (MCP server + REST API, 47 cited claims across 5 dev tools).
v0.2 adds the `ask` endpoint your agent would actually hit — landing in
~4–6 weeks.

Looking for 5 devs to commit now to running v0.2 against a real agent
within 7 days of it shipping. ~30 min of your time, you get:

- early access the day it lands
- a direct line to me (this DM)
- your name in the README's "first users" section if you want it
- no NDAs, no commercials, MIT-licensed

interested? if yes I'll add you to the early-access list and ping you the
day v0.2 is live.
```

**What works:** specific reference to their post, concrete time ask (30 min), single yes/no question, honest about the timeline, no marketing language.

**What doesn't:** *"let me know your thoughts," "would love to chat,"* any phrase a recruiter would use. Also avoid claiming a hosted demo exists — it doesn't yet (Stage 22.4).

---

## Email template (for known contacts)

```
Subject: Pre-launch — 30 min testing Ayiru when v0.2 ships?

Hi [name],

I'm building Ayiru — an MIT-licensed local search box for AI agents
that cuts tool-call costs by routing common dev/CLI/API questions to a
verified knowledge graph instead of paying for web_search tokens.

v0.1 is shipped (MCP server + REST API + 47 cited claims). v0.2 adds
the `ask` endpoint your agent would actually hit; ETA 4–6 weeks. I want
5 named devs lined up to test it the week it lands. You're on my shortlist.

What I need from you:
  - A "yes I'm in" reply now so I add you to the early-access list
  - ~30 min once v0.2 ships (probably 2 × 15 min sessions)
  - One real LangChain / Claude Agent SDK / AutoGen agent to point at it
  - Honest feedback — what surprised you, what was broken, what you wish
    it did

What you get:
  - Early access the day v0.2 lands
  - A direct line to me (DM / email; you set the channel)
  - Your name in the README's "first users" if you want it
  - Once Stage 22 ships, a hosted demo at try.ayiru.dev for kicking
    the tyres without installing anything

Yes / no — that's the only question I need answered today. Onboarding
instructions arrive the day v0.2 ships.

Thanks for considering it,
[your name]
```

---

## What to track

Spreadsheet (or Notion / Linear ticket) with one row per contact:

| Name | Channel | Date contacted | Response | Status |
|---|---|---|---|---|
| Alice | Anthropic Discord | 2026-05-20 | Yes, has LangGraph agent | committed |
| Bob | r/LangChain | 2026-05-20 | "remind me later" | follow up Day 4 |
| Carol | Twitter DM | 2026-05-21 | No reply | drop after Day 7 |

Status values: `contacted` → `replied` → `committed` → `tested` → `feedback in`.

**Decision Gate row to mark "committed":** they replied "yes" AND named the
agent harness they'll point at it. "Sounds cool" without an agent named is
not a commitment.

---

## What to do if you can't get to 5

- **Drop to 3 committed + 7 contacted-but-pending.** The pre-launch
  checklist still passes if the 3 are real and you have a queue.
- **Re-scope the launch.** No hosted demo, no Stripe; just PyPI + README.
  Smaller surface, lower customer-acquisition pressure.
- **Delay one week.** Cold launches without seeded users get worse, not
  better, with time pressure. A two-week delay to land 5 is cheaper than
  a Show HN that bombs.

---

## After they say yes

**Today (during recruitment, before v0.2 ships):**

1. Add them to `docs/beta_tester_pipeline.md` with `committed` status and the agent harness they named (LangChain / Claude SDK / AutoGen / other).
2. Reply with a 2-line acknowledgement: *"You're on the list — I'll DM you the day v0.2 lands. In the meantime here's the repo if you want to see how v0.1 works: github.com/ruth411/ayiru."*
3. Pin their contact channel in your notes — one channel per tester, not five.

**The day v0.2 ships (Stage 22.3 / Stage 23.1):**

1. Either the hosted demo URL (once Stage 22.4 is live) or `pip install ayiru-client` (once Stage 22.3 publishes).
2. A 3-line "what to try" script:
   ```python
   from ayiru_client import Ayiru
   atlas = Ayiru(base_url="https://try.ayiru.dev")  # or your local API
   print(atlas.ask("how do I delete a docker volume").statement)
   ```
3. A single specific ask: *"After your first 10 queries, tell me one thing that worked and one thing that didn't."*

That's the entire onboarding. Anything more is friction.

---

## What "feedback in" looks like

You're done with a tester when you can answer all three:

1. Did they actually call `ask()` against a real agent? (yes/no)
2. What was their first impression of the answer quality? (free-text quote)
3. Would they install the production version when it ships? (yes/no/maybe)

If you have 3+ "yes/positive/yes" responses by launch day, the Decision
Gate's beta-tester criterion is satisfied. If you have < 3, you're
launching with no audience — re-read the kill criteria in roadmap_v0.2.md.
