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

## 2026-05-22 — first wire-up

| Prompt | Tool picked | Useful answer? | Notes / bugs |
|---|---|---|---|
| | | | |

**Bugs / UX gaps observed:**
-

**Questions that fell back (Stage 20 candidates):**
-

**LLM-side surprises:**
-

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
