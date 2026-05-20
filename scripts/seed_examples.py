"""Thin shim — the actual seed logic lives in `app.seed_data.runner`.

Kept at this path because the existing documentation, tests, and the
historical CLI fallback (`AGENTATLAS_SEED_SCRIPT=$PWD/scripts/seed_examples.py`)
all reference it. The shim adds the backend directory to `sys.path` so
`app.*` imports work when the script is run from a fresh checkout
without `pip install`-ing the package.

Usage:

    python scripts/seed_examples.py                 # additive seed
    python scripts/seed_examples.py --reset         # drop DB + alembic up + seed
    python scripts/seed_examples.py --database-url sqlite:///... --reset

The recommended invocation post-Stage-14 is `agentatlas seed --reset`,
which calls into the same `main()` directly without spawning a
subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.seed_data.runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
