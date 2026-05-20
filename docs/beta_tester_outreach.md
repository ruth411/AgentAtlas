# Phase 0.4 — Beta-tester outreach playbook

> **Purpose:** Recruit 5 named agent developers who commit (in writing) to
> running v0.2 against their real agent within 7 days of A1 shipping.
> If you can't get 5 in a week, the launch has no audience. Reduce scope
> to "show the code" or delay until recruitment works.

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

Short, specific, asks for one explicit yes/no:

```
hey [name] — saw your post about [specific thread / project that names a real
LLM cost or tool-choice problem].

I'm building a local search box for AI agents — cuts your agent's tool-call
bill by routing common dev/CLI/API questions to a verified knowledge graph
instead of paying for web_search tokens.

I'm looking for 5 devs to try it against a real agent next week before I
launch. ~30 min of your time over a week, you get:

- early access + a direct line to me (this DM)
- your name in the README's "first users" section if you want it
- no NDAs, no commercials, MIT-licensed

interested? happy to send the install instructions when you say yes.
```

**What works:** specific reference to their post, concrete time ask (30 min),
single yes/no question, no marketing language.

**What doesn't:** "let me know your thoughts," "would love to chat," any
phrase a recruiter would use.

---

## Email template (for known contacts)

```
Subject: 30 min next week — testing Ayiru before launch?

Hi [name],

I'm about to launch Ayiru — an open-source local search box for AI
agents that cuts tool-call costs by routing common dev/CLI/API questions
to a verified knowledge graph instead of paying for web_search tokens.

Before I open it up, I want 5 named devs to put it in front of a real
agent for a week and tell me what breaks. You're on my shortlist.

What I need from you:
  - ~30 min over 7 days (probably 2 × 15 min sessions)
  - One real LangChain / Claude Agent SDK / AutoGen agent to point at it
  - Honest feedback — what surprised you, what was broken, what you wish
    it did

What you get:
  - Early access to v0.2
  - A direct line to me (DM / email; you set the channel)
  - Your name in the README's "first users" if you want it
  - The hosted demo at try.ayiru.dev (no install required for kicking
    the tyres)

Yes / no — that's the only question I need answered today. Setup
instructions follow if you say yes.

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

Send within 60 minutes:

1. The hosted demo URL (or `pip install ayiru-client` once renamed).
2. A 3-line "what to try" script:
   ```
   from ayiru_client import Ayiru
   atlas = Ayiru(base_url="https://try.ayiru.dev")
   print(atlas.ask("how do I delete a docker volume").statement)
   ```
3. Your Discord handle / email — one channel, not five.
4. A single specific ask: *"After your first 10 queries, tell me one thing
   that worked and one thing that didn't."*

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
