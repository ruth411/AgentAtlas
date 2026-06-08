"""Ayiru client SDK — public surface.

Two entry points: :class:`Ayiru` (blocking) and :class:`AsyncAyiru`
(asyncio). Both expose the same five methods — ``ask``,
``validate_command``, ``get_tool_spec``, ``search_tools``, ``savings``."""

from ayiru_client._async_client import AsyncAyiru
from ayiru_client._client import Ayiru
from ayiru_client._errors import AyiruError
from ayiru_client._models import (
    Answer,
    AskResponse,
    EvidenceCitation,
    SavingsResponse,
    SearchToolsResponse,
    ToolMatchSummary,
    ToolSpec,
    ValidateCommandResponse,
)
from ayiru_client._version import __version__

__all__ = [
    "Answer",
    "AskResponse",
    "AsyncAyiru",
    "Ayiru",
    "AyiruError",
    "EvidenceCitation",
    "SavingsResponse",
    "SearchToolsResponse",
    "ToolMatchSummary",
    "ToolSpec",
    "ValidateCommandResponse",
    "__version__",
]
