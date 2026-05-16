from uuid import uuid4


def generate_claim_id() -> str:
    return f"claim_{uuid4().hex}"


def generate_evidence_id() -> str:
    return f"ev_{uuid4().hex}"


def generate_verification_id() -> str:
    return f"ver_{uuid4().hex}"
