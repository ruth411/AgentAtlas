# Contributing to AgentAtlas

Thanks for your interest. AgentAtlas is an early-stage open-source project; a small amount of process makes it easier for maintainers to merge your contribution quickly.

## Ground rules

These rules are non-negotiable because the project's trust story depends on them:

1. **Safety rules never weaken.** Never expand `allowed_commands`, widen SSRF guards, or demote evidence-trust requirements without a separate design discussion.
2. **Contracts are versioned.** `contracts/*.v1.json` is locked. A behavioural change to any contract gets a new file (`*.v2.json`); the old file stays for replay compatibility.
3. **Tests are required for new domain rules.** If you're changing the orchestrator, risk engine, or ingestion lane, ship a test.
4. **Migrations stay reversible.** `alembic downgrade -1` must work after your migration.
5. **Bundled contracts + seed data stay in lockstep with the repo-root canonical copies.** Edit at the canonical location, then `cp` into the bundled directory (or rerun the helper). `test_bundled_contracts_in_sync.py` and `test_bundled_seed_in_sync.py` enforce this.

## Getting set up

```bash
git clone https://github.com/ruth411/AgentAtlas.git
cd AgentAtlas
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -e 'backend[dev]'

agentatlas seed --reset
agentatlas serve --reload
```

Before opening a PR:

```bash
cd backend
.venv/bin/python -m pytest      # must stay green
.venv/bin/ruff check app tests  # must stay clean
```

## Pull request checklist

- One concern per PR. A migration + a route change + a doc rewrite is three PRs.
- Tests added for any new behaviour.
- `pytest -q` and `ruff check app tests` both pass locally.
- If you changed a contract, you added a regression test and bumped the contract version (if behaviour changed).
- If you added a new public endpoint, the route is mounted under `/v1/` (Stage 14).
- The PR description explains *why* the change is needed, not just *what* it does.

## What to work on

Good first contributions:

- Add a new tool to `contracts/agentatlas_stage_0.v1.json`'s `initial_tools` list, then submit seed claims for it.
- Add a runtime verifier for a new claim type in `app/services/runtime_verifier.py`.
- Expand the headline scenarios in `data/seed_artifacts/claims/headline_scenarios.json`.
- Improve the demo dashboard (`frontend/`) — error states, dark mode, search-as-you-type.

Significant changes worth discussing before you start:

- Replacing SQLite with Postgres in the test matrix.
- Adding a new ingestion lane.
- Changing the verification-level promotion rules.
- Touching the safety policy.

Open an issue first. We'll save you the trouble of an unmergeable PR.

## Code style

- Python 3.11+ syntax. Type hints required on every public function.
- Pydantic v2 throughout. `ConfigDict(extra="forbid", str_strip_whitespace=True)` on every schema.
- One short comment per non-obvious decision. Multi-paragraph docstrings only on module headers.
- Tests use `tmp_path` for ephemeral SQLite DBs. Don't hit the dev DB from tests.

## Reporting bugs

File an issue with:

1. What you did (the exact command or curl).
2. What you expected.
3. What actually happened (full error + stack trace if any).
4. Your environment (Python version, OS, install method).

## Security issues

Please do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure path.

## License

By contributing, you agree that your contribution will be licensed under the MIT License — the same license as the project. See [LICENSE](LICENSE).
