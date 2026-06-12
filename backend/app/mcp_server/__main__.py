"""Back-compat entry point: `python -m app.mcp_server`.

The implementation lives in `ayiru_mcp._internal`. This module forwards
to the same `build_default_server()` the new console script uses, but
does NOT swap the database URL — the backend dev path relies on whatever
`AYIRU_DATABASE_URL` (or the auto-resolved checkout default) points at.
"""

from ayiru_mcp._internal.server import build_default_server


def main() -> None:
    build_default_server().serve()


if __name__ == "__main__":
    main()
