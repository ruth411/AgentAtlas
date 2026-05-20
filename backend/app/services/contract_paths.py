"""Stage 14: single helper that resolves a contract JSON file.

The 11 service-layer loaders used to embed a hard-coded
`Path(__file__).resolve().parents[3] / "contracts" / name` lookup. That
worked from a source checkout (`<repo>/contracts/<name>.json` exists)
but broke after `pip install`: the wheel ships only the `app` package,
so `parents[3]` resolved to `lib/python3.x/` and the JSON file wasn't
there.

This helper centralises the lookup with a two-step strategy:

1. Source-tree first — if running from a checkout, prefer the canonical
   `<repo>/contracts/<name>.json` (devs editing contracts see their
   changes immediately, no rebuild needed).
2. Package-bundled fallback — `app/contracts/<name>.json` ships inside
   the wheel as package data, so `pip install agentatlas` followed by
   any service call resolves contracts cleanly.

A `FileNotFoundError` is raised when neither location has the file;
that's strictly a configuration bug (or a hand-edited install) and the
loaders surface it without swallowing.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from pathlib import Path


# Source-tree root is computed once at import: from this file, walk up
# three levels (`services/` → `app/` → `backend/`) to reach the repo
# root, then descend into `contracts/`. From a wheel install, that path
# resolves to `lib/python3.x/contracts` which won't exist — the loader
# falls through to the package-bundled copy.
_SOURCE_TREE_CONTRACTS = (
    Path(__file__).resolve().parents[2] / "contracts"
)


def contract_path(name: str) -> Path:
    """Return a `Path` to the requested contract JSON file.

    `name` is the file basename (e.g. ``"query_policy.v1.json"``). The
    function never adds extensions; callers pass the full filename so
    grepping for contract usage stays straightforward.
    """
    # Step 1: source-tree (development checkout).
    src = _SOURCE_TREE_CONTRACTS / name
    if src.is_file():
        return src

    # Step 2: package-bundled (wheel install).
    bundled = _bundled_path(name)
    if bundled is not None:
        return bundled

    raise FileNotFoundError(
        f"Contract '{name}' was not found at either source-tree "
        f"({src}) or in the bundled `app.contracts` package. The wheel "
        "may be corrupt; reinstall or run from a checkout."
    )


@cache
def _bundled_resource_root() -> Path | None:
    """Resolve the package-bundled contracts directory once, cache the
    result. Returns `None` if the `app.contracts` package is missing
    (e.g. someone hand-deleted the directory after install)."""
    try:
        # `as_file` would be needed for zip-imported wheels, but
        # AgentAtlas is shipped as a regular wheel that pip extracts
        # to disk, so `files(...)` returns a concrete Path-like that
        # already lives on the filesystem.
        return Path(str(resources.files("app.contracts")))
    except (ModuleNotFoundError, FileNotFoundError):
        return None


def _bundled_path(name: str) -> Path | None:
    root = _bundled_resource_root()
    if root is None:
        return None
    candidate = root / name
    return candidate if candidate.is_file() else None
