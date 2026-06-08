"""Single source of truth for the published ayiru-client version."""

from __future__ import annotations

from importlib import metadata

_PACKAGE_NAME = "ayiru-client"


def package_version() -> str:
    try:
        return metadata.version(_PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        # Source-tree fallback for unusual import paths that bypass package
        # installation metadata. Keep the SDK usable rather than crashing
        # during import; tests pin the normal installed path.
        return "0.0.0+unknown"


__version__ = package_version()
USER_AGENT = f"ayiru-client-py/{__version__}"


__all__ = ["USER_AGENT", "__version__", "package_version"]
