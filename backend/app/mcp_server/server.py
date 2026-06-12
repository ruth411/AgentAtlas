"""Back-compat shim: re-exports from `ayiru_mcp._internal.server`."""

from ayiru_mcp._internal.server import *  # noqa: F401,F403
from ayiru_mcp._internal.server import (  # noqa: F401
    McpServer,
    build_default_server,
)
