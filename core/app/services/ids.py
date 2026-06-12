from uuid import uuid4


def generate_claim_id() -> str:
    return f"claim_{uuid4().hex}"


def generate_evidence_id() -> str:
    return f"ev_{uuid4().hex}"


def generate_verification_id() -> str:
    return f"ver_{uuid4().hex}"


def generate_ingestion_run_id() -> str:
    return f"ing_{uuid4().hex}"


def generate_ingestion_artifact_id() -> str:
    return f"art_{uuid4().hex}"


def generate_audit_event_id() -> str:
    return f"audit_{uuid4().hex}"


def generate_human_review_id() -> str:
    return f"review_{uuid4().hex}"


def generate_query_id() -> str:
    """Stage 18 — synthetic entity id for QUERY_SERVED audit rows. One per
    ``ask()`` call, recorded as the audit row's ``entity_id`` so the
    /audit/events endpoint can list a query's history just like claims."""
    return f"query_{uuid4().hex}"
