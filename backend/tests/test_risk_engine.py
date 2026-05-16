from copy import deepcopy

import pytest

from app.schemas.enums import RiskLevel
from app.schemas.risk import RiskDimension
from app.services.risk_engine import (
    _risk_model,
    _validate_risk_model,
    compute_risk_assessment,
)


@pytest.mark.parametrize(
    "subject,tool_id,expected",
    [
        ("git status", "git", RiskLevel.LOW),
        ("gh repo delete my-org/my-repo --yes", "github-cli", RiskLevel.CRITICAL),
        ("vercel --prod", "vercel-cli", RiskLevel.HIGH),
        ("docker run --help", "docker", RiskLevel.LOW),
        ("docker run alpine", "docker", RiskLevel.MEDIUM),
        ("GIT STATUS", "git", RiskLevel.LOW),
    ],
)
def test_risk_engine_classifies_canonical_cases(
    subject: str, tool_id: str, expected: RiskLevel
) -> None:
    assessment = compute_risk_assessment(subject, tool_id=tool_id)

    assert assessment.risk_level == expected


@pytest.mark.parametrize(
    "subject,expected_rule",
    [
        ("rm -rf /tmp/scratch", "u.rm_rf"),
        ("rm -r -f /tmp/scratch", "u.rm_rf"),
        ("terraform destroy", "u.terraform_destroy"),
        ("kubectl delete namespace prod", "u.kubectl_delete"),
        ("git push --force origin main", "u.force_flag"),
    ],
)
def test_universal_rules_fire_without_tool_id(subject: str, expected_rule: str) -> None:
    assessment = compute_risk_assessment(subject)

    assert assessment.risk_level == RiskLevel.CRITICAL
    assert expected_rule in assessment.matched_rule_ids


@pytest.mark.parametrize("subject", ["git merger-tool", "redeploy-status"])
def test_word_boundary_regressions_do_not_match_compound_words(subject: str) -> None:
    assessment = compute_risk_assessment(subject)

    assert assessment.risk_level == RiskLevel.MEDIUM
    assert assessment.matched_rule_ids == []


def test_dimensions_are_attributed_for_destructive_remote_action() -> None:
    assessment = compute_risk_assessment("gh repo delete my-org/my-repo", tool_id="github-cli")

    assert assessment.risk_level == RiskLevel.CRITICAL
    assert assessment.dimensions == [
        RiskDimension.REMOTE_MUTATION,
        RiskDimension.DESTRUCTIVE,
        RiskDimension.AUTH_SENSITIVE,
    ]


def test_multi_rule_aggregation_uses_highest_risk_and_sorted_rule_ids() -> None:
    assessment = compute_risk_assessment(
        "gh repo delete my-org/my-repo --force", tool_id="github-cli"
    )

    assert assessment.risk_level == RiskLevel.CRITICAL
    assert assessment.safe_to_auto_execute is False
    assert assessment.requires_confirmation is True
    assert assessment.matched_rule_ids == sorted(assessment.matched_rule_ids)
    assert {"gh.repo_delete", "u.force_flag"}.issubset(set(assessment.matched_rule_ids))


def test_tool_scoped_rules_do_not_leak_across_tools() -> None:
    assessment = compute_risk_assessment("docker run alpine", tool_id="git")

    assert assessment.risk_level == RiskLevel.MEDIUM
    assert assessment.matched_rule_ids == []


def test_tool_and_universal_rules_do_not_duplicate_logical_matches() -> None:
    assessment = compute_risk_assessment("git status", tool_id="git")

    assert assessment.risk_level == RiskLevel.LOW
    assert assessment.matched_rule_ids == ["git.status"]


def test_vercel_preview_does_not_fire_on_prod_deployment() -> None:
    assessment = compute_risk_assessment("vercel --prod", tool_id="vercel-cli")

    assert assessment.risk_level == RiskLevel.HIGH
    assert "vercel.prod" in assessment.matched_rule_ids
    assert "vercel.preview" not in assessment.matched_rule_ids


@pytest.mark.parametrize(
    "subject",
    [
        "What does the api endpoint look like?",
        "Chat with the bot about model selection",
        "Open AI is interesting",
    ],
)
def test_openai_model_call_rule_rejects_bare_api_model_or_chat_mentions(subject: str) -> None:
    assessment = compute_risk_assessment(subject, tool_id="openai-api")

    assert assessment.risk_level == RiskLevel.MEDIUM
    assert "openai.model_call" not in assessment.matched_rule_ids


def test_fallback_returns_medium_risk() -> None:
    assessment = compute_risk_assessment("unknown action")

    assert assessment.risk_level == RiskLevel.MEDIUM
    assert assessment.dimensions == [RiskDimension.UNKNOWN]
    assert assessment.requires_confirmation is True
    assert assessment.safe_to_auto_execute is False


def test_risk_assessment_is_reproducible() -> None:
    first = compute_risk_assessment("vercel --prod", tool_id="vercel-cli")
    second = compute_risk_assessment("vercel --prod", tool_id="vercel-cli")

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_risk_model_is_cached() -> None:
    assert _risk_model() is _risk_model()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update({"version": 2}),
        lambda data: data["universal"][0].update({"pattern": "["}),
        lambda data: data["universal"][0].update({"dimensions": ["bad_dimension"]}),
    ],
)
def test_invalid_risk_contracts_raise(mutation) -> None:
    data = deepcopy(_risk_model())
    mutation(data)

    with pytest.raises(ValueError):
        _validate_risk_model(data)
