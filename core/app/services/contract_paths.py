"""Resolve a contract JSON file shipped under the `app.contracts` namespace.

The lean trust contracts (`risk_model`, `confidence_model`, `query_policy`,
`tool_trust_sources`, `ayiru_stage_0.v{1,2}`) ship with the `ayiru-core` wheel.
The ingestion-specific contracts (`*_ingestion_sources`, `runtime_verification_sources`)
ship with the `ayiru` backend wheel. Both contribute to the PEP 420 namespace
package `app.contracts`.

We avoid `importlib.resources.files("app.contracts")` because setuptools
editable installs inject opaque `__path_hook__` entries into the namespace's
`__path__` and `MultiplexedPath` rejects them with `NotADirectoryError`.
Walking `app.contracts.__path__` directly and filtering to real directories
gives the same merge behaviour without the editable-install footgun, and
also works for regular (non-editable) wheel installs.

A `FileNotFoundError` is raised when the requested basename is absent from
every contributor. That's strictly a configuration bug (a wheel is missing,
or someone hand-edited the install) and the loaders surface it without
swallowing.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path


def contract_path(name: str) -> Path:
    """Return a `Path` to the requested contract JSON file.

    `name` is the file basename (e.g. ``"query_policy.v1.json"``). The
    function never adds extensions; callers pass the full filename so
    grepping for contract usage stays straightforward.
    """
    for root in _contract_roots():
        candidate = root / name
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        f"Contract '{name}' was not found in any `app.contracts` "
        f"contributor. Searched: {[str(r) for r in _contract_roots()]}. "
        f"Ensure both ayiru-core (lean contracts) and ayiru (ingestion "
        f"contracts) wheels are installed."
    )


@cache
def _contract_roots() -> tuple[Path, ...]:
    """All real filesystem directories that contribute to `app.contracts`.

    Reads `app.contracts.__path__` (the namespace package's contributor
    list), drops entries that aren't on-disk directories (editable-install
    finder hooks, frozen importers), resolves symlinks, and deduplicates
    while preserving order.
    """
    try:
        import app.contracts as namespace
    except ImportError:
        return ()

    seen: set[Path] = set()
    roots: list[Path] = []
    for entry in namespace.__path__:
        candidate = Path(entry)
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)
    return tuple(roots)
