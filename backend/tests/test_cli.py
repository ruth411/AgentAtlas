"""Stage 12: `agentatlas` CLI tests.

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
# `agentatlas --version` / `--help`
# ---------------------------------------------------------------------------


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"agentatlas {cli_module.PACKAGE_VERSION}"


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
# `agentatlas serve` — uvicorn entry point
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

    assert main(["serve"]) == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert captured["reload"] is False


# ---------------------------------------------------------------------------
# `agentatlas mcp` — stdio bridge
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


# ---------------------------------------------------------------------------
# `agentatlas query` — wraps QueryEngine.validate_command
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
# `agentatlas tools`
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
# `agentatlas verify`
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
# `agentatlas seed`
# ---------------------------------------------------------------------------


def test_seed_forwards_reset_and_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should forward `--reset` and `--database-url` verbatim to
    `scripts/seed_examples.main`, not silently drop them."""

    captured: dict[str, list[str] | None] = {"argv": None}

    fake_module = type(sys)("fake_seed_examples")

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    fake_module.main = fake_main  # type: ignore[attr-defined]

    monkeypatch.setattr(cli_module, "_load_seed_module", lambda: fake_module)

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

    fake_module = type(sys)("fake_seed_examples")

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    fake_module.main = fake_main  # type: ignore[attr-defined]
    monkeypatch.setattr(cli_module, "_load_seed_module", lambda: fake_module)

    main(["seed"])
    assert captured["argv"] == []


def test_load_seed_module_finds_real_script() -> None:
    """End-to-end: from the installed-package path, the loader should
    walk up the source tree and find `scripts/seed_examples.py`."""
    module = cli_module._load_seed_module()
    assert callable(getattr(module, "main", None))


# ---------------------------------------------------------------------------
# `agentatlas migrate`
# ---------------------------------------------------------------------------


def test_migrate_invokes_alembic_upgrade_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    fake_command = type(sys)("alembic.command")

    def fake_upgrade(cfg: Any, target: str) -> None:
        captured["cfg"] = cfg
        captured["target"] = target

    fake_command.upgrade = fake_upgrade  # type: ignore[attr-defined]

    class FakeConfig:
        def __init__(self, path: str) -> None:
            captured["ini_path"] = path
            self._options: dict[str, str] = {}

        def set_main_option(self, key: str, value: str) -> None:
            self._options[key] = value

    fake_config_mod = type(sys)("alembic.config")
    fake_config_mod.Config = FakeConfig  # type: ignore[attr-defined]

    fake_alembic = type(sys)("alembic")
    fake_alembic.command = fake_command  # type: ignore[attr-defined]
    fake_alembic.config = fake_config_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_command)
    monkeypatch.setitem(sys.modules, "alembic.config", fake_config_mod)

    rc = main(["migrate", "--database-url", "sqlite:///tmp/m.db"])
    assert rc == 0
    assert captured["target"] == "head"
    assert captured["ini_path"].endswith("alembic.ini")
    assert captured["cfg"]._options["sqlalchemy.url"] == "sqlite:///tmp/m.db"


def test_migrate_without_alembic_ini_reports_clean_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTATLAS_ALEMBIC_INI", "/definitely/missing/alembic.ini")

    fake_command = type(sys)("alembic.command")
    fake_command.upgrade = lambda *a, **k: None  # type: ignore[attr-defined]
    fake_config_mod = type(sys)("alembic.config")

    class _NoConfig:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise AssertionError(
                "Config() must not be constructed when the ini is missing."
            )

    fake_config_mod.Config = _NoConfig  # type: ignore[attr-defined]
    fake_alembic = type(sys)("alembic")
    fake_alembic.command = fake_command  # type: ignore[attr-defined]
    fake_alembic.config = fake_config_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_command)
    monkeypatch.setitem(sys.modules, "alembic.config", fake_config_mod)

    rc = main(["migrate"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "could not locate alembic.ini" in err
