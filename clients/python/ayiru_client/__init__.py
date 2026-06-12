"""Ayiru client SDK — public surface.

Two entry points: :class:`Ayiru` (blocking) and :class:`AsyncAyiru`
(asyncio). Both expose the compatibility query methods plus the newer
structured subject / capability / action-resolution APIs."""

from ayiru_client._async_client import AsyncAyiru
from ayiru_client._client import Ayiru
from ayiru_client._errors import AyiruError
from ayiru_client._models import (
    Answer,
    AskResponse,
    CapabilityRecord,
    ConstraintSetResponse,
    EffectProfileResponse,
    EvidenceCitation,
    GetCapabilitiesResponse,
    ResolveActionResponse,
    ResolveSubjectResponse,
    SavingsResponse,
    SearchToolsResponse,
    SubjectSpecResponse,
    SubjectSummary,
    ToolMatchSummary,
    ToolSpec,
    ValidateCommandResponse,
    WorkflowPlanResponse,
    WorkflowPlanSummary,
)
from ayiru_client._version import __version__

__all__ = [
    "Answer",
    "AskResponse",
    "AsyncAyiru",
    "Ayiru",
    "AyiruError",
    "CapabilityRecord",
    "ConstraintSetResponse",
    "EvidenceCitation",
    "EffectProfileResponse",
    "GetCapabilitiesResponse",
    "ResolveActionResponse",
    "ResolveSubjectResponse",
    "SavingsResponse",
    "SearchToolsResponse",
    "SubjectSpecResponse",
    "SubjectSummary",
    "ToolMatchSummary",
    "ToolSpec",
    "ValidateCommandResponse",
    "WorkflowPlanResponse",
    "WorkflowPlanSummary",
    "__version__",
]
