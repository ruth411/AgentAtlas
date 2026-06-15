from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from app.services.structured_cli_ingestion import StructuredCliIngestionService
from app.services.structured_knowledge_store import StructuredKnowledgeStore


FIXED_TIME = datetime(2026, 6, 12, tzinfo=timezone.utc)

_PR_CREATE_HELP = """Create a pull request on GitHub.

Adding a pull request to projects requires authorization with the `project` scope.
To authorize, run `gh auth refresh -s project`.

USAGE
  gh pr create [flags]

ALIASES
  gh pr new

FLAGS
  -B, --base branch          The branch into which you want your code merged
  -b, --body string          Body for the pull request
      --dry-run              Print details instead of creating the PR. May still push git changes.
  -t, --title string         Title for the pull request

INHERITED FLAGS
  -R, --repo [HOST/]OWNER/REPO   Select another repository using the [HOST/]OWNER/REPO format
      --help                     Show help for command
"""

_REPO_DELETE_HELP = """Delete a GitHub repository.

Deletion requires authorization with the `delete_repo` scope.
To authorize, run `gh auth refresh -s delete_repo`

USAGE
  gh repo delete [<repository>] [flags]

FLAGS
  --yes   Confirm deletion without prompting

INHERITED FLAGS
  --help   Show help for command
"""


_SECRET_DELETE_HELP = """Delete a secret on one of the following levels.

USAGE
  gh secret delete <secret-name> [flags]

FLAGS
  -a, --app string   Application name
  -e, --env string   Set deployment environment

INHERITED FLAGS
  --help   Show help for command
"""

_EXTENSION_REMOVE_HELP = """Remove an installed extension.

USAGE
  gh extension remove <name>

INHERITED FLAGS
  --help   Show help for command
"""

_REPO_ARCHIVE_HELP = """Archive a GitHub repository.

USAGE
  gh repo archive [<repository>] [flags]

FLAGS
  -y, --yes   Skip the confirmation prompt

INHERITED FLAGS
  --help   Show help for command
"""

_PR_LIST_HELP = """List pull requests in a repository.

USAGE
  gh pr list [flags]

FLAGS
  -s, --state string   Filter by state: {open|closed|merged|all}

INHERITED FLAGS
  --help   Show help for command
"""


class FakeGhHelpRunner:
    def __init__(self, outputs: dict[tuple[str, ...], str]) -> None:
        self._outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...]) -> str:
        self.calls.append(argv)
        try:
            return self._outputs[argv]
        except KeyError as exc:
            raise AssertionError(f"Unexpected gh help argv: {argv}") from exc


def test_ingest_gh_persists_structured_rows_for_selected_subjects(tmp_path) -> None:
    db_path = tmp_path / "structured-gh.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")
    runner = FakeGhHelpRunner(
        {
            ("gh", "pr", "create", "--help"): _PR_CREATE_HELP,
            ("gh", "repo", "delete", "--help"): _REPO_DELETE_HELP,
        }
    )

    report = StructuredCliIngestionService(store, runner=runner, now=FIXED_TIME).ingest_gh(
        subject_ids=["gh-pr-create", "gh-repo-delete"]
    )

    assert report.subjects_written == 2
    assert report.capability_rows_written >= 7
    assert report.constraint_rows_written >= 4
    assert report.effect_rows_written == 2

    conn = sqlite3.connect(db_path)
    try:
        subject_rows = conn.execute(
            "SELECT subject_id, verification_level FROM subjects ORDER BY subject_id"
        ).fetchall()
        assert subject_rows == [
            ("gh-pr-create", "L3_runtime_verified"),
            ("gh-repo-delete", "L3_runtime_verified"),
        ]

        invocation_json = conn.execute(
            "SELECT detail_json FROM capabilities "
            "WHERE subject_id = 'gh-pr-create' AND capability_type = 'invocation'"
        ).fetchone()
        assert invocation_json is not None
        invocation = json.loads(invocation_json[0])
        assert invocation["argv_schema"]["program"] == "gh"
        assert invocation["argv_schema"]["subcommand_path"] == ["pr", "create"]
        flag_names = {flag["name"] for flag in invocation["flag_schema"]}
        assert "--title" in flag_names
        assert "--repo" in flag_names

        auth_scopes = conn.execute(
            "SELECT detail_json FROM constraints "
            "WHERE subject_id = 'gh-pr-create' AND constraint_kind = 'auth_scope'"
        ).fetchall()
        assert {json.loads(row[0])["scope"] for row in auth_scopes} == {"project"}

        effect_row = conn.execute(
            "SELECT effect_kind, destructive, reversible, mutates_remote_state "
            "FROM effects WHERE subject_id = 'gh-repo-delete'"
        ).fetchone()
        assert effect_row == ("destructive", 1, 0, 1)
    finally:
        conn.close()


def test_ingest_gh_is_idempotent_for_same_subjects(tmp_path) -> None:
    db_path = tmp_path / "structured-gh-idempotent.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")
    runner = FakeGhHelpRunner({("gh", "pr", "create", "--help"): _PR_CREATE_HELP})
    service = StructuredCliIngestionService(store, runner=runner, now=FIXED_TIME)

    first = service.ingest_gh(subject_ids=["gh-pr-create"])
    second = service.ingest_gh(subject_ids=["gh-pr-create"])

    assert first.subjects_written == 1
    assert second.subjects_written == 1

    conn = sqlite3.connect(db_path)
    try:
        subject_count = conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
        capability_count = conn.execute("SELECT COUNT(*) FROM capabilities").fetchone()[0]
        constraint_count = conn.execute("SELECT COUNT(*) FROM constraints").fetchone()[0]
        effect_count = conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
        assert subject_count == 1
        assert capability_count == first.capability_rows_written
        assert constraint_count == first.constraint_rows_written
        assert effect_count == first.effect_rows_written
    finally:
        conn.close()


def _effect_for(conn: sqlite3.Connection, subject_id: str) -> tuple:
    return conn.execute(
        "SELECT effect_kind, destructive, reversible, mutates_remote_state, "
        "json_extract(detail_json, '$.classification_reason') "
        "FROM effects WHERE subject_id = ?",
        (subject_id,),
    ).fetchone()


def test_destructive_verb_subcommands_are_flagged_destructive(tmp_path) -> None:
    db_path = tmp_path / "structured-destructive.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")
    runner = FakeGhHelpRunner(
        {
            ("gh", "secret", "delete", "--help"): _SECRET_DELETE_HELP,
            ("gh", "extension", "remove", "--help"): _EXTENSION_REMOVE_HELP,
            ("gh", "repo", "archive", "--help"): _REPO_ARCHIVE_HELP,
            ("gh", "pr", "list", "--help"): _PR_LIST_HELP,
        }
    )
    StructuredCliIngestionService(store, runner=runner, now=FIXED_TIME).ingest_gh(
        subject_ids=[
            "gh-secret-delete",
            "gh-extension-remove",
            "gh-repo-archive",
            "gh-pr-list",
        ]
    )

    conn = sqlite3.connect(db_path)
    try:
        # `delete` and `remove` leaves are irreversible destructive mutations.
        kind, destructive, reversible, mutates, reason = _effect_for(conn, "gh-secret-delete")
        assert (kind, destructive, reversible, mutates) == ("destructive", 1, 0, 1)
        assert "destructive" in reason and "gh secret delete" in reason

        kind, destructive, reversible, mutates, _ = _effect_for(conn, "gh-extension-remove")
        assert (kind, destructive, reversible, mutates) == ("destructive", 1, 0, 1)

        # `archive` mutates remote state but does not destroy data.
        kind, destructive, _, mutates, _ = _effect_for(conn, "gh-repo-archive")
        assert destructive == 0
        assert mutates == 1

        # A read-only list command is neither destructive nor mutating.
        kind, destructive, _, mutates, _ = _effect_for(conn, "gh-pr-list")
        assert (destructive, mutates) == (0, 0)
        assert kind == "network"
    finally:
        conn.close()


def test_effect_reasons_are_per_command_not_a_single_template(tmp_path) -> None:
    db_path = tmp_path / "structured-reasons.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")
    runner = FakeGhHelpRunner(
        {
            ("gh", "pr", "create", "--help"): _PR_CREATE_HELP,
            ("gh", "repo", "delete", "--help"): _REPO_DELETE_HELP,
            ("gh", "secret", "delete", "--help"): _SECRET_DELETE_HELP,
            ("gh", "pr", "list", "--help"): _PR_LIST_HELP,
        }
    )
    StructuredCliIngestionService(store, runner=runner, now=FIXED_TIME).ingest_gh(
        subject_ids=["gh-pr-create", "gh-repo-delete", "gh-secret-delete", "gh-pr-list"]
    )

    conn = sqlite3.connect(db_path)
    try:
        reasons = [
            row[0]
            for row in conn.execute(
                "SELECT json_extract(detail_json, '$.classification_reason') FROM effects"
            ).fetchall()
        ]
        # No collapse to one generic string, and every reason names its command.
        assert len(set(reasons)) >= 3
        assert all(reason.startswith("`gh ") for reason in reasons)
    finally:
        conn.close()


def test_inferred_rows_carry_a_lower_verification_level_than_capabilities(tmp_path) -> None:
    db_path = tmp_path / "structured-levels.db"
    store = StructuredKnowledgeStore(database_url=f"sqlite:///{db_path}")
    runner = FakeGhHelpRunner({("gh", "pr", "create", "--help"): _PR_CREATE_HELP})
    StructuredCliIngestionService(store, runner=runner, now=FIXED_TIME).ingest_gh(
        subject_ids=["gh-pr-create"]
    )

    conn = sqlite3.connect(db_path)
    try:
        # Runtime-parsed capabilities are genuinely runtime-verified.
        cap_levels = {
            row[0]
            for row in conn.execute("SELECT DISTINCT verification_level FROM capabilities")
        }
        assert cap_levels == {"L3_runtime_verified"}

        # Inferred effect classifications must not claim runtime verification.
        effect_levels = {
            row[0] for row in conn.execute("SELECT DISTINCT verification_level FROM effects")
        }
        assert effect_levels == {"L2_source_verified"}

        # Environment constraints are proven by running the binary (L3); the
        # auth-scope constraint is parsed from help text (L2).
        constraint_levels = dict(
            conn.execute(
                "SELECT constraint_kind, verification_level FROM constraints"
            ).fetchall()
        )
        assert constraint_levels["environment"] == "L3_runtime_verified"
        assert constraint_levels["auth_scope"] == "L2_source_verified"
    finally:
        conn.close()
