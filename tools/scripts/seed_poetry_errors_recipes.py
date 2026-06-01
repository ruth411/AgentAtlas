"""Direct-insert poetry error + recipe claims."""

from datetime import datetime, timezone
from hashlib import sha256

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import get_claim_store
from app.services.embedding_service import (
    EmbeddingService,
    claim_embedding_text,
    serialize_embedding,
)
from app.services.orchestrator import CanonOrchestrator

BASE = "https://python-poetry.org/docs"

CLAIMS = [
    # ============================================================
    # poetry-errors — 30 common errors
    # ============================================================
    (
        "poetry-errors", "poetry error: Couldn't find a Python interpreter for poetry to use",
        "Poetry can't find a compatible Python on PATH. "
        "Fix: (1) ensure Python is installed: `python3 --version` and `which python3`; "
        "(2) tell poetry which to use: `poetry env use python3.12`; "
        "(3) point to specific interpreter: `poetry env use /usr/local/bin/python3.12`; "
        "(4) for pyenv/asdf/mise managed: shim must be on PATH before invoking poetry; "
        "(5) check what poetry sees: `poetry env info`.",
        f"{BASE}/managing-environments/",
    ),
    (
        "poetry-errors", "poetry error: The current project's supported Python range (^3.X) is not compatible with some of the required packages",
        "A dependency requires a different Python version than your project's `python = \"...\"` allows. "
        "Fix: (1) loosen project Python: e.g. `python = \"^3.9\"` instead of `^3.12`; "
        "(2) constrain the dep: pin to a version that supports your Python: `package = {version = \"<2.0\", python = \"<3.13\"}`; "
        "(3) check what each dep wants: `poetry show <pkg>` shows its python constraint; "
        "(4) for older Python support: many packages drop Python 3.8 / 3.9 — review upstream changelog.",
        f"{BASE}/dependency-specification/",
    ),
    (
        "poetry-errors", "poetry error: SolverProblemError / Because <X> depends on <Y> which conflicts with <Z>",
        "Dependency conflict. Poetry's solver tells you exactly what conflicts. "
        "Fix: (1) read the error carefully — it names the offending packages; "
        "(2) loosen one of the constraints: e.g. drop `^1.2.3` to `>=1.0`; "
        "(3) use dependency groups to isolate dev vs prod: `[tool.poetry.group.dev.dependencies]`; "
        "(4) override transitive: `[tool.poetry.dependencies] some-transitive = \"^X.Y\"`; "
        "(5) for stubborn conflicts: try `poetry lock --no-update` to keep existing pins.",
        f"{BASE}/managing-dependencies/",
    ),
    (
        "poetry-errors", "poetry error: Could not find a version of <pkg> that satisfies the constraint",
        "No version on the configured registry matches your range. "
        "Fix: (1) check available: `poetry search <pkg>` or PyPI page; "
        "(2) typo check: `package-name` vs `package_name` matter; "
        "(3) for private packages: configure the repo: `poetry source add private https://...`; "
        "(4) for git deps: `poetry add git+https://github.com/user/repo.git`; "
        "(5) for pre-releases: `poetry add --allow-prereleases <pkg>`.",
        f"{BASE}/repositories/",
    ),
    (
        "poetry-errors", "poetry error: The lock file is not consistent with pyproject.toml",
        "pyproject.toml has deps the lockfile doesn't reflect (or vice versa). "
        "Fix: (1) regenerate: `poetry lock` updates poetry.lock to match pyproject.toml; "
        "(2) `poetry lock --no-update` keeps versions but reconciles; "
        "(3) `poetry install --sync` to make env match lockfile; "
        "(4) commit both `pyproject.toml` AND `poetry.lock` together; "
        "(5) for CI: `poetry check --lock` fails if drift detected.",
        f"{BASE}/cli/#lock",
    ),
    (
        "poetry-errors", "poetry error: The lock file is not up to date with the latest changes",
        "Similar to the consistency error but specifically about `poetry install` refusing stale lock. "
        "Fix: (1) `poetry lock --no-update` to refresh; "
        "(2) `poetry install` after lock update; "
        "(3) check what changed: `git diff pyproject.toml poetry.lock`; "
        "(4) for CI: lockfile and pyproject.toml MUST be in sync — fail fast if not.",
        f"{BASE}/cli/#install",
    ),
    (
        "poetry-errors", "poetry error: Could not parse the pyproject.toml file / Invalid TOML",
        "TOML syntax error in pyproject.toml. "
        "Fix: (1) test parse: `python -c 'import tomllib; tomllib.load(open(\"pyproject.toml\",\"rb\"))'`; "
        "(2) common: missing quotes around strings, trailing commas in arrays (TOML doesn't allow), wrong indentation; "
        "(3) for poetry-specific schema: `poetry check` validates; "
        "(4) verify section names: `[tool.poetry.dependencies]` not `[tool.poetry.dependency]`; "
        "(5) use a TOML-aware editor for syntax highlighting.",
        f"{BASE}/pyproject/",
    ),
    (
        "poetry-errors", "poetry error: PEP 517 build failed / build backend error",
        "Building a dep failed during install (usually C extensions). "
        "Fix: (1) read the actual compile error (usually 'header.h not found' or similar); "
        "(2) install build tools: macOS `xcode-select --install`; Linux `apt install build-essential python3-dev`; "
        "(3) for specific deps: install system libs (`libpq-dev` for psycopg2, `libxml2-dev` for lxml); "
        "(4) prefer wheels: `poetry add --no-binary=:none: ...` is wrong — drop it to allow wheels; "
        "(5) for Apple Silicon: some packages need `arch -arm64` prefix.",
        f"{BASE}/managing-dependencies/",
    ),
    (
        "poetry-errors", "poetry error: Authentication required / repository 'X' authentication failure",
        "Private repo credentials missing or wrong. "
        "Fix: (1) configure: `poetry config http-basic.<repo-name> <user> <password>`; "
        "(2) via env: `POETRY_HTTP_BASIC_<REPO>_USERNAME` + `_PASSWORD`; "
        "(3) for tokens: `poetry config pypi-token.<repo-name> <token>`; "
        "(4) check current: `poetry config --list | grep http-basic`; "
        "(5) for keyring backend: `pip install keyring && keyring set ...`.",
        f"{BASE}/repositories/",
    ),
    (
        "poetry-errors", "poetry error: Connection failed / read timeout / DNS",
        "Network problem reaching the configured registry. "
        "Fix: (1) check connectivity: `curl https://pypi.org`; "
        "(2) corporate proxy: `poetry config http-proxy.http http://proxy:8080`; or env `HTTPS_PROXY=...`; "
        "(3) bump timeout: `poetry config request-timeout 60`; "
        "(4) DNS: `nslookup pypi.org` and `files.pythonhosted.org`; "
        "(5) for mirror: `poetry source add --priority=primary pypi-mirror https://mirror.example.com/simple/`.",
        f"{BASE}/configuration/",
    ),
    (
        "poetry-errors", "poetry error: Repository 'X' is not defined",
        "Tried to publish/install from a repo that isn't configured. "
        "Fix: (1) add it: `poetry source add private-pypi https://pypi.private.com/simple/`; "
        "(2) list configured: `poetry source show`; "
        "(3) for one-off publish: `poetry publish --repository private-pypi`; "
        "(4) common: typo in repo name — `pypi-test` vs `testpypi`.",
        f"{BASE}/repositories/",
    ),
    (
        "poetry-errors", "poetry error: EnvCommandError / Command 'X' returned non-zero",
        "A subprocess (compiler, pip, etc.) failed during install. "
        "Fix: (1) scroll up — the real error is above; "
        "(2) verbose mode: `poetry install -vvv` shows full output; "
        "(3) try outside poetry: `poetry run pip install <pkg>` (uses poetry's env but pip directly); "
        "(4) for build issues: see PEP 517 build failed recipe.",
        f"{BASE}/cli/#install",
    ),
    (
        "poetry-errors", "poetry error: Project version is not a valid PEP 440 version",
        "Version string in pyproject.toml doesn't match PEP 440. "
        "Fix: (1) valid: `1.2.3`, `1.2.3a1`, `1.2.3.dev0`, `1.2.3+local`; "
        "(2) invalid: `1.2.3-rc1` (use `1.2.3rc1`), `v1.2.3` (drop the v); "
        "(3) bump: `poetry version patch` / `minor` / `major` / `prepatch` / `preminor`; "
        "(4) inspect current: `poetry version`.",
        f"{BASE}/cli/#version",
    ),
    (
        "poetry-errors", "poetry error: Could not parse version constraint",
        "Dependency version range is invalid. "
        "Fix: (1) valid: `^1.0`, `~1.0`, `>=1.0,<2.0`, `1.0.*`, `==1.0`; "
        "(2) invalid: `1.0+` (no plus operator in poetry), `latest` (always pin); "
        "(3) compound: `\"requests\": {version = \"^2.0\", python = \"<3.13\"}`; "
        "(4) git: `{git = \"...\", branch = \"main\"}`; "
        "(5) local: `{path = \"./my-pkg\"}` or `{path = \"./my-pkg\", develop = true}` (editable).",
        f"{BASE}/dependency-specification/",
    ),
    (
        "poetry-errors", "poetry error: Failed to authenticate with PyPI on publish",
        "Token or credentials missing / wrong for PyPI upload. "
        "Fix: (1) get a PyPI API token: pypi.org → Account settings → API tokens; "
        "(2) configure: `poetry config pypi-token.pypi pypi-AgEIcHlwaS5vcmcCJ...`; "
        "(3) for TestPyPI: `poetry config repositories.testpypi https://test.pypi.org/legacy/` then `poetry config pypi-token.testpypi <token>`; "
        "(4) verify: `poetry publish --dry-run` shows what would happen; "
        "(5) 2FA required on accounts — use scoped tokens.",
        f"{BASE}/cli/#publish",
    ),
    (
        "poetry-errors", "poetry error: SSL: CERTIFICATE_VERIFY_FAILED",
        "TLS cert validation failed reaching a registry. "
        "Fix: (1) update certifi: `poetry self update` (refreshes bundled CA); "
        "(2) for corporate proxy MITM: install proxy CA into system trust store + `poetry config certificates.<repo>.cert /path/to/ca.pem`; "
        "(3) disable verification per-repo (insecure, dev only): `poetry config certificates.<repo>.client-cert false`; "
        "(4) check clock: `date` should be reasonable; "
        "(5) common with intermediate CA on private PyPI: server must serve full chain.",
        f"{BASE}/repositories/",
    ),
    (
        "poetry-errors", "poetry error: Plugin not found",
        "Tried to use a plugin that isn't installed. "
        "Fix: (1) install: `poetry self add poetry-plugin-<name>`; "
        "(2) common plugins: `poetry-plugin-export` (1.2+ requires this for `poetry export`), `poetry-plugin-shell` (1.3+ for `poetry shell`); "
        "(3) list installed plugins: `poetry self show plugins`; "
        "(4) update plugin: `poetry self update --plugins`.",
        f"{BASE}/plugins/",
    ),
    (
        "poetry-errors", "poetry error: poetry-core / build backend version mismatch",
        "pyproject.toml's `[build-system]` declares a poetry-core version your installed poetry doesn't match. "
        "Fix: (1) align: `requires = [\"poetry-core>=1.0.0\"]` for older, `\">=1.5.0\"` for poetry 1.5+; "
        "(2) for poetry 2.0: `\"poetry-core>=2.0.0\"`; "
        "(3) update poetry: `poetry self update`; "
        "(4) check core version: `poetry --version` reports both poetry + poetry-core.",
        f"{BASE}/libraries/",
    ),
    (
        "poetry-errors", "poetry error: extras not declared in pyproject.toml",
        "Tried `poetry install --extras X` but X isn't defined. "
        "Fix: (1) declare extras: ```toml\\n[tool.poetry.extras]\\ndev = [\"pytest\", \"black\"]\\n```; "
        "(2) the extras list must reference deps that exist in `dependencies` table (as optional: `pytest = {version = \"*\", optional = true}`); "
        "(3) modern (1.2+): use dependency groups instead: `[tool.poetry.group.dev.dependencies]` (cleaner); "
        "(4) install: `poetry install --extras dev` or `--all-extras`.",
        f"{BASE}/managing-dependencies/",
    ),
    (
        "poetry-errors", "poetry error: Group 'X' is not defined",
        "`poetry install --with X` or `--without X` references a non-existent group. "
        "Fix: (1) list groups: check pyproject.toml `[tool.poetry.group.*.dependencies]` sections; "
        "(2) default groups: `main` (always installed) + `dev` (1.2+ default, was `dev-dependencies` in 1.0); "
        "(3) optional groups: `[tool.poetry.group.docs] optional = true` — only installed if `--with docs`; "
        "(4) install everything: `poetry install --all-groups` (2.0+) or `--with dev,docs,test`.",
        f"{BASE}/managing-dependencies/",
    ),
    (
        "poetry-errors", "poetry error: Could not find a virtualenv / no environment exists",
        "No env created yet for this project. "
        "Fix: (1) `poetry install` creates one automatically; "
        "(2) explicit: `poetry env use python3.12` then `poetry install`; "
        "(3) check status: `poetry env info`; "
        "(4) list envs for project: `poetry env list`; "
        "(5) env location: `poetry config virtualenvs.path` (default `~/Library/Caches/pypoetry/virtualenvs` on macOS).",
        f"{BASE}/managing-environments/",
    ),
    (
        "poetry-errors", "poetry error: virtualenv already exists in-project (.venv conflict)",
        "Mixing `virtualenvs.in-project=true` with existing `.venv` causes confusion. "
        "Fix: (1) decide where envs live: in-project (`.venv/`) or global cache; "
        "(2) toggle: `poetry config virtualenvs.in-project true` (or false); "
        "(3) remove old env: `poetry env remove <name>` or `rm -rf .venv`; "
        "(4) recreate: `poetry install`.",
        f"{BASE}/configuration/",
    ),
    (
        "poetry-errors", "poetry error: poetry shell removed in 2.0 / 'shell' is not a command",
        "Poetry 2.0 removed the `shell` command. "
        "Fix: (1) install the plugin: `poetry self add poetry-plugin-shell`; "
        "(2) modern alternatives: `poetry env activate` (returns shell command to source); "
        "(3) wrap a single command: `poetry run <cmd>` (no shell needed); "
        "(4) for direct activation: `source $(poetry env info --path)/bin/activate`.",
        f"{BASE}/cli/#shell",
    ),
    (
        "poetry-errors", "poetry error: poetry export removed / requirements.txt export fails",
        "Poetry 1.2+ moved `export` to a plugin. "
        "Fix: (1) install plugin: `poetry self add poetry-plugin-export`; "
        "(2) export: `poetry export -f requirements.txt -o requirements.txt --without-hashes`; "
        "(3) with hashes: drop `--without-hashes`; "
        "(4) for dev deps: `--with dev`; "
        "(5) for production deploys: prefer `poetry install --only main --no-root` directly.",
        f"{BASE}/cli/#export",
    ),
    (
        "poetry-errors", "poetry error: Out of disk space / cache cleanup needed",
        "Poetry's cache (~/.cache/pypoetry on Linux) can grow large. "
        "Fix: (1) inspect: `du -sh ~/.cache/pypoetry`; "
        "(2) clear: `poetry cache clear --all PyPI` (or other repo name); "
        "(3) clear all caches: `poetry cache clear --all .`; "
        "(4) list cached: `poetry cache list`; "
        "(5) move cache to bigger disk: `poetry config cache-dir /path/to/cache`.",
        f"{BASE}/cli/#cache",
    ),
    (
        "poetry-errors", "poetry error: poetry self update fails / version mismatch",
        "Poetry can't update itself due to install method conflict. "
        "Fix: (1) check install method: `poetry --version` and `which poetry`; "
        "(2) installed via curl script: `poetry self update`; "
        "(3) installed via pipx: use `pipx upgrade poetry` instead; "
        "(4) installed via pip: `pip install -U poetry`; "
        "(5) recommended: install poetry via official installer or pipx (isolated env).",
        f"{BASE}/cli/#self",
    ),
    (
        "poetry-errors", "poetry error: project does not declare itself as a package / 'name' or 'version' missing",
        "Poetry 1.x required name + version; 2.0 relaxed for 'app-only' projects but still needs them for libraries. "
        "Fix: (1) add to pyproject.toml: ```toml\\n[tool.poetry]\\nname = \"my-project\"\\nversion = \"0.1.0\"\\ndescription = \"\"\\nauthors = [\"You <you@x.com>\"]\\n```; "
        "(2) for app-only (poetry 2.0+, no publish): `[tool.poetry] package-mode = false`; "
        "(3) for `--no-root` install (skip project itself): useful for pure-app deploys.",
        f"{BASE}/pyproject/",
    ),
    (
        "poetry-errors", "poetry error: 'sync' is not a command (pre-1.5)",
        "`poetry sync` was added in 1.5+. Older versions use `install --sync` or `install --remove-untracked`. "
        "Fix: (1) check version: `poetry --version`; "
        "(2) upgrade: `poetry self update`; "
        "(3) workaround on old poetry: `poetry install --sync` (1.0-1.4) or `--remove-untracked` (much older); "
        "(4) modern (1.5+): `poetry sync` removes packages not in lockfile.",
        f"{BASE}/cli/#sync",
    ),
    (
        "poetry-errors", "poetry error: Cannot find a compatible Python interpreter (with pyenv)",
        "Pyenv shim resolves to a Python not matching project requirement. "
        "Fix: (1) install matching: `pyenv install 3.12.0`; "
        "(2) set for project: `pyenv local 3.12.0` (writes .python-version); "
        "(3) tell poetry: `poetry env use $(pyenv which python)`; "
        "(4) verify: `poetry env info` shows interpreter path; "
        "(5) for poetry to auto-detect pyenv: ensure pyenv init runs in shell before poetry invocation.",
        f"{BASE}/managing-environments/",
    ),
    (
        "poetry-errors", "poetry error: 'remove' / 'uninstall' subprocess error on Windows",
        "File locking on Windows (antivirus, IDE indexing) blocks deletion. "
        "Fix: (1) close IDE / dev server before `poetry remove`; "
        "(2) exclude project dir from antivirus real-time scanning; "
        "(3) use `--directory` flag to specify project: `poetry --directory=/path remove <pkg>`; "
        "(4) on failure: manually `rmdir /S /Q .venv` then `poetry install`.",
        f"{BASE}/cli/#remove",
    ),

    # ============================================================
    # poetry-recipes — 36 common workflows
    # ============================================================
    (
        "poetry-recipes", "poetry recipe: install poetry itself",
        "Official installer (recommended): `curl -sSL https://install.python-poetry.org | python3 -`. "
        "Adds ~/.local/bin to PATH (configure shell after). "
        "Alternative: `pipx install poetry` (isolated venv, easy to update). "
        "Specific version: `pipx install poetry==1.8.3`. "
        "Avoid `pip install poetry` in a project venv — pollutes deps. "
        "Verify: `poetry --version`.",
        f"{BASE}/",
    ),
    (
        "poetry-recipes", "poetry recipe: create a new project (poetry new)",
        "`poetry new my-project` scaffolds: package dir + `tests/` + pyproject.toml + README. "
        "Src layout: `poetry new --src my-project` (creates `src/my_project/`). "
        "Name vs path: `poetry new my-project --name custom_name` for different module name. "
        "Then `cd my-project && poetry install`. "
        "For interactive setup in existing dir: `poetry init` (writes pyproject.toml only).",
        f"{BASE}/cli/#new",
    ),
    (
        "poetry-recipes", "poetry recipe: add dependencies (basic + variants)",
        "Latest: `poetry add requests`. "
        "Specific: `poetry add 'requests==2.31.0'`. "
        "Range: `poetry add 'requests>=2.0,<3.0'`. "
        "Dev (1.2+): `poetry add --group dev pytest`. "
        "Optional + extras: `poetry add 'fastapi[all]'`. "
        "Git: `poetry add git+https://github.com/user/repo.git#main`. "
        "Path (editable): `poetry add --editable ./my-lib`. "
        "Pre-release: `poetry add --allow-prereleases <pkg>`.",
        f"{BASE}/cli/#add",
    ),
    (
        "poetry-recipes", "poetry recipe: use dependency groups (modern, 1.2+)",
        "pyproject.toml: ```toml\\n[tool.poetry.group.dev.dependencies]\\npytest = \"^7.0\"\\n\\n[tool.poetry.group.docs]\\noptional = true\\n\\n[tool.poetry.group.docs.dependencies]\\nsphinx = \"^7.0\"\\n```. "
        "Install only main: `poetry install --only main`. "
        "Install main + dev: `poetry install --with dev`. "
        "Skip dev (prod deploy): `poetry install --without dev`. "
        "Install optional group: `poetry install --with docs`.",
        f"{BASE}/managing-dependencies/",
    ),
    (
        "poetry-recipes", "poetry recipe: update packages",
        "All up to range: `poetry update`. "
        "Specific: `poetry update requests`. "
        "Lock without updating: `poetry lock --no-update` (refreshes metadata only). "
        "To latest (ignore range): edit pyproject.toml to bump bounds + `poetry update`. "
        "Verify what would change: `poetry update --dry-run`. "
        "After update: commit both pyproject.toml AND poetry.lock.",
        f"{BASE}/cli/#update",
    ),
    (
        "poetry-recipes", "poetry recipe: install from lockfile (reproducible)",
        "Standard: `poetry install` (uses lockfile, respects pyproject.toml). "
        "Strict reproducibility (sync, 1.5+): `poetry sync` removes anything not in lockfile. "
        "Production-only: `poetry install --only main --no-root` (no dev deps, doesn't install your package). "
        "Frozen lockfile (CI, fail if drift): `poetry install --no-update` plus `poetry check --lock`.",
        f"{BASE}/cli/#install",
    ),
    (
        "poetry-recipes", "poetry recipe: regenerate lockfile",
        "`poetry lock` — full re-resolution (slow, picks fresh versions). "
        "`poetry lock --no-update` — keep existing versions, just refresh metadata + integrity hashes. "
        "After dep changes: `poetry lock` + commit. "
        "For pyproject.toml-only changes (no version bumps): `--no-update` is safer + faster. "
        "Always commit `poetry.lock` to version control.",
        f"{BASE}/cli/#lock",
    ),
    (
        "poetry-recipes", "poetry recipe: run a command in the project env",
        "`poetry run <command>` runs in the project's virtualenv. "
        "Common: `poetry run pytest`, `poetry run python script.py`, `poetry run mypy .`. "
        "Why over `poetry shell`: doesn't require activating subshell; works in CI. "
        "For pytest scripts: define entry point in pyproject.toml + use `poetry run my-cli`.",
        f"{BASE}/cli/#run",
    ),
    (
        "poetry-recipes", "poetry recipe: activate the project env",
        "Poetry 1.x: `poetry shell` (spawns subshell with env activated). "
        "Poetry 2.0+: `poetry env activate` prints the activation command; eval it: `eval $(poetry env activate)`. "
        "Or install plugin for backward compat: `poetry self add poetry-plugin-shell`. "
        "Manual: `source $(poetry env info --path)/bin/activate`. "
        "Verify: `which python` should be in `.venv/bin/python`.",
        f"{BASE}/cli/#shell",
    ),
    (
        "poetry-recipes", "poetry recipe: show dependency tree / why is X installed",
        "Tree: `poetry show --tree` (full dependency graph). "
        "Reverse: `poetry show --tree <pkg>` shows ancestors. "
        "Outdated: `poetry show --outdated` lists packages with newer versions. "
        "Specific package: `poetry show requests` (version + deps + summary). "
        "Latest available: `poetry show --latest`.",
        f"{BASE}/cli/#show",
    ),
    (
        "poetry-recipes", "poetry recipe: export to requirements.txt (for legacy tools)",
        "Requires plugin (1.2+): `poetry self add poetry-plugin-export`. "
        "Basic: `poetry export -f requirements.txt -o requirements.txt`. "
        "Without hashes: `--without-hashes` (smaller, less secure). "
        "With dev deps: `--with dev`. "
        "Specific groups: `--only main` or `--with docs`. "
        "Use case: deploying to platforms that don't speak poetry (some PaaS, AWS Lambda).",
        f"{BASE}/cli/#export",
    ),
    (
        "poetry-recipes", "poetry recipe: build a package (sdist + wheel)",
        "`poetry build` creates `dist/<pkg>-<version>.tar.gz` + `<pkg>-<version>-py3-none-any.whl`. "
        "Specific format: `poetry build --format sdist` or `--format wheel`. "
        "Output dir: `--output ./custom-dist`. "
        "Inspect wheel: `unzip -l dist/<wheel>.whl`. "
        "Test install: `pip install dist/<pkg>.whl` in a clean env.",
        f"{BASE}/cli/#build",
    ),
    (
        "poetry-recipes", "poetry recipe: publish to PyPI",
        "(1) Build: `poetry build`. "
        "(2) Configure token: `poetry config pypi-token.pypi pypi-AgE...`. "
        "(3) Publish: `poetry publish`. "
        "Test first on TestPyPI: ```bash\\npoetry config repositories.testpypi https://test.pypi.org/legacy/\\npoetry config pypi-token.testpypi <token>\\npoetry publish --repository testpypi\\n```. "
        "One-shot (build + publish): `poetry publish --build`. "
        "Dry-run: `--dry-run`.",
        f"{BASE}/cli/#publish",
    ),
    (
        "poetry-recipes", "poetry recipe: configure a private repository",
        "Add: `poetry source add --priority=supplemental private https://pypi.private.com/simple/`. "
        "Priorities (poetry 1.5+): `primary` (search here only), `supplemental` (fall back to here), `explicit` (only for tagged deps). "
        "Auth: `poetry config http-basic.private <user> <password>` OR `poetry config pypi-token.private <token>`. "
        "Use: `poetry add private-pkg --source private` (force this source). "
        "List sources: `poetry source show`.",
        f"{BASE}/repositories/",
    ),
    (
        "poetry-recipes", "poetry recipe: switch Python interpreter",
        "`poetry env use python3.12` (or full path: `/usr/local/bin/python3.12`). "
        "Each project can have multiple envs (one per Python version). "
        "List: `poetry env list` shows all envs for current project. "
        "Active env info: `poetry env info`. "
        "Remove an env: `poetry env remove python3.11` or `poetry env remove <env-name>`.",
        f"{BASE}/managing-environments/",
    ),
    (
        "poetry-recipes", "poetry recipe: use in-project virtualenv (.venv at project root)",
        "Enable globally: `poetry config virtualenvs.in-project true`. "
        "Now `poetry install` creates `.venv/` in the project (instead of cache dir). "
        "Add to .gitignore: `.venv/`. "
        "Benefits: IDE auto-discovery, easier to clean, no cache pollution. "
        "Disable: `poetry config virtualenvs.in-project false`.",
        f"{BASE}/configuration/",
    ),
    (
        "poetry-recipes", "poetry recipe: bump version",
        "By component: `poetry version patch` (1.0.0 → 1.0.1), `minor` (1.0.0 → 1.1.0), `major` (1.0.0 → 2.0.0). "
        "Pre-releases: `prepatch` (1.0.0 → 1.0.1a0), `preminor`, `premajor`. "
        "Specific: `poetry version 2.5.0`. "
        "Read current: `poetry version` (or `--short` for just the number). "
        "Updates `[tool.poetry] version = ...` in pyproject.toml.",
        f"{BASE}/cli/#version",
    ),
    (
        "poetry-recipes", "poetry recipe: run pytest in the project env",
        "Direct: `poetry run pytest`. "
        "With args: `poetry run pytest tests/ -v -k test_foo`. "
        "In an entry script (pyproject.toml): ```toml\\n[tool.poetry.scripts]\\ntest = \"pytest:main\"\\n``` then `poetry run test`. "
        "For CI: `poetry install --with test && poetry run pytest --cov`.",
        f"{BASE}/cli/#run",
    ),
    (
        "poetry-recipes", "poetry recipe: CI install (reproducible, no prompts, no project install)",
        "`poetry install --no-interaction --no-root --only main`. "
        "`--no-interaction` skips prompts. "
        "`--no-root` skips installing the project itself (useful for Docker layer caching). "
        "`--only main` skips dev groups (production deploy). "
        "Combine with strict lockfile check: `poetry check --lock` first (fails if drift). "
        "Cache `~/.cache/pypoetry` between CI runs for speed.",
        f"{BASE}/cli/#install",
    ),
    (
        "poetry-recipes", "poetry recipe: install plugins",
        "`poetry self add poetry-plugin-export` (export to requirements.txt). "
        "`poetry self add poetry-plugin-shell` (legacy `poetry shell` command). "
        "`poetry self add poetry-dynamic-versioning` (version from git tags). "
        "List installed: `poetry self show plugins`. "
        "Update all plugins: `poetry self update --plugins`. "
        "Remove: `poetry self remove poetry-plugin-export`.",
        f"{BASE}/plugins/",
    ),
    (
        "poetry-recipes", "poetry recipe: migrate from requirements.txt",
        "(1) `poetry init` (interactive) — creates pyproject.toml; "
        "(2) for each line in requirements.txt: `poetry add <pkg>` (or `cat requirements.txt | xargs -L 1 poetry add`); "
        "(3) for dev deps: `poetry add --group dev <pkgs>`; "
        "(4) commit pyproject.toml + poetry.lock; "
        "(5) update CI: replace `pip install -r requirements.txt` with `poetry install`; "
        "(6) keep requirements.txt during transition: `poetry export -o requirements.txt`.",
        f"{BASE}/basic-usage/",
    ),
    (
        "poetry-recipes", "poetry recipe: migrate from setup.py / setup.cfg",
        "(1) `poetry init` to scaffold pyproject.toml; "
        "(2) move metadata: name, version, description, authors → `[tool.poetry]`; "
        "(3) move install_requires → `[tool.poetry.dependencies]` (each as a line); "
        "(4) move extras_require → `[tool.poetry.extras]` (or modern groups); "
        "(5) move scripts/entry_points → `[tool.poetry.scripts]`; "
        "(6) for setuptools-specific (cython, etc.): may need to keep setuptools as a fallback.",
        f"{BASE}/pyproject/",
    ),
    (
        "poetry-recipes", "poetry recipe: use src layout",
        "Modern best practice — code lives in `src/my_package/` (not at repo root). "
        "Generate: `poetry new --src my-project`. "
        "For existing: move code into `src/<package_name>/` and update pyproject.toml: ```toml\\n[tool.poetry]\\npackages = [{include = \"my_package\", from = \"src\"}]\\n```. "
        "Why: prevents `import my_package` from working without install — catches missing dep declarations.",
        f"{BASE}/pyproject/",
    ),
    (
        "poetry-recipes", "poetry recipe: define entry-point scripts (CLI commands)",
        "In pyproject.toml: ```toml\\n[tool.poetry.scripts]\\nmy-cli = \"my_package.cli:main\"\\nlint = \"my_package.lint:run\"\\n```. "
        "After `poetry install`: `my-cli` is in `.venv/bin/`. "
        "Run via poetry: `poetry run my-cli` or after `poetry shell`/activate, just `my-cli`. "
        "Module-callable syntax: `module.submodule:function`.",
        f"{BASE}/pyproject/",
    ),
    (
        "poetry-recipes", "poetry recipe: pre-commit hook for poetry",
        "Use the `python-poetry/poetry` repo's pre-commit hooks. "
        "`.pre-commit-config.yaml`: ```yaml\\nrepos:\\n  - repo: https://github.com/python-poetry/poetry\\n    rev: 1.8.3\\n    hooks:\\n      - id: poetry-check\\n      - id: poetry-lock\\n      - id: poetry-export\\n        args: [\"-f\", \"requirements.txt\", \"-o\", \"requirements.txt\"]\\n```. "
        "Install: `pre-commit install`. "
        "Auto-runs `poetry check` (validate pyproject.toml) + `poetry lock` (keep lockfile fresh) on commits.",
        f"{BASE}/pre-commit-hooks/",
    ),
    (
        "poetry-recipes", "poetry recipe: configure cache directory",
        "Default: `~/.cache/pypoetry` (Linux), `~/Library/Caches/pypoetry` (macOS), `%LOCALAPPDATA%\\pypoetry\\Cache` (Windows). "
        "Change: `poetry config cache-dir /custom/path`. "
        "Show current: `poetry config cache-dir`. "
        "List cached: `poetry cache list`. "
        "Clear: `poetry cache clear --all PyPI`. "
        "CI: cache this dir across runs for faster installs.",
        f"{BASE}/configuration/",
    ),
    (
        "poetry-recipes", "poetry recipe: deploy with Docker (multi-stage)",
        "```dockerfile\\nFROM python:3.12-slim AS builder\\nRUN pip install poetry==1.8.3\\nWORKDIR /app\\nCOPY pyproject.toml poetry.lock ./\\nRUN poetry config virtualenvs.in-project true && \\\\\\n    poetry install --only main --no-root --no-interaction\\n\\nFROM python:3.12-slim\\nWORKDIR /app\\nCOPY --from=builder /app/.venv /app/.venv\\nCOPY ./src ./src\\nENV PATH=\"/app/.venv/bin:$PATH\"\\nCMD [\"python\", \"-m\", \"my_package\"]\\n```. "
        "Key: `--only main --no-root` keeps the image lean.",
        f"{BASE}/cli/#install",
    ),
    (
        "poetry-recipes", "poetry recipe: lock for production (poetry sync)",
        "Best practice for prod deploys: `poetry sync --only main --no-root`. "
        "`sync` (1.5+) removes anything in env not in lockfile (strict reproducibility). "
        "Compared to `install`: `install` is additive (won't remove); `sync` is exact-match. "
        "Use case: container builds, fresh deploy envs. "
        "On older poetry (pre-1.5): `poetry install --sync`.",
        f"{BASE}/cli/#sync",
    ),
    (
        "poetry-recipes", "poetry recipe: configure environment for CI",
        "Env vars (no `poetry config` needed): `POETRY_VIRTUALENVS_IN_PROJECT=true`, `POETRY_VIRTUALENVS_CREATE=true`, `POETRY_NO_INTERACTION=1`. "
        "For auth: `POETRY_HTTP_BASIC_<REPO>_USERNAME` + `_PASSWORD` (uppercase repo name). "
        "GitHub Actions example: ```yaml\\nenv:\\n  POETRY_VIRTUALENVS_IN_PROJECT: 'true'\\n  POETRY_NO_INTERACTION: '1'\\n```. "
        "Then: `poetry install --no-root --only main` runs without prompts.",
        f"{BASE}/configuration/",
    ),
    (
        "poetry-recipes", "poetry recipe: lockfile-only resolve (CI dependency check)",
        "Verify lockfile is consistent without installing: `poetry check --lock` (1.5+) fails if drift. "
        "Lock without installing: `poetry lock --no-update` (refresh metadata only). "
        "Re-resolve fully: `poetry lock`. "
        "Use case: CI step that fails fast on uncommitted lockfile changes — before the slow install step.",
        f"{BASE}/cli/#check",
    ),
    (
        "poetry-recipes", "poetry recipe: dependency from git with extras + ref",
        "```toml\\n[tool.poetry.dependencies]\\nmy-lib = {git = \"https://github.com/user/repo.git\", branch = \"main\"}\\nother-lib = {git = \"https://github.com/user/repo.git\", tag = \"v1.2.3\", extras = [\"async\"]}\\nthird-lib = {git = \"https://github.com/user/repo.git\", rev = \"abc1234\"}\\n```. "
        "Subdir (monorepo): `subdirectory = \"packages/my-pkg\"`. "
        "For private repos: configure SSH key or use `git+ssh://git@github.com:user/repo.git`.",
        f"{BASE}/dependency-specification/",
    ),
    (
        "poetry-recipes", "poetry recipe: use a local editable dependency",
        "`poetry add --editable ../my-lib`. "
        "In pyproject.toml: `my-lib = {path = \"../my-lib\", develop = true}`. "
        "Local changes immediately reflected (no reinstall). "
        "Use case: developing two related libs in parallel without publishing. "
        "Alternative: workspace setup (poetry doesn't have native workspaces — use uv or pdm if you need them).",
        f"{BASE}/dependency-specification/",
    ),
    (
        "poetry-recipes", "poetry recipe: handle optional dependencies (extras)",
        "Define in pyproject.toml: ```toml\\n[tool.poetry.dependencies]\\nredis = {version = \"^4.0\", optional = true}\\npostgres = {version = \"^2.9\", optional = true}\\n\\n[tool.poetry.extras]\\ncache = [\"redis\"]\\ndb = [\"postgres\"]\\nall = [\"redis\", \"postgres\"]\\n```. "
        "Install with extra: `poetry install --extras cache`. "
        "All extras: `poetry install --all-extras`. "
        "For consumers: `pip install your-pkg[cache]`.",
        f"{BASE}/managing-dependencies/",
    ),
    (
        "poetry-recipes", "poetry recipe: switch to uv (faster alternative)",
        "uv from Astral is 10-100x faster + handles pyproject.toml natively. "
        "Migration: keep pyproject.toml as-is, replace commands. "
        "`poetry install` → `uv sync`. "
        "`poetry add <pkg>` → `uv add <pkg>`. "
        "`poetry lock` → `uv lock`. "
        "`poetry run <cmd>` → `uv run <cmd>`. "
        "uv writes its own `uv.lock` (more compact than poetry.lock).",
        f"{BASE}/",
    ),
    (
        "poetry-recipes", "poetry recipe: production deployment best practices",
        "(1) Pin poetry version in CI: `pip install poetry==1.8.3` (consistent behavior). "
        "(2) Always commit `poetry.lock`. "
        "(3) CI install: `poetry install --no-interaction --no-root --only main`. "
        "(4) Verify lockfile in CI: `poetry check --lock` fails if drift. "
        "(5) Use `--no-cache` in Docker builds to keep images small. "
        "(6) For wheels-only deploys: `poetry build` then deploy the wheel.",
        f"{BASE}/configuration/",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (tool_id, subject, statement, evidence_url) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-poetry-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-poetry-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="poetry-catalog",
            evidence=[evidence],
            risk_level=RiskLevel.LOW,
            created_at=now,
        )
        try:
            store.create(claim)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ #{suffix} create: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        try:
            verification = orchestrator.verify_claim(claim)
            store.save_verification_result(verification)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ #{suffix} verify: {type(exc).__name__}: {exc}")
        text = claim_embedding_text(subject=subject, statement=statement)
        vec = svc.embed_one(text)
        store.set_embedding(claim.claim_id, serialize_embedding(vec))
        ok += 1
        print(f"  ✓ #{suffix} [{tool_id}] {subject[:70]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()
