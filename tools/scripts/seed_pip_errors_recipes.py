"""Direct-insert pip error + recipe claims."""

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

BASE = "https://pip.pypa.io/en/stable"

CLAIMS = [
    # ============================================================
    # pip-errors — 30 common errors
    # ============================================================
    (
        "pip-errors", "pip error: Could not find a version that satisfies the requirement / No matching distribution",
        "Package or version not on the configured index, OR no compatible wheel for your platform/Python. "
        "Fix: (1) typo check: `pip search` (removed) → use https://pypi.org/search/?q=<name>; "
        "(2) check available versions: `pip index versions <package>`; "
        "(3) for platform/Python mismatch: many packages don't have wheels for new Python — try older Python OR `--no-binary :all:` to build from source; "
        "(4) for private packages: add `--index-url` or `--extra-index-url`; "
        "(5) common: package renamed (e.g. `sklearn` → `scikit-learn`).",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: externally-managed-environment (PEP 668 — Debian/Ubuntu/Fedora)",
        "Modern distros block system-wide pip installs to prevent breaking the OS. "
        "Fix: (1) preferred: use a venv: `python3 -m venv .venv && source .venv/bin/activate && pip install ...`; "
        "(2) for CLI tools: use `pipx install <tool>` (isolated venv per tool); "
        "(3) for user-only: `pip install --user <pkg>`; "
        "(4) UNSAFE bypass: `pip install --break-system-packages <pkg>` (risks breaking apt-installed packages); "
        "(5) for containers/CI: edit `EXTERNALLY-MANAGED` file in stdlib site-packages dir — only if you own the image.",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-errors", "pip error: Could not install packages due to OSError: [Errno 13] Permission denied",
        "Trying to install system-wide without write perms. "
        "Fix: (1) use a venv (always preferred): `python -m venv .venv && source .venv/bin/activate`; "
        "(2) user install: `pip install --user <pkg>`; "
        "(3) sudo (NOT recommended): risks corrupting system Python; "
        "(4) for `--user`: ensure `~/.local/bin` is on PATH; "
        "(5) for Docker: run as USER non-root + write to user-owned dirs.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: ResolutionImpossible / conflicting dependencies",
        "Two installed packages require incompatible versions of a shared dep. "
        "Fix: (1) read the error — pip lists the conflict graph; "
        "(2) loosen pins: drop strict version constraints if possible; "
        "(3) use older pip resolver: `pip install --use-deprecated=legacy-resolver` (last resort — silent breakage); "
        "(4) for new project: pip-compile or uv to plan a consistent set; "
        "(5) for production: use `pip-tools` (`pip-compile requirements.in -o requirements.txt`) to lock a working set.",
        f"{BASE}/topics/dependency-resolution/",
    ),
    (
        "pip-errors", "pip error: Microsoft Visual C++ 14.0 or greater is required (Windows)",
        "Package needs to compile C extensions and you don't have a C compiler. "
        "Fix: (1) install Visual Studio Build Tools 2022 (free): https://visualstudio.microsoft.com/visual-cpp-build-tools/ — check 'C++ build tools' workload; "
        "(2) or try precompiled wheel: most popular packages have wheels — `pip install -U pip wheel setuptools` first to ensure modern resolver picks them; "
        "(3) for specific arch: `--only-binary :all:` forces wheel-only (errors if no wheel); "
        "(4) for some packages: conda has prebuilt binaries.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: gcc/clang not found / Python.h not found (Linux build)",
        "Package needs C build tools or Python dev headers. "
        "Fix: (1) Debian/Ubuntu: `apt install build-essential python3-dev`; "
        "(2) RHEL/Fedora: `dnf install gcc python3-devel`; "
        "(3) Alpine: `apk add gcc musl-dev python3-dev`; "
        "(4) for specific extensions (psycopg2, lxml): also `libpq-dev`, `libxml2-dev` etc — error message names the missing lib; "
        "(5) try binary wheel: `pip install --only-binary :all: <pkg>` avoids the compile.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: subprocess-exited-with-error / PEP 517 build failed",
        "Modern build (PEP 517) failed during wheel creation. "
        "Fix: (1) scroll up — actual error is above the pip wrapper; "
        "(2) update build tools: `pip install -U pip setuptools wheel build`; "
        "(3) for packages requiring extra deps: install build deps first (`pip install cython` for some); "
        "(4) try older build: `pip install --no-build-isolation <pkg>` (uses your installed setuptools — may help in edge cases); "
        "(5) for pre-release/dev packages: may need pyproject.toml fix upstream.",
        f"{BASE}/reference/build-system/",
    ),
    (
        "pip-errors", "pip error: HTTP 403 from PyPI / your IP has been rate-limited",
        "PyPI Cloudflare blocked your IP (often from cloud providers, CI bots, or downloads from same IP). "
        "Fix: (1) check status: https://status.python.org; "
        "(2) use a mirror: `pip install --index-url https://mirrors.cloud.tencent.com/pypi/simple/ ...` (or pypi-jp.python.org for Japan, kapeli for general); "
        "(3) for CI: configure pip to retry on 403 (default does); "
        "(4) for high-volume: set up internal mirror with devpi or PyPI Cloud (paid).",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-errors", "pip error: connection error / no connection to PyPI",
        "Network can't reach pypi.org. "
        "Fix: (1) check connectivity: `curl https://pypi.org`; "
        "(2) corporate proxy: `pip install --proxy http://proxy:8080 <pkg>` or `export HTTPS_PROXY=...`; "
        "(3) DNS: `nslookup pypi.org` and `files.pythonhosted.org`; "
        "(4) firewall allowlist: pypi.org, files.pythonhosted.org, pythonhosted.org; "
        "(5) for offline: pre-download with `pip download` on connected host, transfer, then `pip install --no-index --find-links .` offline.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: SSL: CERTIFICATE_VERIFY_FAILED",
        "TLS cert chain validation failed. Often: corporate proxy MITM, stale CA bundle, or system clock skew. "
        "Fix: (1) update certifi: `pip install -U certifi`; "
        "(2) update system CAs: macOS `/Applications/Python\\ 3.X/Install\\ Certificates.command`; Linux `apt install ca-certificates`; "
        "(3) for corp proxy: `pip config set global.cert /path/to/corp-ca.pem`; "
        "(4) LAST RESORT: `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org <pkg>` (disables verification — insecure); "
        "(5) check clock: `date` should be reasonable.",
        f"{BASE}/topics/secure-installs/",
    ),
    (
        "pip-errors", "pip error: hash mismatch (--require-hashes mode)",
        "A downloaded file's hash doesn't match what's in requirements.txt. "
        "Fix: (1) regenerate hashes: `pip hash <file.whl>` or use `pip-compile --generate-hashes`; "
        "(2) for source-vs-wheel: hashes are file-specific — wheel hash != tarball hash for the same package; "
        "(3) if package was re-released with same version (rare but happens): contact maintainer + update hashes; "
        "(4) for security audit: investigate why hash changed — could indicate compromise.",
        f"{BASE}/topics/secure-installs/",
    ),
    (
        "pip-errors", "pip error: ReadTimeoutError / timed out",
        "Slow PyPI / files.pythonhosted.org response. "
        "Fix: (1) retry — usually transient; "
        "(2) bump timeout: `pip install --timeout 60 <pkg>`; "
        "(3) for huge packages (torch, tensorflow): can take minutes — be patient; "
        "(4) use closer mirror for your region (see HTTP 403 recipe); "
        "(5) check status: https://status.python.org.",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-errors", "pip error: invalid command 'X' / unknown command",
        "Typo or removed command. "
        "Fix: (1) `pip --help` lists current commands; "
        "(2) `pip search` was removed in 2021 — use PyPI web search; "
        "(3) typos: `pip instal` → `install`, `pip freez` → `freeze`; "
        "(4) for outdated pip (where command might not exist): `pip install -U pip`.",
        f"{BASE}/cli/",
    ),
    (
        "pip-errors", "pip warning: Running pip as 'root' user is dangerous",
        "Informational. System pip + root means OS package conflicts. "
        "Fix: (1) use a venv (best): `python -m venv .venv && source .venv/bin/activate`; "
        "(2) for Docker: switch to non-root USER + write to user dir; "
        "(3) suppress in CI only: `pip install --root-user-action=ignore <pkg>`; "
        "(4) NEVER ignore on a host machine — risks breaking system Python.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip warning: You are using pip version X; however version Y is available",
        "Pip wants to update itself. "
        "Fix: (1) update: `pip install -U pip`; "
        "(2) in venv: `python -m pip install -U pip`; "
        "(3) outside venv (system pip): may hit EXTERNALLY-MANAGED — see PEP 668 fix; "
        "(4) suppress: `pip install --disable-pip-version-check <pkg>` or env `PIP_DISABLE_PIP_VERSION_CHECK=1`.",
        f"{BASE}/cli/",
    ),
    (
        "pip-errors", "pip error: package X requires a different Python: 3.9.x vs 3.11",
        "Package's `python_requires` doesn't include your Python version. "
        "Fix: (1) check your Python: `python --version`; "
        "(2) check requirements: `pip show <package>` or PyPI page; "
        "(3) find compatible version: `pip index versions <package>`; "
        "(4) use compatible Python: `pyenv install 3.11 && pyenv local 3.11` (or asdf/mise); "
        "(5) for Python too new: package may not have updated yet — pin to older minor.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: only-binary not set / cannot find compatible wheel",
        "No wheel for your platform/Python, and you have `--only-binary :all:` or no compiler. "
        "Fix: (1) drop `--only-binary` to allow source builds; "
        "(2) install compiler (see gcc/clang and MSVC recipes); "
        "(3) check available wheels: PyPI page → 'Download files'; "
        "(4) for niche platforms (linux/arm, alpine): may need to build everything from source; "
        "(5) conda-forge has prebuilt binaries for many awkward packages.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: editable install requires setup.py / pyproject.toml",
        "Pip's PEP 660 editable install needs build backend support. "
        "Fix: (1) modern (pyproject.toml + PEP 660 backend): `pip install -e .`; "
        "(2) setuptools >= 64 supports PEP 660; older versions: `pip install -e .` still works via legacy `develop`; "
        "(3) for flit: must use `pyproject.toml` with `build-system.requires = ['flit_core']`; "
        "(4) ensure `[build-system]` block exists in pyproject.toml.",
        f"{BASE}/topics/local-project-installs/",
    ),
    (
        "pip-errors", "pip error: cannot uninstall 'X': distutils installed",
        "Package was installed by distutils (legacy / OS package manager), pip can't safely remove. "
        "Fix: (1) check origin: `pip show <package>` → Location; "
        "(2) for system packages (apt/dnf): `sudo apt remove python3-<package>`; "
        "(3) for force: `pip install --ignore-installed --force-reinstall <package>` (won't remove old, just shadows it); "
        "(4) better: use venv to avoid system conflicts.",
        f"{BASE}/cli/pip_uninstall/",
    ),
    (
        "pip-errors", "pip error: invalid requirement '<X>' / parser error",
        "requirements.txt or `pip install <spec>` syntax wrong. "
        "Fix: (1) basic: `package==1.2.3`, `package>=1.0,<2.0`; "
        "(2) with extras: `package[extra1,extra2]==1.2.3`; "
        "(3) git: `git+https://github.com/user/repo@v1.0#egg=package`; "
        "(4) local file: `./local-package-dir` or `./pkg.whl`; "
        "(5) for windows paths in reqs.txt: forward slashes work, backslashes don't.",
        f"{BASE}/reference/requirements-file-format/",
    ),
    (
        "pip-errors", "pip error: cannot import setuptools",
        "Build environment missing setuptools. "
        "Fix: (1) `pip install -U pip setuptools wheel`; "
        "(2) in CI / fresh venv: must run that line BEFORE installing dependent packages; "
        "(3) for pyproject.toml that requires setuptools: declare it: `[build-system] requires = ['setuptools>=64', 'wheel']`; "
        "(4) for build isolation issues: try `--no-build-isolation`.",
        f"{BASE}/reference/build-system/",
    ),
    (
        "pip-errors", "pip error: '<X>' has unsafe filename / path traversal",
        "Wheel or sdist filename failed safety check (rare — security feature). "
        "Fix: (1) report to package maintainer; "
        "(2) re-download — could be corrupt; "
        "(3) NEVER `--no-deps --no-build-isolation` to bypass — the check is there for a reason.",
        f"{BASE}/topics/secure-installs/",
    ),
    (
        "pip-errors", "pip error: invalid wheel filename / wrong tag",
        "Wheel filename doesn't follow PEP 427 (name-version-pythontag-abitag-platformtag.whl). "
        "Fix: (1) re-download — likely corrupt; "
        "(2) for a custom wheel: ensure name follows: `mypkg-1.0.0-py3-none-any.whl`; "
        "(3) check tags: PEP 425; "
        "(4) for an sdist (tar.gz): different rules — name + version is enough.",
        f"{BASE}/reference/build-system/",
    ),
    (
        "pip-errors", "pip error: unrecognized arguments: --xyz",
        "Flag doesn't exist in your pip version. "
        "Fix: (1) check help: `pip install --help`; "
        "(2) for new flags (e.g. `--require-virtualenv`, `--break-system-packages`): need pip 23+; "
        "(3) upgrade: `pip install -U pip`; "
        "(4) common typos: `--require-hash` → `--require-hashes` (with -es).",
        f"{BASE}/cli/",
    ),
    (
        "pip-errors", "pip error: pip 21+ resolver: backtracking forever",
        "New resolver tries many combinations on conflicting graphs — can take hours. "
        "Fix: (1) tighten pins: more `==` constraints reduce search space; "
        "(2) `pip install -U pip` — newer pip has better heuristics; "
        "(3) use a real lockfile tool: `pip-compile` (pip-tools) or `uv pip compile` (10-100x faster); "
        "(4) for known conflicts: install in two steps with `--no-deps`.",
        f"{BASE}/topics/dependency-resolution/",
    ),
    (
        "pip-errors", "pip error: package 'X' is not installed but referenced by Y",
        "Inconsistent install — some packages removed but others still reference them. "
        "Fix: (1) `pip check` lists all inconsistencies; "
        "(2) reinstall the missing: `pip install <missing>`; "
        "(3) nuclear: rebuild venv from scratch; "
        "(4) for production: pinned requirements.txt + `pip install -r ...` after wiping env.",
        f"{BASE}/cli/pip_check/",
    ),
    (
        "pip-errors", "pip error: cache 'X' is corrupt / wheel cache error",
        "HTTP cache or wheel cache got into a bad state. "
        "Fix: (1) purge: `pip cache purge`; "
        "(2) specific package: `pip cache remove <pkg>`; "
        "(3) check location: `pip cache dir`; "
        "(4) for disk space issues: `pip cache info` shows usage.",
        f"{BASE}/cli/pip_cache/",
    ),
    (
        "pip-errors", "pip error: namespace package conflict",
        "Two installs both try to own the same namespace path. "
        "Fix: (1) declare PEP 420 implicit namespace properly (no `__init__.py` in namespace dir); "
        "(2) avoid mixing PEP 420 + legacy pkg_resources-style namespaces; "
        "(3) for old setup.py: use `namespace_packages=['ns']` consistently across all participating packages.",
        f"{BASE}/topics/local-project-installs/",
    ),
    (
        "pip-errors", "pip error: pip cannot install --user inside venv / --user not allowed",
        "Mixing `--user` and venv produces unpredictable behavior. "
        "Fix: (1) inside venv: just `pip install <pkg>` (no `--user`); "
        "(2) outside venv on system pip: `--user` installs to ~/.local; "
        "(3) `--user` writes to site.USER_BASE — different from venv's site-packages.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-errors", "pip error: deprecation warning: TLSv1.0 / OpenSSL too old",
        "Old Python with old SSL can't connect to modern PyPI. "
        "Fix: (1) upgrade Python (3.10+ on a modern OS); "
        "(2) for stuck on old: use a proxy/mirror with more permissive TLS; "
        "(3) the deprecation is real — PyPI rejects TLS < 1.2.",
        f"{BASE}/topics/secure-installs/",
    ),

    # ============================================================
    # pip-recipes — 36 common workflows
    # ============================================================
    (
        "pip-recipes", "pip recipe: install a package (basic + version variants)",
        "Latest: `pip install requests`. "
        "Specific: `pip install requests==2.31.0`. "
        "Minimum: `pip install 'requests>=2.31'`. "
        "Range: `pip install 'requests>=2.0,<3.0'`. "
        "Compatible (~=): `pip install 'requests~=2.31.0'` (allows 2.31.x but not 2.32). "
        "Pre-release: `pip install --pre requests` (alpha/beta versions). "
        "Multiple: `pip install requests httpx pydantic`.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: install from requirements.txt",
        "`pip install -r requirements.txt`. "
        "Multiple files: `-r base.txt -r prod.txt`. "
        "With constraints: `pip install -r requirements.txt -c constraints.txt` (constraints don't install, just cap versions). "
        "Verbose for slow resolves: `-v`. "
        "For reproducible: `--require-hashes` + hashes in file.",
        f"{BASE}/reference/requirements-file-format/",
    ),
    (
        "pip-recipes", "pip recipe: generate requirements.txt (freeze)",
        "Quick: `pip freeze > requirements.txt` (all installed, exact versions). "
        "Caveats: includes transitive deps (no distinction from direct); ignores extras. "
        "Better (just direct deps): `pip-tools` — write `requirements.in` with direct deps, run `pip-compile` → produces `requirements.txt` with all pins + hashes. "
        "Modern alternative: `uv pip compile requirements.in -o requirements.txt` (10-100x faster).",
        f"{BASE}/cli/pip_freeze/",
    ),
    (
        "pip-recipes", "pip recipe: editable install for local development",
        "From project root: `pip install -e .`. "
        "With dev extras: `pip install -e '.[dev,test]'`. "
        "Editable install reflects source changes immediately (no reinstall needed). "
        "Requires PEP 660 build backend (setuptools>=64, hatchling, flit, pdm). "
        "For src-layout: works the same — pip handles `[tool.setuptools.packages.find]` correctly.",
        f"{BASE}/topics/local-project-installs/",
    ),
    (
        "pip-recipes", "pip recipe: install from git URL",
        "Main branch: `pip install git+https://github.com/user/repo.git`. "
        "Specific commit: `git+https://github.com/user/repo.git@abc1234`. "
        "Specific tag: `git+https://github.com/user/repo.git@v1.2.3`. "
        "Specific branch: `git+https://github.com/user/repo.git@feature-branch`. "
        "With subdirectory (monorepo): `git+https://github.com/user/repo.git@v1.0#subdirectory=packages/my-pkg`. "
        "SSH: `git+ssh://git@github.com/user/repo.git`.",
        f"{BASE}/topics/vcs-support/",
    ),
    (
        "pip-recipes", "pip recipe: install with extras (optional deps)",
        "`pip install 'package[extra1]'` (note quotes for shells that eat `[]`). "
        "Multiple: `pip install 'fastapi[all]'` or `pip install 'sqlalchemy[asyncio,postgresql]'`. "
        "All extras at once (some packages support): `'package[all]'`. "
        "List available extras: PyPI page → 'Project description' usually lists them. "
        "In requirements.txt: `fastapi[all]==0.115.0` works the same.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: install without dependencies",
        "`pip install --no-deps <package>`. "
        "Use case: you want a specific older version of a sub-dep + control it yourself; debugging dependency conflicts. "
        "Dangerous: the package may fail at runtime if deps aren't satisfied. "
        "Usually paired with explicit installs of needed deps.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: upgrade a package",
        "`pip install -U <package>` or `--upgrade`. "
        "All installed: `pip install -U $(pip freeze | cut -d= -f1)` (Linux/Mac) — but risky; deps may break. "
        "Better: bump versions in requirements.txt + reinstall: `pip install -r requirements.txt --upgrade`. "
        "Specific dep too: `--upgrade-strategy eager` (upgrades all transitive deps) vs default `only-if-needed`.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: uninstall a package",
        "`pip uninstall <package>` (prompts). "
        "Multiple: `pip uninstall pkg1 pkg2 pkg3`. "
        "Skip confirmation: `-y`. "
        "From requirements: `pip uninstall -r requirements.txt -y`. "
        "Caveat: doesn't remove deps that aren't used elsewhere — use `pip-autoremove` or manually.",
        f"{BASE}/cli/pip_uninstall/",
    ),
    (
        "pip-recipes", "pip recipe: list installed packages",
        "All: `pip list`. "
        "Outdated: `pip list --outdated`. "
        "Up-to-date only: `pip list --uptodate`. "
        "Only top-level (not deps): `pip list --not-required`. "
        "Format JSON: `pip list --format=json | jq`. "
        "By location: `pip list --user` (user-installed only).",
        f"{BASE}/cli/pip_list/",
    ),
    (
        "pip-recipes", "pip recipe: show package metadata",
        "`pip show <package>` prints: Name, Version, Summary, Home-page, Author, License, Location, Requires, Required-by. "
        "Multiple: `pip show numpy pandas`. "
        "More files: `pip show -f <package>` (lists all installed files). "
        "For dep tree: `pip show <package>` shows direct deps; for full tree: `pipdeptree` (third-party).",
        f"{BASE}/cli/pip_show/",
    ),
    (
        "pip-recipes", "pip recipe: configure index URL (mirror or private)",
        "Per-command: `pip install --index-url https://pypi.example.com/simple/ <pkg>`. "
        "Persistent: `pip config set global.index-url https://pypi.example.com/simple/`. "
        "Env: `PIP_INDEX_URL=https://...`. "
        "Multiple indexes (fall back to PyPI for unknown): `--extra-index-url https://...`. "
        "Common mirrors (for speed in regions): aliyun, tsinghua, douban — usually slower for global users.",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-recipes", "pip recipe: trust an HTTPS host (corporate proxy or self-signed)",
        "Per-command: `pip install --trusted-host pypi.example.com <pkg>`. "
        "Persistent: `pip config set global.trusted-host pypi.example.com`. "
        "WARNING: disables TLS verification — only for known-internal hosts. "
        "Better: install your corporate CA properly: `pip config set global.cert /path/to/ca.pem`.",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-recipes", "pip recipe: download wheels without installing (offline prep)",
        "`pip download <package>` saves wheel to current dir. "
        "Specific dir: `pip download <pkg> -d ./wheels`. "
        "Different platform: `pip download --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: <pkg>` (download for Linux from Mac, etc.). "
        "For offline install later: `pip install --no-index --find-links ./wheels <pkg>`.",
        f"{BASE}/cli/pip_download/",
    ),
    (
        "pip-recipes", "pip recipe: build a wheel locally",
        "`pip wheel . -w ./dist` (build current package's wheel into ./dist/). "
        "Build all from requirements: `pip wheel -r requirements.txt -w ./wheels`. "
        "Use case: prebuild wheels in CI, install fast in production. "
        "For sdist + wheel: use `build` package instead: `python -m build`.",
        f"{BASE}/cli/pip_wheel/",
    ),
    (
        "pip-recipes", "pip recipe: install from local directory or wheel",
        "Local directory: `pip install ./path/to/pkg/`. "
        "Editable: `pip install -e ./path/to/pkg/`. "
        "From wheel file: `pip install ./dist/mypackage-1.0.0-py3-none-any.whl`. "
        "From sdist tarball: `pip install ./dist/mypackage-1.0.0.tar.gz`. "
        "Multiple from dir: `pip install ./pkg1 ./pkg2`.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: install from TestPyPI (test before publishing)",
        "`pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ <pkg>`. "
        "Use test.pypi.org for the test package, fall back to real PyPI for its deps. "
        "Test before final upload: `twine upload --repository testpypi dist/*` then verify with the install command.",
        f"https://packaging.python.org/en/latest/guides/using-testpypi/",
    ),
    (
        "pip-recipes", "pip recipe: clear cache (disk space, debugging)",
        "Clear all: `pip cache purge`. "
        "Specific package: `pip cache remove <pattern>`. "
        "Show usage: `pip cache info`. "
        "Show cache location: `pip cache dir`. "
        "List cached wheels: `pip cache list`. "
        "Caches grow huge over time — periodic purge frees GBs.",
        f"{BASE}/cli/pip_cache/",
    ),
    (
        "pip-recipes", "pip recipe: upgrade pip itself",
        "Inside venv: `python -m pip install -U pip`. "
        "Outside venv (system pip): may hit PEP 668 — use venv instead. "
        "Specific version: `python -m pip install pip==23.3`. "
        "For Python launcher (Windows): `py -m pip install -U pip`. "
        "Best practice: ALWAYS use `python -m pip` not bare `pip` to avoid PATH confusion.",
        f"{BASE}/cli/",
    ),
    (
        "pip-recipes", "pip recipe: pin with hashes for security (--require-hashes)",
        "Generate hashes: `pip hash <file.whl>` per file, OR use `pip-compile --generate-hashes`. "
        "requirements.txt format: ```\\nrequests==2.31.0 \\\\\\n    --hash=sha256:abc... \\\\\\n    --hash=sha256:def...\\n```. "
        "Install with verification: `pip install -r requirements.txt --require-hashes`. "
        "Critical for production / supply-chain security — pinned versions only prove version, hashes prove the actual file.",
        f"{BASE}/topics/secure-installs/",
    ),
    (
        "pip-recipes", "pip recipe: use a constraints file (-c)",
        "constraints.txt caps versions WITHOUT installing — useful when you have many requirements files. "
        "Example: `requests<3` in constraints.txt means any req file you install will be capped at requests <3. "
        "Usage: `pip install -r requirements.txt -c constraints.txt`. "
        "Common pattern: `constraints.txt` checked into repo prevents accidental major upgrades across files.",
        f"{BASE}/topics/repeatable-installs/",
    ),
    (
        "pip-recipes", "pip recipe: pip-tools for proper lockfiles",
        "Install: `pip install pip-tools`. "
        "`requirements.in` (direct deps): `fastapi`, `pydantic` (no versions or just constraints). "
        "Compile to pin: `pip-compile requirements.in -o requirements.txt` (resolves all transitive + pins). "
        "Upgrade specific: `pip-compile --upgrade-package requests requirements.in`. "
        "Sync env: `pip-sync requirements.txt` (installs exactly what's listed, removes everything else).",
        f"{BASE}/topics/repeatable-installs/",
    ),
    (
        "pip-recipes", "pip recipe: switch to uv (faster alternative)",
        "uv from Astral replaces pip with a 10-100x faster Rust binary. "
        "Install: `pip install uv` (or `curl -LsSf https://astral.sh/uv/install.sh | sh`). "
        "Replace pip: `uv pip install <pkg>` (same flags). "
        "Lockfile: `uv pip compile requirements.in -o requirements.txt`. "
        "Sync: `uv pip sync requirements.txt`. "
        "For project management: `uv init`, `uv add`, `uv run` (replaces poetry/pdm too).",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: install in user site (--user)",
        "`pip install --user <package>` writes to `~/.local/lib/pythonX.Y/site-packages/`. "
        "Scripts go to `~/.local/bin/` — ensure it's on PATH. "
        "Use case: install CLI tools without sudo on a system without venv. "
        "Better alternative for CLI tools: `pipx install <tool>` (isolated venv per tool, more reliable).",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: check installed environment consistency",
        "`pip check` reports missing/incompatible deps across installed packages. "
        "Output: lists each conflict (e.g. `package X requires Y>=2.0, but you have Y 1.5`). "
        "Use in CI: exit code != 0 if inconsistencies. "
        "Combined with `pip list --not-required`: see top-level packages only.",
        f"{BASE}/cli/pip_check/",
    ),
    (
        "pip-recipes", "pip recipe: inspect environment as JSON",
        "`pip inspect` returns full JSON report of installed packages + metadata. "
        "Saved: `pip inspect > env.json`. "
        "Use case: programmatic environment audits, SBOM generation. "
        "Combined with jq: `pip inspect | jq '.installed[] | select(.metadata.name == \"requests\") | .metadata.version'`.",
        f"{BASE}/reference/inspect-report/",
    ),
    (
        "pip-recipes", "pip recipe: create + activate a virtual environment",
        "Stdlib (venv): `python -m venv .venv` then activate: Linux/Mac `source .venv/bin/activate`, Windows `.venv\\Scripts\\activate`. "
        "After: pip installs go into `.venv/`. "
        "Deactivate: `deactivate`. "
        "Delete: just `rm -rf .venv`. "
        "Modern alternative: `uv venv` (10x faster). "
        "For Python version selection: `python3.11 -m venv .venv` (specific Python).",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-recipes", "pip recipe: install for a different Python interpreter",
        "`/path/to/other/python -m pip install <pkg>` always installs into that interpreter. "
        "Common: `python3.11 -m pip install <pkg>` (one specific Python). "
        "For pyenv/asdf/mise managed: `pyenv exec pip install <pkg>` or similar. "
        "Avoid: bare `pip` (could be wrong Python on system with multiple).",
        f"{BASE}/topics/python-option/",
    ),
    (
        "pip-recipes", "pip recipe: pre-release / beta install",
        "`pip install --pre <pkg>` allows pre-release versions (a, b, rc). "
        "Specific: `pip install 'pkg>=2.0.0b1'` doesn't need `--pre`. "
        "List pre-releases: `pip index versions <pkg> --pre`. "
        "Caveat: pre-releases can break — pin in requirements.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: install platform-specific (cross-platform download)",
        "`pip download --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: --no-deps <pkg> -d ./wheels`. "
        "Then transfer wheels to target machine, install: `pip install --no-index --find-links ./wheels <pkg>`. "
        "Use case: build deploy artifact for Linux from Mac, AWS Lambda layers, etc. "
        "Common platforms: `manylinux_2_28_x86_64`, `manylinux2014_aarch64`, `macosx_11_0_arm64`, `win_amd64`.",
        f"{BASE}/cli/pip_download/",
    ),
    (
        "pip-recipes", "pip recipe: configure pip globally with pip config",
        "View current: `pip config list`. "
        "Set: `pip config set global.index-url https://pypi.example.com/simple/`. "
        "Unset: `pip config unset global.index-url`. "
        "Locations: `pip config list -v` shows all config files; user is `~/.config/pip/pip.conf`. "
        "Section keys: `global.*` (everywhere), `install.*` (install only), `download.*`, `wheel.*`.",
        f"{BASE}/topics/configuration/",
    ),
    (
        "pip-recipes", "pip recipe: install offline / air-gapped",
        "On connected host: `pip download -r requirements.txt -d wheels/` (downloads all wheels). "
        "Transfer `wheels/` folder. "
        "On air-gapped: `pip install --no-index --find-links ./wheels -r requirements.txt`. "
        "Combine with `--require-hashes` for verification. "
        "For platform mismatch: use `--platform` etc on download side.",
        f"{BASE}/cli/pip_install/",
    ),
    (
        "pip-recipes", "pip recipe: use pyproject.toml dependencies",
        "Modern (PEP 621): in `pyproject.toml`: ```toml\\n[project]\\nname = \"my-app\"\\nversion = \"0.1.0\"\\ndependencies = [\\n  \"requests>=2.31\",\\n  \"pydantic>=2.0\",\\n]\\n[project.optional-dependencies]\\ndev = [\"pytest\", \"ruff\"]\\n```. "
        "Install: `pip install -e .` (the project + deps). "
        "With extras: `pip install -e '.[dev]'`. "
        "Lockfile: `pip-tools` or `uv` produces pinned requirements.txt from pyproject.toml.",
        f"https://packaging.python.org/en/latest/tutorials/packaging-projects/",
    ),
    (
        "pip-recipes", "pip recipe: install just the metadata (--dry-run)",
        "`pip install --dry-run <pkg>` shows what would be installed without changing anything. "
        "Useful for: checking resolver output before commit, debugging dependency conflicts. "
        "Output: `Would install pkg-1.2.3 dep1-2.0.0 dep2-1.5.1`. "
        "Combined with `--report -`: outputs detailed JSON report.",
        f"{BASE}/reference/installation-report/",
    ),
    (
        "pip-recipes", "pip recipe: use pipx for CLI tools (instead of pip --user)",
        "`pipx install <tool>` installs to isolated venv, exposes CLI on PATH. "
        "Examples: `pipx install poetry`, `pipx install ruff`, `pipx install black`. "
        "Upgrade: `pipx upgrade <tool>`. "
        "List: `pipx list`. "
        "Run without install: `pipx run <tool>`. "
        "Each tool gets own venv — no dependency conflicts.",
        f"https://packaging.python.org/en/latest/tutorials/installing-packages/",
    ),
    (
        "pip-recipes", "pip recipe: production deployment best practices",
        "(1) Use venv (always). "
        "(2) Pin everything (requirements.txt with == versions + hashes). "
        "(3) Use `--require-hashes` in production install: `pip install --require-hashes -r requirements.txt`. "
        "(4) Build wheels in CI, install in prod (saves time + avoids build-time deps in prod image). "
        "(5) Use `--no-cache-dir` in Docker images (smaller layers). "
        "(6) For multi-stage Docker: copy `.venv` from builder stage to runtime stage.",
        f"{BASE}/topics/secure-installs/",
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
            evidence_id=f"ev-pip-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-pip-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="pip-catalog",
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
