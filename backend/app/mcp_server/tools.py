"""Back-compat shim: re-exports from `ayiru_mcp._internal.tools`.

Tests in `backend/tests/test_mcp_server.py` reach for the private
``_TOOL_REGISTRY`` to inject a misbehaving tool — that one is exported
explicitly because `from ... import *` skips underscore-prefixed names.
"""

from ayiru_mcp._internal.tools import *  # noqa: F401,F403
from ayiru_mcp._internal.tools import (  # noqa: F401
    _TOOL_REGISTRY,
    McpTool,
    find_tool,
    list_tools,
)
