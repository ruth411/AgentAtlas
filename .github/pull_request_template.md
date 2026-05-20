<!--
  Thanks for sending a PR to Ayiru.

  Before you submit, please read CONTRIBUTING.md once. The non-negotiables
  for any PR live there. A PR that violates them gets closed with a link
  back, not a merge.
-->

## Summary

<!--
  One or two sentences. What changes, and *why*. The why is what reviewers
  optimise for. A PR with a great "what" and no "why" is a guess.
-->

## What this changes

- <!-- bulletted; user-visible behaviour, not implementation detail -->

## Test plan

<!--
  How you verified this works. Run the commands you ran. Paste the output
  if it's short.
-->

```
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

## Checklist

<!--
  Tick the boxes that apply. Untick the ones that don't — but if you're
  unticking one of the non-negotiables, the PR needs a conversation in
  the description about why.
-->

### Required (non-negotiable)
- [ ] New domain rules have tests covering them.
- [ ] `pytest -q` passes locally.
- [ ] `ruff check app tests` is clean.
- [ ] If this touches a migration, `alembic downgrade -1 && alembic upgrade head` works against a fresh DB.
- [ ] No safety rule was weakened (no expansion of `allowed_commands`, no widening of SSRF guards, no demotion of evidence-trust requirements).
- [ ] No LLM call was introduced in the safety path.

### Required (contracts)
- [ ] Any `contracts/*.json` change is a NEW versioned file (`*.v2.json`), not an in-place edit of `*.v1.json`.
- [ ] If the contract changed, the bundled copy under `backend/app/contracts/` matches the canonical copy at the repo root (lockstep test enforces this).

### Required (API)
- [ ] New endpoints are mounted under `/v1/` (or appropriately versioned).
- [ ] Errors use the structured `{"error": {"code", "message", "details"}}` envelope.
- [ ] Request/response models use `ConfigDict(extra="forbid", str_strip_whitespace=True)`.

### Recommended
- [ ] CHANGELOG.md entry added under `[Unreleased]`.
- [ ] If the change is user-visible, the README / relevant doc was updated in the same PR.
- [ ] If this fixes a bug from an issue, the PR description includes `Fixes #N`.

## Related issues

<!-- "Fixes #N" or "Refs #N". Multiple are fine. -->
