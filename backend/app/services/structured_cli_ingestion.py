from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Protocol
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.schemas.enums import ConfidenceBand, RiskLevel, VerificationLevel, VerificationStatus
from app.services.contract_paths import contract_path
from app.services.structured_knowledge_store import (
    StructuredCapability,
    StructuredConstraint,
    StructuredEffect,
    StructuredKnowledgeStore,
    StructuredSubject,
)


_GH_TOOL_IDS = ("gh-cli", "gh-config", "gh-workflows")
_FORBIDDEN_TOKENS = ("|", ";", "&", "&&", "||", ">", "<", ">>", "$(", "`", "\n", "\r")
_SECTION_HEADERS = {
    "USAGE",
    "ALIASES",
    "FLAGS",
    "INHERITED FLAGS",
    "EXAMPLES",
    "LEARN MORE",
}
_FLAG_LINE = re.compile(r"^\s{2,}(?:-[A-Za-z0-9],\s+)?--[A-Za-z0-9-]+")
_CHOICES = re.compile(r"\{([^{}]+)\}")
_DEFAULT = re.compile(r"\(default ([^)]+)\)")
_AUTH_SCOPE = re.compile(r"authorization with the `([^`]+)` scope", re.IGNORECASE)
_AUTH_REFRESH = re.compile(r"`gh auth refresh -s ([^`]+)`")
_MIN_SCOPE_SENTENCE = re.compile(r"minimum required scopes .*?: (.+?)\.", re.IGNORECASE)


class StructuredCliIngestionError(ValueError):
    """Raised when structured CLI ingestion cannot continue safely."""


@dataclass(frozen=True)
class GhCommandSource:
    subject_id: str
    subject_name: str
    source_url: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class ParsedFlag:
    name: str
    short: str | None
    takes_value: bool
    value_name: str | None
    value_type: str
    repeatable: bool
    required: bool
    deprecated: bool
    inherited: bool
    description: str
    default: str | int | bool | None
    choices: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "short": self.short,
            "takes_value": self.takes_value,
            "value_name": self.value_name,
            "value_type": self.value_type,
            "repeatable": self.repeatable,
            "required": self.required,
            "deprecated": self.deprecated,
            "inherited": self.inherited,
            "description": self.description,
            "default": self.default,
            "choices": list(self.choices),
        }


@dataclass(frozen=True)
class ParsedHelp:
    command: tuple[str, ...]
    synopsis: str
    description: str
    usage_signature: str
    aliases: list[str]
    flags: list[ParsedFlag]
    positionals: list[dict[str, Any]]
    auth_scopes: list[str]
    preconditions: list[str]
    raw_help: str


@dataclass(frozen=True)
class StructuredIngestionReport:
    subjects_written: int
    capability_rows_written: int
    constraint_rows_written: int
    effect_rows_written: int
    subject_ids: list[str]


class GhHelpRunner(Protocol):
    def run(self, argv: tuple[str, ...]) -> str:
        """Return help text for a safe gh argv ending in --help."""


class SafeGhHelpRunner:
    def run(self, argv: tuple[str, ...]) -> str:
        _assert_safe_gh_help_argv(argv)
        try:
            proc = subprocess.run(
                list(argv),
                cwd="/",
                env={"PATH": os.environ.get("PATH", "")},
                shell=False,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except OSError as exc:
            raise StructuredCliIngestionError(f"Failed to run {' '.join(argv)}: {exc}") from exc
        output = proc.stdout or proc.stderr
        if proc.returncode != 0 or not output.strip():
            raise StructuredCliIngestionError(
                f"Help capture failed for {' '.join(argv)} with exit {proc.returncode}."
            )
        return output


class StructuredCliIngestionService:
    def __init__(
        self,
        store: StructuredKnowledgeStore,
        *,
        runner: GhHelpRunner | None = None,
        now: datetime | None = None,
    ) -> None:
        self._store = store
        self._runner = runner or SafeGhHelpRunner()
        self._now = now

    def ingest_gh(
        self,
        *,
        dry_run: bool = False,
        subject_ids: list[str] | None = None,
    ) -> StructuredIngestionReport:
        selected = set(subject_ids or [])
        sources = [
            item for item in gh_contract_sources() if not selected or item.subject_id in selected
        ]
        written_subjects = 0
        written_capabilities = 0
        written_constraints = 0
        written_effects = 0
        handled: list[str] = []
        for source in sources:
            help_text = self._runner.run(source.command + ("--help",))
            parsed = parse_gh_help(source.command, help_text)
            bundle = build_gh_subject_bundle(source, parsed, captured_at=self._timestamp())
            if not dry_run:
                self._store.upsert_subject_graph(
                    bundle["subject"],
                    capabilities=bundle["capabilities"],
                    constraints=bundle["constraints"],
                    effects=bundle["effects"],
                )
            written_subjects += 1
            written_capabilities += len(bundle["capabilities"])
            written_constraints += len(bundle["constraints"])
            written_effects += len(bundle["effects"])
            handled.append(source.subject_id)
        return StructuredIngestionReport(
            subjects_written=written_subjects,
            capability_rows_written=written_capabilities,
            constraint_rows_written=written_constraints,
            effect_rows_written=written_effects,
            subject_ids=handled,
        )

    def _timestamp(self) -> datetime:
        return self._now or datetime.now(timezone.utc)


@cache
def gh_contract_sources() -> list[GhCommandSource]:
    data = json.loads(contract_path("docs_ingestion_sources.v1.json").read_text())
    sources: list[GhCommandSource] = []
    seen: set[str] = set()
    for tool_id in _GH_TOOL_IDS:
        for item in data["tools"][tool_id]["sources"]:
            url = item["url"]
            command = _command_from_gh_manual_url(url)
            subject_id = _subject_id_for_command(command)
            if subject_id in seen:
                continue
            seen.add(subject_id)
            sources.append(
                GhCommandSource(
                    subject_id=subject_id,
                    subject_name=item["subject"],
                    source_url=url,
                    command=command,
                )
            )
    return sources


def parse_gh_help(command: tuple[str, ...], raw_help: str) -> ParsedHelp:
    lines = [line.rstrip() for line in raw_help.splitlines()]
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        header = line.strip()
        if header in _SECTION_HEADERS:
            current = header
            sections.setdefault(header, [])
            continue
        if current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    preamble_text = "\n".join(preamble).strip()
    preamble_lines = [line.strip() for line in preamble if line.strip()]
    synopsis = preamble_lines[0] if preamble_lines else " ".join(command)
    description = "\n".join(preamble_lines[1:]).strip()
    usage_signature = _first_non_empty(sections.get("USAGE", [])) or " ".join(command)
    aliases = [line.strip() for line in sections.get("ALIASES", []) if line.strip()]
    flags = _parse_flags(sections.get("FLAGS", []), inherited=False)
    flags.extend(_parse_flags(sections.get("INHERITED FLAGS", []), inherited=True))
    return ParsedHelp(
        command=command,
        synopsis=synopsis,
        description=description,
        usage_signature=usage_signature,
        aliases=aliases,
        flags=flags,
        positionals=_parse_positionals(command, usage_signature),
        auth_scopes=_extract_auth_scopes(preamble_text),
        preconditions=_extract_preconditions(preamble_text),
        raw_help=raw_help,
    )


def build_gh_subject_bundle(
    source: GhCommandSource,
    parsed: ParsedHelp,
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    subject = StructuredSubject(
        subject_id=source.subject_id,
        subject_kind="tool",
        name=source.subject_name,
        family="gh",
        verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
        provenance_claim_ids=[],
        created_at=captured_at,
        updated_at=captured_at,
    )
    capabilities: list[StructuredCapability] = [
        StructuredCapability(
            capability_id=_stable_row_id(source.subject_id, "existence", "command"),
            subject_id=source.subject_id,
            capability_type="existence",
            title=f"{' '.join(source.command)} exists",
            detail=_validated_detail(
                {
                    "kind": "existence",
                    "command": " ".join(source.command),
                    "source_url": source.source_url,
                    "usage_signature": parsed.usage_signature,
                    "runtime_verified": True,
                    "synopsis": parsed.synopsis,
                }
            ),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99,
            confidence_band=ConfidenceBand.STRONG,
            risk_level=RiskLevel.NONE,
            created_at=captured_at,
            updated_at=captured_at,
        ),
        StructuredCapability(
            capability_id=_stable_row_id(source.subject_id, "invocation", "usage"),
            subject_id=source.subject_id,
            capability_type="invocation",
            title=f"{' '.join(source.command)} invocation",
            detail=_validated_detail(
                {
                    "kind": "invocation",
                    "command": " ".join(source.command),
                    "source_url": source.source_url,
                    "usage_signature": parsed.usage_signature,
                    "synopsis": parsed.synopsis,
                    "argv_schema": {
                        "program": "gh",
                        "subcommand_path": list(source.command[1:]),
                        "positionals": list(parsed.positionals),
                    },
                    "flag_schema": [flag.to_json() for flag in parsed.flags],
                }
            ),
            verification_status=VerificationStatus.ACCEPTED,
            verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
            confidence=0.99,
            confidence_band=ConfidenceBand.STRONG,
            risk_level=_risk_level_for_subject(source.subject_id, parsed),
            created_at=captured_at,
            updated_at=captured_at,
        ),
    ]
    if parsed.aliases:
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(source.subject_id, "metadata", "aliases"),
                subject_id=source.subject_id,
                capability_type="metadata",
                title=f"{' '.join(source.command)} aliases",
                detail=_validated_detail(
                    {
                        "kind": "metadata",
                        "command": " ".join(source.command),
                        "source_url": source.source_url,
                        "aliases": list(parsed.aliases),
                    }
                ),
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.96,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=RiskLevel.NONE,
                created_at=captured_at,
                updated_at=captured_at,
            )
        )
    for flag in parsed.flags:
        capabilities.append(
            StructuredCapability(
                capability_id=_stable_row_id(source.subject_id, "configuration", flag.name),
                subject_id=source.subject_id,
                capability_type="configuration",
                title=f"{' '.join(source.command)} flag {flag.name}",
                detail=_validated_detail(
                    {
                        "kind": "configuration",
                        "command": " ".join(source.command),
                        "source_url": source.source_url,
                        "usage_signature": parsed.usage_signature,
                        "flag": flag.to_json(),
                    }
                ),
                verification_status=VerificationStatus.ACCEPTED,
                verification_level=VerificationLevel.L3_RUNTIME_VERIFIED,
                confidence=0.98,
                confidence_band=ConfidenceBand.STRONG,
                risk_level=_risk_level_for_flag(source.subject_id, flag),
                created_at=captured_at,
                updated_at=captured_at,
            )
        )
    constraints = _constraints_for_subject(source, parsed, captured_at)
    effects = _effects_for_subject(source, parsed, captured_at)
    return {
        "subject": subject,
        "capabilities": capabilities,
        "constraints": constraints,
        "effects": effects,
    }


def _parse_flags(lines: list[str], *, inherited: bool) -> list[ParsedFlag]:
    entries: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if not line.strip():
            continue
        if _FLAG_LINE.match(line):
            if current:
                entries.append(current)
            current = [line.strip()]
            continue
        if current:
            current.append(line.strip())
    if current:
        entries.append(current)
    parsed: list[ParsedFlag] = []
    for entry in entries:
        head = entry[0]
        desc_tail = " ".join(entry[1:]).strip()
        parts = re.split(r"\s{2,}", head, maxsplit=1)
        option_part = parts[0].strip()
        description = parts[1].strip() if len(parts) == 2 else ""
        if desc_tail:
            description = f"{description} {desc_tail}".strip()
        parsed.append(_parse_flag(option_part, description, inherited=inherited))
    return parsed


def _parse_flag(option_part: str, description: str, *, inherited: bool) -> ParsedFlag:
    match = re.match(
        r"^(?:(?P<short>-[A-Za-z0-9]),\s*)?(?P<long>--[A-Za-z0-9-]+)"
        r"(?:\s+(?P<value>.+))?$",
        option_part,
    )
    if not match:
        raise StructuredCliIngestionError(f"Could not parse flag signature: {option_part}")
    short = match.group("short")
    long_name = match.group("long")
    value_name = match.group("value")
    choices = _extract_choices(description)
    default = _extract_default(description)
    return ParsedFlag(
        name=long_name,
        short=short,
        takes_value=value_name is not None,
        value_name=value_name,
        value_type=_infer_value_type(value_name),
        repeatable=_is_repeatable(value_name, description),
        required=False,
        deprecated="deprecated" in description.casefold(),
        inherited=inherited,
        description=description or long_name,
        default=default,
        choices=choices,
    )


def _parse_positionals(command: tuple[str, ...], usage_signature: str) -> list[dict[str, Any]]:
    prefix = " ".join(command)
    remainder = usage_signature[len(prefix):].strip() if usage_signature.startswith(prefix) else ""
    remainder = remainder.replace("[flags]", "").strip()
    if not remainder:
        return []
    groups = re.findall(r"\[[^\]]+\]|<[^>]+>|\{[^}]+\}", remainder)
    positionals: list[dict[str, Any]] = []
    for group in groups:
        raw = group.strip()
        if "flags" in raw.casefold():
            continue
        required = not raw.startswith("[")
        cleaned = raw.strip("[]{}")
        variadic = "..." in cleaned
        cleaned = cleaned.replace("...", "").strip()
        choices = [part.strip(" <>") for part in cleaned.split("|")] if "|" in cleaned else []
        name = "|".join(choices) if choices else cleaned.strip("<>")
        positionals.append(
            {
                "name": name or raw,
                "raw": raw,
                "required": required,
                "variadic": variadic,
                "choices": [choice for choice in choices if choice],
            }
        )
    return positionals


def _constraints_for_subject(
    source: GhCommandSource,
    parsed: ParsedHelp,
    captured_at: datetime,
) -> list[StructuredConstraint]:
    constraints = [
        StructuredConstraint(
            constraint_id=_stable_row_id(source.subject_id, "constraint", "environment-gh"),
            subject_id=source.subject_id,
            constraint_kind="environment",
            detail={
                "command": " ".join(source.command),
                "source_url": source.source_url,
                "requires_binary": "gh",
                "runtime_verified": True,
            },
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]
    for scope in parsed.auth_scopes:
        constraints.append(
            StructuredConstraint(
                constraint_id=_stable_row_id(source.subject_id, "constraint", f"auth-{scope}"),
                subject_id=source.subject_id,
                constraint_kind="auth_scope",
                detail={
                    "command": " ".join(source.command),
                    "source_url": source.source_url,
                    "scope": scope,
                },
                created_at=captured_at,
                updated_at=captured_at,
            )
        )
    for note in parsed.preconditions:
        constraints.append(
            StructuredConstraint(
                constraint_id=_stable_row_id(source.subject_id, "constraint", note),
                subject_id=source.subject_id,
                constraint_kind="precondition",
                detail={
                    "command": " ".join(source.command),
                    "source_url": source.source_url,
                    "requirement": note,
                },
                created_at=captured_at,
                updated_at=captured_at,
            )
        )
    return constraints


def _effects_for_subject(
    source: GhCommandSource,
    parsed: ParsedHelp,
    captured_at: datetime,
) -> list[StructuredEffect]:
    effect_kind, destructive, reversible, mutates_remote_state, may_cost_money, exposure = (
        _effect_profile(source.subject_id, parsed)
    )
    return [
        StructuredEffect(
            effect_id=_stable_row_id(source.subject_id, "effect", effect_kind),
            subject_id=source.subject_id,
            effect_kind=effect_kind,
            destructive=destructive,
            reversible=reversible,
            mutates_remote_state=mutates_remote_state,
            may_cost_money=may_cost_money,
            may_expose_secrets=exposure,
            detail={
                "command": " ".join(source.command),
                "source_url": source.source_url,
                "synopsis": parsed.synopsis,
                "classification_reason": _effect_reason(source.subject_id, parsed),
            },
            created_at=captured_at,
            updated_at=captured_at,
        )
    ]


# Trailing subcommand tokens that denote an irreversible, data-destroying
# operation. Token-driven so any newly ingested `gh ... delete` / `... remove`
# subcommand is classified correctly without a hand-maintained subject allowlist.
_DESTRUCTIVE_VERBS = frozenset(
    {"delete", "remove", "rm", "purge", "prune", "destroy", "revoke", "uninstall"}
)
# Destructive subjects whose trailing token is not itself a destructive verb.
_DESTRUCTIVE_SUBJECT_OVERRIDES = frozenset({"gh-repo-delete"})
# Subjects that change remote GitHub state without destroying data.
_MUTATING_TOKENS = (
    "repo-create",
    "repo-edit",
    "repo-fork",
    "repo-archive",
    "pr-create",
    "pr-merge",
    "pr-close",
    "pr-reopen",
    "pr-review",
    "issue-create",
    "issue-close",
    "issue-reopen",
    "issue-comment",
    "release-create",
    "workflow-run",
    "run-rerun",
    "run-cancel",
    "secret-set",
    "variable-set",
    "codespace-create",
    "gist-create",
    "api",
    "config-set",
    "alias-set",
    "alias-import",
    "extension-install",
)
# Mutations that are not trivially reversible (a follow-up command exists but the
# operation is not a no-op to undo).
_HARD_TO_REVERSE = frozenset(
    {
        "gh-pr-merge",
        "gh-pr-close",
        "gh-issue-close",
        "gh-run-cancel",
        "gh-secret-set",
        "gh-variable-set",
        "gh-workflow-run",
    }
)


def _is_destructive(subject_id: str) -> bool:
    leaf = subject_id.split("-")[-1]
    return leaf in _DESTRUCTIVE_VERBS or subject_id in _DESTRUCTIVE_SUBJECT_OVERRIDES


def _effect_profile(
    subject_id: str,
    parsed: ParsedHelp,
) -> tuple[str, bool, bool, bool, bool, bool]:
    text = parsed.raw_help.casefold()
    destructive = _is_destructive(subject_id)
    mutates_remote = destructive or any(
        token in subject_id for token in _MUTATING_TOKENS
    )
    may_cost_money = subject_id == "gh-codespace-create"
    may_expose_secrets = subject_id in {
        "gh-auth-login",
        "gh-auth-refresh",
        "gh-auth-token",
        "gh-secret-set",
    } or "--show-token" in text
    reversible = not destructive and subject_id not in _HARD_TO_REVERSE
    if destructive:
        return ("destructive", True, False, True, may_cost_money, may_expose_secrets)
    if may_expose_secrets:
        return ("secret_exposure", False, reversible, mutates_remote, may_cost_money, True)
    if may_cost_money:
        return ("cost", False, reversible, mutates_remote, True, may_expose_secrets)
    if mutates_remote:
        return ("mutation", False, reversible, mutates_remote, may_cost_money, may_expose_secrets)
    return ("network", False, True, False, False, may_expose_secrets)


def _effect_reason(subject_id: str, parsed: ParsedHelp) -> str:
    command = " ".join(parsed.command)
    leaf = subject_id.split("-")[-1]
    if _is_destructive(subject_id):
        return (
            f"`{command}` performs a destructive {leaf} operation; the change is "
            "irreversible and mutates remote state."
        )
    if subject_id == "gh-codespace-create":
        return "Creates a remote Codespace, which may consume billable resources."
    if subject_id in {"gh-auth-token", "gh-secret-set"} or "--show-token" in parsed.raw_help.casefold():
        return f"`{command}` handles sensitive token or secret material that may be exposed."
    if subject_id in _HARD_TO_REVERSE:
        return f"`{command}` changes remote GitHub state in a way that is not trivially reversible."
    if any(token in subject_id for token in _MUTATING_TOKENS):
        return f"`{command}` mutates remote GitHub state."
    if "authorization with the" in parsed.raw_help.casefold():
        return f"`{command}` is a remote action gated by GitHub authorization."
    return f"`{command}` reads or executes against GitHub without destroying data."


def _extract_auth_scopes(text: str) -> list[str]:
    scopes = {match.group(1) for match in _AUTH_SCOPE.finditer(text)}
    scopes.update(match.group(1) for match in _AUTH_REFRESH.finditer(text))
    sentence = _MIN_SCOPE_SENTENCE.search(text)
    if sentence:
        raw = sentence.group(1)
        scopes.update(item.strip(" `") for item in re.split(r",| and ", raw) if item.strip())
    return sorted(scope for scope in scopes if scope)


def _extract_preconditions(text: str) -> list[str]:
    requirements: list[str] = []
    if "must support an `on.workflow_dispatch` trigger" in text:
        requirements.append("workflow must define on.workflow_dispatch")
    if "current branch isn't fully pushed to a git remote" in text:
        requirements.append("current branch should be pushed or --head specified")
    return requirements


def _extract_choices(description: str) -> list[str]:
    match = _CHOICES.search(description)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split("|") if item.strip()]


def _extract_default(description: str) -> str | int | bool | None:
    match = _DEFAULT.search(description)
    if not match:
        return None
    raw = match.group(1).strip().strip('"')
    if raw.isdigit():
        return int(raw)
    if raw.casefold() in {"true", "false"}:
        return raw.casefold() == "true"
    return raw


def _infer_value_type(value_name: str | None) -> str:
    if value_name is None:
        return "boolean"
    normalized = value_name.strip().casefold()
    if normalized == "int":
        return "integer"
    if normalized == "duration":
        return "duration"
    if normalized in {"file", "files"}:
        return "file"
    if "=" in normalized:
        return "key_value"
    if normalized in {"strings", "topics"}:
        return "string_list"
    if normalized in {"string", "login", "branch", "expression", "format"}:
        return "string"
    if any(marker in normalized for marker in ("owner/repo", "<", ">", "/", "sha", "ref")):
        return "token"
    return "string"


def _is_repeatable(value_name: str | None, description: str) -> bool:
    if value_name is None:
        return False
    normalized = value_name.strip().casefold()
    return normalized.endswith("s") or "multiple" in description.casefold()


def _validated_detail(detail: dict[str, Any]) -> dict[str, Any]:
    try:
        structured_capability_validator().validate(detail)
    except ValidationError as exc:
        raise StructuredCliIngestionError(f"Structured capability detail failed validation: {exc}") from exc
    return detail


@cache
def structured_capability_validator() -> Draft202012Validator:
    schema = json.loads(contract_path("structured_capability.v1.json").read_text())
    return Draft202012Validator(schema)


def _subject_id_for_command(command: tuple[str, ...]) -> str:
    if command == ("gh",):
        return "gh"
    return "-".join(command)


def _command_from_gh_manual_url(url: str) -> tuple[str, ...]:
    path = Path(urlparse(url).path)
    slug = path.name
    if slug == "gh":
        return ("gh",)
    if not slug.startswith("gh_"):
        raise StructuredCliIngestionError(f"Unsupported gh manual URL: {url}")
    return tuple(["gh", *slug.removeprefix("gh_").split("_")])


def _stable_row_id(subject_id: str, row_kind: str, discriminator: str) -> str:
    digest = sha256(f"{subject_id}|{row_kind}|{discriminator}".encode("utf-8")).hexdigest()[:16]
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{subject_id}-{row_kind}-{discriminator}".lower())
    base = re.sub(r"-{2,}", "-", base).strip("-")
    trimmed = base[:110].rstrip("-")
    return f"{trimmed}-{digest}"


def _risk_level_for_subject(subject_id: str, parsed: ParsedHelp) -> RiskLevel:
    effect_kind, destructive, _, _, may_cost_money, exposure = _effect_profile(subject_id, parsed)
    if destructive:
        return RiskLevel.CRITICAL
    if may_cost_money or exposure or effect_kind == "mutation":
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _risk_level_for_flag(subject_id: str, flag: ParsedFlag) -> RiskLevel:
    if _is_destructive(subject_id) and flag.name in {"--yes", "--confirm"}:
        return RiskLevel.CRITICAL
    if flag.name in {"--with-token", "--show-token", "--insecure-storage"}:
        return RiskLevel.HIGH
    if flag.name in {"--dry-run", "--web", "--editor"}:
        return RiskLevel.LOW
    return RiskLevel.NONE


def _first_non_empty(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _assert_safe_gh_help_argv(argv: tuple[str, ...]) -> None:
    if not argv or argv[0] != "gh" or argv[-1] != "--help":
        raise StructuredCliIngestionError("Structured gh ingestion only allows `gh ... --help`.")
    for token in argv:
        if any(forbidden in token for forbidden in _FORBIDDEN_TOKENS):
            raise StructuredCliIngestionError(f"Unsafe token in argv: {token!r}")
