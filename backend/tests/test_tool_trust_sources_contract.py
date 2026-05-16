import json
from pathlib import Path

from app.schemas.evidence import Evidence
from app.schemas.enums import EvidenceType, TrustLevel
from app.services.evidence_trust import resolve_evidence_trust_level
from tests.helpers import valid_sha256


ROOT = Path(__file__).resolve().parents[2]
STAGE_0_CONTRACT = ROOT / "contracts/agentatlas_stage_0.v1.json"
TRUST_SOURCES_CONTRACT = ROOT / "contracts/tool_trust_sources.v1.json"


def _evidence(source_uri: str) -> Evidence:
    return Evidence(
        evidence_id="ev_contract",
        evidence_type=EvidenceType.OFFICIAL_DOCS,
        source_uri=source_uri,
        excerpt="Official documentation excerpt.",
        hash=valid_sha256(source_uri),
        captured_at="2026-05-14T00:00:00+00:00",
        trust_level=TrustLevel.HIGH,
    )


def test_tool_trust_sources_cover_stage_0_tools() -> None:
    stage_0 = json.loads(STAGE_0_CONTRACT.read_text())
    trust_sources = json.loads(TRUST_SOURCES_CONTRACT.read_text())

    stage_0_tools = {tool["tool_id"] for tool in stage_0["initial_tools"]}

    assert stage_0_tools <= set(trust_sources["tools"])
    for tool_id in stage_0_tools:
        assert trust_sources["tools"][tool_id]["official_hosts"]
        assert "source_repositories" in trust_sources["tools"][tool_id]


def test_tool_trust_sources_drive_server_trust_resolution() -> None:
    official = _evidence("https://docs.github.com/cli/manual/gh_repo_delete")
    fake = _evidence("https://docs.example.com/cli/manual/gh_repo_delete")

    assert resolve_evidence_trust_level("github-cli", official) == TrustLevel.HIGH
    assert resolve_evidence_trust_level("github-cli", fake) == TrustLevel.LOW
