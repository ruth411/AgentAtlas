from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.enums import EvidenceType, TrustLevel
from app.schemas.evidence import Evidence
from tests.helpers import valid_sha256


def test_accepts_valid_evidence() -> None:
    evidence = Evidence(
        evidence_id="ev_123",
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri="docs://vercel-cli/deploy",
        excerpt="Production deployments are created with --prod.",
        hash=valid_sha256("ev_123"),
        captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        trust_level=TrustLevel.HIGH,
    )

    assert evidence.evidence_type == EvidenceType.OFFICIAL_DOCS


def test_rejects_blank_source_uri() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev_123",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri="",
            excerpt="Production deployments are created with --prod.",
            hash=valid_sha256("ev_123"),
            captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            trust_level=TrustLevel.HIGH,
        )
