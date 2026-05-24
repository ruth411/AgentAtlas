"""Stage 12: `ayiru` CLI tests.

Exercise every subcommand in-process where we can — calling `app.cli.main`
directly with an explicit argv keeps the test suite fast and avoids the
flakiness of spawning subprocesses for every case.

The two exceptions are `serve` and `mcp`, which both block forever in
their real implementations. We patch the underlying entrypoints
(`uvicorn.run`, `McpServer.serve`) and assert that the CLI wired the
right arguments through; the actual server loops are exercised by
`test_routes_*` and `test_mcp_server.py`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from app import cli as cli_module
from app.cli import main


# ---------------------------------------------------------------------------
# `ayiru --version` / `--help`
# ---------------------------------------------------------------------------


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"ayiru {cli_module.PACKAGE_VERSION}"


def test_no_subcommand_exits_with_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    # argparse uses exit code 2 for "user typed the command wrong".
    assert exc.value.code == 2


def test_unknown_subcommand_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["definitely-not-a-real-subcommand"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# `ayiru serve` — uvicorn entry point
# ---------------------------------------------------------------------------


def test_serve_wires_uvicorn_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(target: str, **kwargs: Any) -> None:
        captured["target"] = target
        captured.update(kwargs)

    # The `import uvicorn` inside `_cmd_serve` resolves to whatever module
    # the test has wired up — installing a fake module covers the case
    # where uvicorn isn't on the test interpreter.
    fake_module = type(sys)("uvicorn")
    fake_module.run = fake_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_module)

    rc = main(["serve", "--host", "0.0.0.0", "--port", "9999", "--reload"])
    assert rc == 0
    assert captured["target"] == "app.main:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
    assert captured["reload"] is True


def test_serve_defaults_to_localhost_no_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(target: str, **kwargs: Any) -> None:
        captured.update(kwargs)

    fake_module = type(sys)("uvicorn")
    fake_module.run = fake_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_module)
    # Stub the auto-migrate so the test doesn't actually invoke alembic.
    monkeypatch.setattr(cli_module, "_auto_migrate", lambda: None)

    assert main(["serve"]) == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert captured["reload"] is False


def test_serve_runs_auto_migrate_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 14: a fresh-install user running `ayiru serve` against
    an empty DB shouldn't have to know to run `migrate` first. The
    serve subcommand auto-applies pending migrations on startup."""
    migrate_calls: list[bool] = []

    def fake_migrate() -> None:
        migrate_calls.append(True)

    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli_module, "_auto_migrate", fake_migrate)

    assert main(["serve"]) == 0
    assert migrate_calls == [True]


def test_serve_no_migrate_flag_skips_auto_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators who manage migrations out of band pass --no-migrate."""
    migrate_calls: list[bool] = []

    def fake_migrate() -> None:
        migrate_calls.append(True)

    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli_module, "_auto_migrate", fake_migrate)

    assert main(["serve", "--no-migrate"]) == 0
    assert migrate_calls == []


def test_serve_continues_when_auto_migrate_raises(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Auto-migration failures shouldn't prevent the server from
    starting (e.g. operator already migrated manually with a custom
    URL). The CLI surfaces a clear warning on stderr but proceeds."""
    def boom() -> None:
        raise RuntimeError("no alembic.ini visible")

    uvicorn_called: list[bool] = []
    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: uvicorn_called.append(True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli_module, "_auto_migrate", boom)

    assert main(["serve"]) == 0
    assert uvicorn_called == [True]
    err = capsys.readouterr().err
    assert "skipping auto-migration" in err


def test_serve_auto_seed_runs_when_db_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stage 15.5 — `docker run … ayiru` must produce a populated graph
    on first start. The --auto-seed flag invokes the bundled seed
    runner when the claim count is zero post-migration."""
    seed_calls: list[list[str]] = []

    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli_module, "_auto_migrate", lambda: None)

    class _EmptyQuery:
        def count(self) -> int:
            return 0

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def query(self, _record):
            return _EmptyQuery()

    monkeypatch.setattr(
        "app.db.session.SessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.seed_data.runner.main",
        lambda argv: seed_calls.append(argv) or 0,
    )

    assert main(["serve", "--auto-seed"]) == 0
    assert seed_calls == [[]]
    err = capsys.readouterr().err
    assert "auto-seed populating empty database" in err


def test_serve_auto_seed_skips_when_db_has_claims(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Idempotency: container restarts against a persistent volume must
    not re-seed. The auto-seed path inspects claim count first."""
    seed_calls: list[list[str]] = []

    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli_module, "_auto_migrate", lambda: None)

    class _NonEmptyQuery:
        def count(self) -> int:
            return 47

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def query(self, _record):
            return _NonEmptyQuery()

    monkeypatch.setattr(
        "app.db.session.SessionLocal",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "app.seed_data.runner.main",
        lambda argv: seed_calls.append(argv) or 0,
    )

    assert main(["serve", "--auto-seed"]) == 0
    assert seed_calls == []
    err = capsys.readouterr().err
    assert "auto-seed no-op" in err
    assert "47" in err


def test_serve_without_auto_seed_does_not_invoke_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default dev workflow (`ayiru serve --reload`) must never
    silently seed — that would clobber whatever the developer is
    iterating on. Auto-seed is opt-in via the flag."""
    seed_calls: list[list[str]] = []

    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli_module, "_auto_migrate", lambda: None)
    monkeypatch.setattr(
        "app.seed_data.runner.main",
        lambda argv: seed_calls.append(argv) or 0,
    )

    assert main(["serve"]) == 0
    assert seed_calls == []


# ---------------------------------------------------------------------------
# `ayiru mcp` — stdio bridge
# ---------------------------------------------------------------------------


def test_mcp_subcommand_invokes_build_default_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served: dict[str, bool] = {"called": False}

    class FakeServer:
        def serve(self) -> None:  # matches real signature: default stdin/stdout
            served["called"] = True

    monkeypatch.setattr(
        "app.mcp_server.server.build_default_server",
        lambda: FakeServer(),
    )
    assert main(["mcp"]) == 0
    assert served["called"] is True


def test_mcp_subcommand_warns_when_api_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stage 15.8 — the operator who set AYIRU_API_KEY almost certainly
    expects it to gate every surface. The MCP stdio path has no transport
    to attach credentials to, so the only honest mitigation is a stderr
    disclosure at startup. The HTTP API stays gated; the warning makes
    the asymmetry visible."""

    monkeypatch.setenv("AYIRU_API_KEY", "secret-token")

    class FakeServer:
        def serve(self) -> None:
            return None

    monkeypatch.setattr(
        "app.mcp_server.server.build_default_server",
        lambda: FakeServer(),
    )
    assert main(["mcp"]) == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "AYIRU_API_KEY" in captured.err
    assert "MCP stdio" in captured.err
    # The JSON-RPC stdout stream must NOT carry the warning — that would
    # break a client framer that expects valid JSON per line.
    assert captured.out == ""


def test_mcp_subcommand_silent_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without AYIRU_API_KEY the disclosure is noise; emit nothing."""

    monkeypatch.delenv("AYIRU_API_KEY", raising=False)

    class FakeServer:
        def serve(self) -> None:
            return None

    monkeypatch.setattr(
        "app.mcp_server.server.build_default_server",
        lambda: FakeServer(),
    )
    assert main(["mcp"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# ---------------------------------------------------------------------------
# `ayiru query` — wraps QueryEngine.validate_command
# ---------------------------------------------------------------------------


def test_query_block_returns_exit_code_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`safe_to_auto_execute=False` ⇒ exit 2 so shell pipelines can guard
    on the verdict."""

    class FakeEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def validate_command(self, *, tool_id: str, command: str) -> Any:
            from datetime import datetime, timezone

            from app.schemas.enums import (
                ConfidenceBand,
                RiskLevel,
                VerificationLevel,
            )
            from app.schemas.query import ValidateCommandResponse

            return ValidateCommandResponse(
                tool_id=tool_id,
                command=command,
                matched_claim_id="claim_xyz",
                match_method="prefix",
                safe_to_auto_execute=False,
                requires_human_confirmation=True,
                risk_level=RiskLevel.CRITICAL,
                verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                confidence=0.92,
                confidence_band=ConfidenceBand.HIGH,
                reasons=["Safety policy blocks auto-execution at risk level 'critical'."],
                verdict_generated_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine", FakeEngine
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["query", "--tool", "github-cli", "--command", "gh repo delete x"])
    out = capsys.readouterr().out
    assert "BLOCK" in out
    assert "risk=critical" in out
    assert "claim_xyz" in out
    assert rc == 2


def test_query_allow_returns_exit_code_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def validate_command(self, *, tool_id: str, command: str) -> Any:
            from datetime import datetime, timezone

            from app.schemas.enums import (
                ConfidenceBand,
                RiskLevel,
                VerificationLevel,
            )
            from app.schemas.query import ValidateCommandResponse

            return ValidateCommandResponse(
                tool_id=tool_id,
                command=command,
                matched_claim_id="claim_safe",
                match_method="exact",
                safe_to_auto_execute=True,
                requires_human_confirmation=False,
                risk_level=RiskLevel.LOW,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.95,
                confidence_band=ConfidenceBand.STRONG,
                reasons=["Read-only command; safe to auto-execute."],
                verdict_generated_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine", FakeEngine
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["query", "--tool", "git", "--command", "git status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ALLOW" in out
    assert "risk=low" in out


def test_query_json_flag_emits_parseable_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def validate_command(self, *, tool_id: str, command: str) -> Any:
            from datetime import datetime, timezone

            from app.schemas.enums import (
                ConfidenceBand,
                VerificationLevel,
            )
            from app.schemas.query import ValidateCommandResponse

            return ValidateCommandResponse(
                tool_id=tool_id,
                command=command,
                matched_claim_id=None,
                match_method="none",
                safe_to_auto_execute=False,
                requires_human_confirmation=True,
                risk_level=None,
                verification_level=VerificationLevel.L0_UNVERIFIED,
                confidence=0.0,
                confidence_band=ConfidenceBand.NONE,
                reasons=["No verified claim matches this command for the requested tool."],
                verdict_generated_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine", FakeEngine
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    main(["query", "--tool", "git", "--command", "git frobnicate", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["safe_to_auto_execute"] is False
    assert payload["match_method"] == "none"


# ---------------------------------------------------------------------------
# `ayiru ask` — Stage 17 retrieval CLI
# ---------------------------------------------------------------------------


def _build_fake_ask_engine(*, fallback: bool, answer_count: int = 2) -> Any:
    """Build a FakeEngine class that returns a deterministic AskResponse.

    Cribbed from the validate_command fakes — keeps the CLI tests
    hermetic by avoiding any real ClaimStore or graph state. The
    response shape mirrors what QueryEngine.ask would have returned."""

    from datetime import datetime, timezone

    from app.schemas.enums import (
        ConfidenceBand,
        RiskLevel,
        TrustLevel,
        VerificationLevel,
    )
    from app.schemas.evidence import EvidenceType
    from app.schemas.query import Answer, AskResponse, EvidenceCitation

    class FakeEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def ask(
            self,
            *,
            question: str,
            limit: int = 5,
            tool_id_hint: str | None = None,
        ) -> Any:
            if fallback:
                return AskResponse(
                    question=question,
                    answers=[],
                    fallback_recommended=True,
                    estimated_tokens_saved=0,
                    generated_at=datetime.now(timezone.utc),
                )
            answers = [
                Answer(
                    claim_id=f"claim_test_{i}",
                    subject="docker rm",
                    statement="removes one or more containers",
                    tool_id=tool_id_hint or "docker",
                    confidence=0.92,
                    confidence_band=ConfidenceBand.STRONG,
                    verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                    risk_level=RiskLevel.CRITICAL,
                    evidence=[
                        EvidenceCitation(
                            evidence_type=EvidenceType.OFFICIAL_DOCS,
                            source_uri="https://docs.docker.com/reference/cli/docker/container/rm/",
                            trust_level=TrustLevel.HIGH,
                        )
                    ],
                    match_reason="subject hit on ['docker', 'rm']",
                )
                for i in range(answer_count)
            ]
            return AskResponse(
                question=question,
                answers=answers,
                fallback_recommended=False,
                estimated_tokens_saved=420,
                generated_at=datetime.now(timezone.utc),
            )

    return FakeEngine


def test_ask_hit_returns_exit_code_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A confident match → exit 0. Lets shell pipelines branch on
    `ayiru ask "..." && do_thing`."""
    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine",
        _build_fake_ask_engine(fallback=False),
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["ask", "how do I delete a docker container"])
    out = capsys.readouterr().out
    assert "HIT" in out
    assert "estimated_tokens_saved=420" in out
    assert "docker rm" in out
    assert "https://docs.docker.com" in out
    assert rc == 0


def test_ask_fallback_returns_exit_code_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fallback verdict → exit 1. Distinct from `ayiru query`'s 0/2
    contract so a shell pipeline can `ayiru ask "..." || curl
    web-search-fallback` without ambiguity."""
    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine",
        _build_fake_ask_engine(fallback=True),
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["ask", "aurora borealis quantum"])
    out = capsys.readouterr().out
    assert "FALLBACK" in out
    assert "web_search" in out
    assert rc == 1


def test_ask_json_flag_emits_parseable_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--json` must emit the AskResponse dump unmodified so scripts
    can pipe it to `jq` and friends."""
    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine",
        _build_fake_ask_engine(fallback=False, answer_count=1),
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["ask", "docker container", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["fallback_recommended"] is False
    assert payload["estimated_tokens_saved"] == 420
    assert len(payload["answers"]) == 1
    assert payload["answers"][0]["tool_id"] == "docker"
    assert rc == 0


def test_ask_forwards_tool_hint_to_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--tool github-cli` must surface as tool_id_hint to the engine.
    Without this, the CLI silently ignores the narrowing flag."""
    captured: dict[str, Any] = {}

    from datetime import datetime, timezone

    from app.schemas.query import AskResponse

    class CaptureEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def ask(self, *, question: str, limit: int, tool_id_hint: str | None) -> Any:
            captured["question"] = question
            captured["limit"] = limit
            captured["tool_id_hint"] = tool_id_hint
            return AskResponse(
                question=question,
                answers=[],
                fallback_recommended=True,
                estimated_tokens_saved=0,
                generated_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine", CaptureEngine
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    main(["ask", "delete a repo", "--tool", "github-cli", "--limit", "3"])
    assert captured["question"] == "delete a repo"
    assert captured["limit"] == 3
    assert captured["tool_id_hint"] == "github-cli"


# ---------------------------------------------------------------------------
# `ayiru tools`
# ---------------------------------------------------------------------------


def test_tools_empty_message_when_no_specs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def search_tools(self, *, query: str, limit: int, offset: int) -> Any:
            from app.schemas.query import SearchToolsResponse

            return SearchToolsResponse(
                query="", matches=[], total=0, limit=limit, offset=offset
            )

    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine", FakeEngine
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )
    rc = main(["tools"])
    assert rc == 0
    assert "no tools published yet" in capsys.readouterr().out


def test_tools_table_renders_published_specs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeEngine:
        def __init__(self, _store: Any) -> None:
            pass

        def search_tools(self, *, query: str, limit: int, offset: int) -> Any:
            from app.schemas.enums import RiskLevel, VerificationLevel
            from app.schemas.query import SearchToolsResponse, ToolMatchSummary

            return SearchToolsResponse(
                query="",
                matches=[
                    ToolMatchSummary(
                        tool_id="github-cli",
                        name="GitHub CLI",
                        interfaces=["cli"],
                        capability_count=12,
                        verified_command_count=3,
                        highest_risk_level=RiskLevel.CRITICAL,
                        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                        match_reason="listed",
                    ),
                    ToolMatchSummary(
                        tool_id="git",
                        name="git",
                        interfaces=["cli"],
                        capability_count=4,
                        verified_command_count=1,
                        highest_risk_level=RiskLevel.LOW,
                        verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                        match_reason="listed",
                    ),
                ],
                total=2,
                limit=limit,
                offset=offset,
            )

    monkeypatch.setattr(
        "app.services.query_engine.QueryEngine", FakeEngine
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["tools"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "github-cli" in out
    assert "L2_source_verified" in out


# ---------------------------------------------------------------------------
# `ayiru verify`
# ---------------------------------------------------------------------------


def test_verify_returns_zero_when_runtime_check_passes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.schemas.verification import RuntimeVerificationResponse
    from app.schemas.enums import VerificationLevel

    class FakeService:
        def __init__(self, _store: Any) -> None:
            pass

        def verify(self, *, claim_id: str, submitted_by: str) -> Any:
            return RuntimeVerificationResponse(
                claim_id=claim_id,
                verifier_kind="cli_tool_existence",
                runtime_check_passed=True,
                promoted_to_l3=True,
                new_verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                reasons=["Help output matched expected pattern.", f"submitted_by={submitted_by}"],
            )

    monkeypatch.setattr(
        "app.services.runtime_verifier.RuntimeVerificationService", FakeService
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["verify", "--claim-id", "claim_abc"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["runtime_check_passed"] is True
    assert payload["promoted_to_l3"] is True
    assert rc == 0


def test_verify_returns_nonzero_when_runtime_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas.verification import RuntimeVerificationResponse
    from app.schemas.enums import VerificationLevel

    class FakeService:
        def __init__(self, _store: Any) -> None:
            pass

        def verify(self, *, claim_id: str, submitted_by: str) -> Any:
            return RuntimeVerificationResponse(
                claim_id=claim_id,
                verifier_kind="cli_tool_existence",
                runtime_check_passed=False,
                promoted_to_l3=False,
                new_verification_level=VerificationLevel.L2_SOURCE_VERIFIED,
                reasons=["sandbox exit code 127 (command not found)"],
            )

    monkeypatch.setattr(
        "app.services.runtime_verifier.RuntimeVerificationService", FakeService
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["verify", "--claim-id", "claim_abc"])
    assert rc == 2


def test_verify_unknown_claim_prints_error_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.services.runtime_verifier import RuntimeVerificationError

    class FakeService:
        def __init__(self, _store: Any) -> None:
            pass

        def verify(self, *, claim_id: str, submitted_by: str) -> Any:
            raise RuntimeVerificationError(f"Claim '{claim_id}' does not exist.")

    monkeypatch.setattr(
        "app.services.runtime_verifier.RuntimeVerificationService", FakeService
    )
    monkeypatch.setattr(
        "app.services.claim_store.get_claim_store", lambda: object()
    )

    rc = main(["verify", "--claim-id", "claim_missing"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Claim 'claim_missing' does not exist." in captured.err


# ---------------------------------------------------------------------------
# `ayiru seed`
# ---------------------------------------------------------------------------


def test_seed_forwards_reset_and_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should forward `--reset` and `--database-url` verbatim to
    `app.seed_data.runner.main`, not silently drop them."""

    captured: dict[str, list[str] | None] = {"argv": None}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("app.seed_data.runner.main", fake_main)
    monkeypatch.delenv("AYIRU_SEED_SCRIPT", raising=False)

    rc = main(
        [
            "seed",
            "--reset",
            "--database-url",
            "sqlite:///tmp/test.db",
        ]
    )
    assert rc == 0
    assert captured["argv"] == [
        "--reset",
        "--database-url",
        "sqlite:///tmp/test.db",
    ]


def test_seed_without_flags_passes_empty_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str] | None] = {"argv": None}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("app.seed_data.runner.main", fake_main)
    monkeypatch.delenv("AYIRU_SEED_SCRIPT", raising=False)

    main(["seed"])
    assert captured["argv"] == []


def test_seed_env_var_override_loads_external_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`AYIRU_SEED_SCRIPT` lets advanced users point the CLI at a
    fork of `scripts/seed_examples.py` without modifying the install."""
    script_path = tmp_path / "fake_seed.py"
    script_path.write_text(
        "def main(argv=None):\n"
        "    import os\n"
        "    os.environ['_AYIRU_FAKE_SEED_RAN'] = ','.join(argv or [])\n"
        "    return 0\n"
    )
    monkeypatch.setenv("AYIRU_SEED_SCRIPT", str(script_path))
    monkeypatch.delenv("_AYIRU_FAKE_SEED_RAN", raising=False)

    rc = main(["seed", "--reset"])
    assert rc == 0
    import os as _os

    assert _os.environ["_AYIRU_FAKE_SEED_RAN"] == "--reset"


def test_in_package_seed_runner_is_importable() -> None:
    """Stage 14: the wheel-bundled seed runner must be importable
    directly. This is the path `ayiru seed` uses by default."""
    from app.seed_data import runner

    assert callable(getattr(runner, "main", None))


# ---------------------------------------------------------------------------
# `ayiru migrate`
# ---------------------------------------------------------------------------


def test_migrate_invokes_alembic_upgrade_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ayiru migrate` runs `alembic upgrade head` against the
    config returned by `make_alembic_config`, passing the requested DB
    URL through."""
    captured: dict[str, Any] = {}

    class FakeConfig:
        def __init__(self) -> None:
            self._options: dict[str, str] = {}

        def set_main_option(self, key: str, value: str) -> None:
            self._options[key] = value

    fake_cfg = FakeConfig()
    fake_cfg.set_main_option("script_location", "/fake/_alembic")

    def fake_make(database_url: str | None = None) -> Any:
        if database_url is not None:
            fake_cfg.set_main_option("sqlalchemy.url", database_url)
        return fake_cfg

    monkeypatch.setattr(
        "app.services.alembic_config.make_alembic_config", fake_make
    )

    fake_command = type(sys)("alembic.command")
    def fake_upgrade(cfg: Any, target: str) -> None:
        captured["cfg"] = cfg
        captured["target"] = target
    fake_command.upgrade = fake_upgrade  # type: ignore[attr-defined]
    fake_alembic = type(sys)("alembic")
    fake_alembic.command = fake_command  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_command)

    rc = main(["migrate", "--database-url", "sqlite:///tmp/m.db"])
    assert rc == 0
    assert captured["target"] == "head"
    assert captured["cfg"]._options["sqlalchemy.url"] == "sqlite:///tmp/m.db"


def test_migrate_surfaces_clean_error_when_config_resolver_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If `make_alembic_config` raises (e.g. wheel install with
    corrupt package data and no env-var override), the CLI surfaces
    the message on stderr and exits 1 — no traceback dumped on the
    user."""
    def boom(database_url: str | None = None) -> Any:
        raise RuntimeError("bundled migrations missing")

    monkeypatch.setattr(
        "app.services.alembic_config.make_alembic_config", boom
    )

    fake_alembic = type(sys)("alembic")
    fake_alembic.command = type(sys)("alembic.command")  # type: ignore[attr-defined]
    fake_alembic.command.upgrade = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_alembic.command)

    rc = main(["migrate"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "bundled migrations missing" in err


# ---------------------------------------------------------------------------
# `ayiru ingest --resume / --force` (Stage 20.4)
# ---------------------------------------------------------------------------


def _write_minimal_seed(tmp_path, tool_id: str, url: str):
    seed = {
        "version": 1,
        "defaults": {"submitted_by": "test-bulk", "verify": False},
        "tools": [{"tool_id": tool_id, "name": tool_id, "urls": [url]}],
    }
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed))
    return path


class _FakeStore:
    def __init__(self, *, completed_urls: set[tuple[str, str]] | None = None) -> None:
        self._completed = completed_urls or set()
        self.skip_checks: list[tuple[str, str]] = []

    def has_completed_ingestion_run(self, *, tool_id: str, command: str) -> bool:
        self.skip_checks.append((tool_id, command))
        return (tool_id, command) in self._completed


class _FakeService:
    def __init__(self, *_, **__) -> None:
        self.calls: list[tuple[str, str]] = []

    def ingest(self, *, tool_id: str, url: str, **_kwargs):
        from app.schemas.enums import IngestionStatus
        from app.schemas.ingestion import DocsIngestionResponse

        self.calls.append((tool_id, url))
        return DocsIngestionResponse(
            run_id=f"run-{len(self.calls)}",
            status=IngestionStatus.COMPLETED,
            final_url=url,
        )


def _patch_ingest_deps(monkeypatch, store, service):
    monkeypatch.setattr("app.services.claim_store.get_claim_store", lambda: store)
    monkeypatch.setattr(
        "app.services.docs_ingestion.DocsIngestionService",
        lambda *a, **k: service,
    )


def test_ingest_resume_skips_urls_with_completed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    seed_path = _write_minimal_seed(tmp_path, "git", "https://git-scm.com/docs/git-status")
    store = _FakeStore(completed_urls={("git", "https://git-scm.com/docs/git-status")})
    service = _FakeService()
    _patch_ingest_deps(monkeypatch, store, service)

    rc = main(["ingest", "--tool-list", str(seed_path), "--resume"])
    out = capsys.readouterr().out
    assert rc == 0
    assert service.calls == []  # never ingested
    assert "(resume: prior COMPLETED run, skipped)" in out
    assert "1 skipped" in out


def test_ingest_resume_processes_urls_without_completed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    seed_path = _write_minimal_seed(tmp_path, "git", "https://git-scm.com/docs/git-status")
    store = _FakeStore(completed_urls=set())  # nothing done yet
    service = _FakeService()
    _patch_ingest_deps(monkeypatch, store, service)

    rc = main(["ingest", "--tool-list", str(seed_path), "--resume"])
    assert rc == 0
    assert service.calls == [("git", "https://git-scm.com/docs/git-status")]


def test_ingest_force_overrides_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    """--resume and --force together is a CLI usage error: the operator
    should pick one. _cmd_ingest exits 2 before any work happens."""

    seed_path = _write_minimal_seed(tmp_path, "git", "https://git-scm.com/docs/git-status")
    store = _FakeStore()
    service = _FakeService()
    _patch_ingest_deps(monkeypatch, store, service)

    rc = main(["ingest", "--tool-list", str(seed_path), "--resume", "--force"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "mutually exclusive" in err
    assert service.calls == []


def test_ingest_force_alone_processes_all_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Without --resume, --force is a no-op signal — the loop processes
    every URL regardless of prior completed runs (no skip check fires)."""

    seed_path = _write_minimal_seed(tmp_path, "git", "https://git-scm.com/docs/git-status")
    store = _FakeStore(completed_urls={("git", "https://git-scm.com/docs/git-status")})
    service = _FakeService()
    _patch_ingest_deps(monkeypatch, store, service)

    rc = main(["ingest", "--tool-list", str(seed_path), "--force"])
    assert rc == 0
    assert service.calls == [("git", "https://git-scm.com/docs/git-status")]
    assert store.skip_checks == []  # resume off → store never queried


def test_ingest_default_does_not_check_resume_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Backward-compat: without --resume, the CLI behaves exactly as it
    did pre-20.4 — no skip check, every URL is processed."""

    seed_path = _write_minimal_seed(tmp_path, "git", "https://git-scm.com/docs/git-status")
    store = _FakeStore(completed_urls={("git", "https://git-scm.com/docs/git-status")})
    service = _FakeService()
    _patch_ingest_deps(monkeypatch, store, service)

    rc = main(["ingest", "--tool-list", str(seed_path)])
    assert rc == 0
    assert service.calls == [("git", "https://git-scm.com/docs/git-status")]
    assert store.skip_checks == []
