from fastapi import APIRouter

router = APIRouter(tags=["health"])


def _health_payload() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Never versioned (universal across deploys)."""

    return _health_payload()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Alias of ``/health`` for monitors that probe the Kubernetes
    convention path (Datadog, BetterUptime, GCP load balancers, etc.).
    Same response shape as ``/health``."""

    return _health_payload()
