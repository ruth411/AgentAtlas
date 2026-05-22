"""Stage 18.4 — telemetry / cost-savings stats endpoint.

Separate router from `/v1/query/*` because the conceptual surface is
different: query routes serve agent-facing retrieval calls; stats routes
serve operator-facing analytics. Mounted at `/v1/stats/...` per
plan_v02.md §Stage 18.4.

This router is read-only and unauthenticated — same posture as
`/v1/query/*`. Anyone hitting the server can read the aggregate
(*"this Ayiru instance saved 12,450 tokens this week"*) and that's by
design. Filtering by API key for multi-tenant accounting is a v0.2.5+
concern.
"""

from datetime import datetime, timedelta, timezone
from os import environ
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.errors import ERROR_RESPONSES
from app.schemas.query import SavingsResponse
from app.services.claim_store import ClaimStore, get_claim_store


router = APIRouter(prefix="/stats", tags=["stats"])


# Anthropic Claude Sonnet input rate, $3 / 1M tokens, as of 2026-05.
# Configurable via env so operators on other models (or with custom
# pricing arrangements) can override without a code change. Surfaced
# in the response so callers know which rate produced the USD figure.
_DEFAULT_USD_PER_MTOK = 3.0
_USD_PER_MTOK_ENV = "AYIRU_PRICE_PER_MTOK_INPUT"


_WINDOW_LITERAL = Literal["24h", "7d", "30d", "all"]
_WINDOW_DELTAS: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


def _usd_per_mtok() -> float:
    raw = environ.get(_USD_PER_MTOK_ENV)
    if raw is None:
        return _DEFAULT_USD_PER_MTOK
    try:
        value = float(raw)
        if value <= 0:
            return _DEFAULT_USD_PER_MTOK
        return value
    except ValueError:
        # Malformed override falls back to the default rather than
        # crashing the endpoint — telemetry must not break the read
        # path on a typo'd env var.
        return _DEFAULT_USD_PER_MTOK


@router.get(
    "/savings",
    response_model=SavingsResponse,
    responses=ERROR_RESPONSES,
)
def get_savings(
    window: _WINDOW_LITERAL = Query(default="30d"),
    store: ClaimStore = Depends(get_claim_store),
) -> SavingsResponse:
    """Aggregate QUERY_SERVED audit events into a cost-savings summary.

    Window semantics:
      - ``24h`` / ``7d`` / ``30d`` — rolling window ending at "now".
      - ``all`` — every QUERY_SERVED event in the audit log.

    The dollar figure is informational. The audit's 2026-05-22 finding
    is that ``estimated_tokens_saved`` is heuristic until the
    calibration sub-stage lands; the USD multiplication carries the
    same uncertainty. The response includes ``usd_per_million_input_tokens``
    so a caller plotting the number can label the assumption.
    """
    now = datetime.now(timezone.utc)
    delta = _WINDOW_DELTAS[window]
    after = now - delta if delta is not None else None

    aggregate = store.aggregate_query_savings(after=after, before=now)

    usd_rate = _usd_per_mtok()
    total_tokens = aggregate["total_tokens_saved"]
    estimated_usd = (total_tokens / 1_000_000.0) * usd_rate

    return SavingsResponse(
        total_queries_served=aggregate["total_queries_served"],
        total_tokens_saved=total_tokens,
        estimated_usd_saved=round(estimated_usd, 4),
        fallback_count=aggregate["fallback_count"],
        by_top_claim=aggregate["by_top_claim"],
        window=window,
        window_start=aggregate["window_start"],
        window_end=aggregate["window_end"],
        usd_per_million_input_tokens=usd_rate,
    )
