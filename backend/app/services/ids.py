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
