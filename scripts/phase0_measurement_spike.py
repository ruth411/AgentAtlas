"""Phase 0.1 — Agent measurement spike.

This script answers the single question the entire v0.2.1 pivot rests on:
**What fraction of an AI agent's token cost is web-search-replaceable
lookups, and will the LLM actually pick a local `ask` tool over `web_search`
when both are listed?**

The script runs one agent harness through 10 realistic dev tasks twice:

- **Baseline run**: only `web_search` available. Every lookup costs real
  search tokens. Measures total tokens + how many turns invoked search.
- **Intervention run**: both `web_search` and a fake `ask` tool (hardcoded
  Python dict). Measures how often the LLM picks `ask` and how many
  tokens the run saves.

Outputs two numbers — *web-search-replaceable token fraction* and
*tool-choice rate for ask* — plus a qualitative log of how the agent
behaved. Compares them to the Decision Gate thresholds in roadmap_v0.2.md
(≥ 25% and ≥ 50% respectively).

USAGE
-----
    # Anthropic (preferred — the Claude Agent SDK is the target audience):
    export ANTHROPIC_API_KEY=sk-ant-...
    backend/.venv/bin/python scripts/phase0_measurement_spike.py --provider anthropic

    # OR OpenAI:
    export OPENAI_API_KEY=sk-...
    backend/.venv/bin/python scripts/phase0_measurement_spike.py --provider openai

    # Dry-run (no LLM calls, validates the script structure only):
    backend/.venv/bin/python scripts/phase0_measurement_spike.py --dry-run

DEPENDENCIES
------------
    pip install langchain langchain-anthropic langchain-openai \\
                langchain-community duckduckgo-search

The script uses LangChain because it's the largest agent-developer audience
and the tool-choice patterns translate to other frameworks. If you switch
to the Claude Agent SDK or AutoGen later, the metrics still apply.

INTERPRETATION
--------------
The Decision Gate (roadmap_v0.2.md):

- **Web-search-replaceable token fraction ≥ 25%** AND
- **Tool-choice rate for `ask` ≥ 50%**

→ ship A1 next Monday. The pivot is real.

Either below threshold → re-read the roadmap's Decision Gate section.
The pivot is wrong and you just saved yourself 5 weeks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any


# -------- The 10 realistic dev tasks --------
#
# Each task is something a real agent might do for a real developer.
# Tasks are deliberately tool-heavy (CLI usage, infra) because that's
# where Ayiru claims to save tokens. If you pick tasks that don't
# benefit from a local knowledge layer (e.g. "write me a poem"), the
# spike will measure noise instead of signal.

DEV_TASKS: list[str] = [
    "How do I delete a GitHub repository using the gh CLI without going through the web UI?",
    "Show me the docker compose syntax for mounting a host directory into a container at /app.",
    "What's the kubectl command to scale a deployment named 'api' to 5 replicas?",
    "How do I create a read-only Postgres user that can only SELECT from one schema?",
    "Write the terraform block for an AWS S3 bucket with versioning enabled and public access blocked.",
    "How do I cancel a stuck git rebase and return the repo to its pre-rebase state?",
    "Generate the systemd unit file for a Python script that should restart on failure.",
    "What's the curl command to send a POST with a JSON body and an Authorization Bearer token?",
    "How do I find the top 10 largest files in a directory tree using only standard Unix tools?",
    "Show me the npm command to install a specific package version as a dev dependency.",
]


# -------- The fake `ask` registry --------
#
# Hardcoded answers for the queries the agent will most likely ask in
# response to the DEV_TASKS above. This is what Ayiru would serve
# from a real claim graph; here we cheat with a dict so we can isolate
# the tool-choice signal from the retrieval quality signal.

FAKE_REGISTRY: dict[str, str] = {
    "gh repo delete": (
        "`gh repo delete <OWNER>/<REPO> --yes` deletes the repository "
        "permanently. This is destructive and irreversible. Requires "
        "`delete_repo` scope on your token. Source: docs.github.com."
    ),
    "docker compose volume mount": (
        "In docker-compose.yml under services.<name>.volumes, use the "
        "syntax `- /host/path:/app`. The host path must exist. Source: "
        "docs.docker.com/compose."
    ),
    "kubectl scale deployment": (
        "`kubectl scale deployment/<NAME> --replicas=<N>` scales the "
        "named deployment to N replicas. Add `-n <namespace>` if not "
        "in the default namespace. Source: kubernetes.io/docs."
    ),
    "postgres read only user": (
        "CREATE USER readonly WITH PASSWORD '...'; "
        "GRANT USAGE ON SCHEMA <name> TO readonly; "
        "GRANT SELECT ON ALL TABLES IN SCHEMA <name> TO readonly; "
        "ALTER DEFAULT PRIVILEGES IN SCHEMA <name> GRANT SELECT ON "
        "TABLES TO readonly; Source: postgresql.org/docs."
    ),
    "terraform s3 bucket versioning": (
        "resource \"aws_s3_bucket\" \"this\" { bucket = \"...\" } "
        "resource \"aws_s3_bucket_versioning\" \"this\" { "
        "bucket = aws_s3_bucket.this.id; "
        "versioning_configuration { status = \"Enabled\" } } "
        "resource \"aws_s3_bucket_public_access_block\" \"this\" { "
        "block_public_acls = true; block_public_policy = true; "
        "ignore_public_acls = true; restrict_public_buckets = true } "
        "Source: registry.terraform.io/providers/hashicorp/aws."
    ),
    "git cancel rebase": (
        "`git rebase --abort` cancels an in-progress rebase and returns "
        "HEAD to where it was before the rebase started. Safe to run "
        "any time during a rebase. Source: git-scm.com/docs/git-rebase."
    ),
    "systemd unit restart on failure": (
        "[Unit]\nDescription=...\n\n[Service]\nExecStart=/usr/bin/python3 "
        "/path/to/script.py\nRestart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=multi-user.target\nSource: freedesktop.org/"
        "software/systemd/man/systemd.service.html."
    ),
    "curl post json bearer": (
        "curl -X POST <URL> -H 'Content-Type: application/json' "
        "-H 'Authorization: Bearer <TOKEN>' -d '{\"key\":\"value\"}'. "
        "Source: curl.se/docs."
    ),
    "find largest files unix": (
        "`find . -type f -exec du -h {} + | sort -rh | head -n 10` lists "
        "the 10 largest files in the current tree by size. Works on "
        "macOS and Linux. Source: GNU coreutils manual."
    ),
    "npm install dev dependency version": (
        "`npm install <package>@<version> --save-dev` installs a specific "
        "version into devDependencies. Use `--save-exact` to pin without "
        "a caret. Source: docs.npmjs.com/cli."
    ),
}


@dataclass
class RunMetrics:
    """One run's measurements."""

    mode: str  # "baseline" or "intervention"
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    web_search_calls: int = 0
    ask_calls: int = 0
    ask_hits: int = 0  # ask was called AND returned a non-empty answer
    task_completions: int = 0  # tasks that finished without error
    elapsed_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens(),
            "web_search_calls": self.web_search_calls,
            "ask_calls": self.ask_calls,
            "ask_hits": self.ask_hits,
            "task_completions": self.task_completions,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }


def _lookup_ask_response(query: str) -> str:
    """Crude keyword match against FAKE_REGISTRY — good enough to
    surface which queries land vs. miss. A real Ayiru would do
    lexical + semantic ranking; this just proxies whether *any* answer
    exists in the registry."""
    needle = query.lower()
    best_key = None
    best_overlap = 0
    for key in FAKE_REGISTRY:
        key_tokens = set(key.lower().split())
        overlap = sum(1 for tok in key_tokens if tok in needle)
        if overlap > best_overlap:
            best_overlap = overlap
            best_key = key
    # Require at least 2 overlapping tokens to count as a hit.
    if best_key is not None and best_overlap >= 2:
        return FAKE_REGISTRY[best_key]
    return ""


def _run_with_langchain(
    provider: str,
    mode: str,
    tasks: list[str],
    verbose: bool,
) -> RunMetrics:
    """Execute one mode of the spike using LangChain.

    `mode == "baseline"` exposes only `web_search`.
    `mode == "intervention"` exposes `web_search` + `ask`.
    """
    # Heavy imports are scoped to the function so `--dry-run` works
    # without the langchain stack installed.
    from langchain_core.tools import Tool
    from langchain_community.tools import DuckDuckGoSearchRun

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if "ANTHROPIC_API_KEY" not in os.environ:
            raise SystemExit("ANTHROPIC_API_KEY not set.")
        llm = ChatAnthropic(model="claude-sonnet-4-5", max_tokens=1024)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        if "OPENAI_API_KEY" not in os.environ:
            raise SystemExit("OPENAI_API_KEY not set.")
        llm = ChatOpenAI(model="gpt-4o-mini", max_tokens=1024)
    else:
        raise SystemExit(f"Unknown provider: {provider}")

    metrics = RunMetrics(mode=mode)

    # Counter closures so the tool wrappers can mutate the metrics.
    def _web_search_wrapper(query: str) -> str:
        metrics.web_search_calls += 1
        try:
            return DuckDuckGoSearchRun().run(query)[:600]
        except Exception as exc:
            return f"(web_search failed: {exc})"

    def _ask_wrapper(query: str) -> str:
        metrics.ask_calls += 1
        answer = _lookup_ask_response(query)
        if answer:
            metrics.ask_hits += 1
            return answer
        return "(no answer in registry; fall back to web_search)"

    web_tool = Tool(
        name="web_search",
        func=_web_search_wrapper,
        description=(
            "Search the public internet for general information. Use as a "
            "last resort — searches are slow (~3s) and expensive in tokens."
        ),
    )
    tools = [web_tool]
    if mode == "intervention":
        ask_tool = Tool(
            name="ask",
            func=_ask_wrapper,
            description=(
                "Ask a local, verified knowledge graph for dev / CLI / API "
                "questions. Returns cited answers in milliseconds and saves "
                "~800 tokens per hit vs web_search. Prefer this tool first "
                "for any question about: git, gh, docker, kubectl, terraform, "
                "postgres, curl, npm, systemd, or common shell utilities. "
                "Falls back to web_search when no answer is found locally."
            ),
        )
        tools.append(ask_tool)

    # We use the simplest possible agent loop — one ReAct-style turn per
    # task. A more elaborate planner would surface a different number,
    # and that's actually fine: the spike measures the LLM's *initial*
    # tool preference, which is what matters for v0.2's pitch.
    from langchain.agents import AgentExecutor, create_react_agent
    from langchain_core.prompts import PromptTemplate

    prompt = PromptTemplate.from_template(
        "You are a dev-ops assistant. Answer the user's question concisely.\n"
        "\n"
        "You have access to these tools:\n"
        "{tools}\n"
        "\n"
        "Use the following format:\n"
        "\n"
        "Question: the input question\n"
        "Thought: think about which tool to use\n"
        "Action: the tool name, one of [{tool_names}]\n"
        "Action Input: the input to the tool\n"
        "Observation: the tool result\n"
        "... (Thought/Action/Action Input/Observation can repeat)\n"
        "Thought: I now know the answer\n"
        "Final Answer: <concise answer to the user>\n"
        "\n"
        "Question: {input}\n"
        "Thought:{agent_scratchpad}"
    )
    agent = create_react_agent(llm, tools, prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=verbose,
        max_iterations=4,
        handle_parsing_errors=True,
        return_intermediate_steps=False,
    )

    started = time.perf_counter()
    for index, task in enumerate(tasks, start=1):
        if verbose:
            print(f"\n--- {mode} task {index}/{len(tasks)} ---")
            print(f"Q: {task}")
        try:
            result = executor.invoke({"input": task})
            metrics.task_completions += 1
            if verbose:
                print(f"A: {result.get('output', '(empty)')[:200]}")
        except Exception as exc:
            metrics.notes.append(f"task {index} failed: {exc}")
            if verbose:
                print(f"FAILED: {exc}")
        # LangChain doesn't expose per-call token usage uniformly across
        # providers, so we approximate via response_metadata when present.
        # For Anthropic, llm.invoke() returns a dict with usage in
        # response_metadata; we capture via a callback below in a future
        # revision. For v0 we record a placeholder and rely on the
        # web_search_calls / ask_calls deltas as the primary signal.
    metrics.elapsed_seconds = time.perf_counter() - started

    return metrics


def _dry_run() -> None:
    """Validate the script structure without hitting any LLM."""
    print("Dry run — validating registry coverage of dev tasks:\n")
    hits = 0
    for index, task in enumerate(DEV_TASKS, start=1):
        answer = _lookup_ask_response(task)
        status = "HIT" if answer else "MISS"
        if answer:
            hits += 1
        print(f"  task {index:2d}: {status}  {task[:60]}...")
    print(f"\nRegistry coverage: {hits}/{len(DEV_TASKS)} tasks have a hardcoded answer.")
    print("\nIf coverage is < 7/10, edit FAKE_REGISTRY before running for real.")
    print("If a real run goes ahead with low coverage, the intervention metric will")
    print("understate the pivot's value.")


def _report(baseline: RunMetrics, intervention: RunMetrics) -> dict[str, Any]:
    """Compute Decision Gate metrics and write the verdict."""
    # Metric 1: web-search-replaceable token fraction.
    #
    # We approximate "replaceable tokens" as the tokens that *would* have
    # been spent on web_search in the baseline run but were intercepted
    # by `ask` in the intervention run. Each replaced search saves
    # ~830 input+output tokens (per roadmap A2).
    AVG_TOKENS_PER_SEARCH = 830
    replaced_searches = max(0, baseline.web_search_calls - intervention.web_search_calls)
    estimated_tokens_saved = replaced_searches * AVG_TOKENS_PER_SEARCH
    # Token cost of the baseline run is approximated from web_search_calls
    # plus a constant per-task overhead (system prompt, reasoning steps).
    # This is a coarse estimate; the goal here is order-of-magnitude.
    PER_TASK_OVERHEAD = 800
    baseline_estimated_tokens = (
        baseline.web_search_calls * AVG_TOKENS_PER_SEARCH
        + len(DEV_TASKS) * PER_TASK_OVERHEAD
    )
    if baseline_estimated_tokens > 0:
        replaceable_fraction = estimated_tokens_saved / baseline_estimated_tokens
    else:
        replaceable_fraction = 0.0

    # Metric 2: tool-choice rate for `ask`.
    total_tool_calls = intervention.ask_calls + intervention.web_search_calls
    if total_tool_calls > 0:
        tool_choice_rate = intervention.ask_calls / total_tool_calls
    else:
        tool_choice_rate = 0.0

    # Decision Gate thresholds — from roadmap_v0.2.md.
    GATE_FRACTION = 0.25
    GATE_TOOL_CHOICE = 0.50

    gate_1_passed = replaceable_fraction >= GATE_FRACTION
    gate_2_passed = tool_choice_rate >= GATE_TOOL_CHOICE

    verdict = {
        "baseline": baseline.summary(),
        "intervention": intervention.summary(),
        "metrics": {
            "estimated_replaceable_fraction": round(replaceable_fraction, 3),
            "estimated_tokens_saved": estimated_tokens_saved,
            "tool_choice_rate_for_ask": round(tool_choice_rate, 3),
            "ask_hit_rate": (
                round(intervention.ask_hits / intervention.ask_calls, 3)
                if intervention.ask_calls > 0
                else 0.0
            ),
        },
        "gate": {
            "fraction_threshold": GATE_FRACTION,
            "fraction_passed": gate_1_passed,
            "tool_choice_threshold": GATE_TOOL_CHOICE,
            "tool_choice_passed": gate_2_passed,
            "verdict": (
                "PROCEED to Phase A"
                if (gate_1_passed and gate_2_passed)
                else "STOP — Decision Gate failed; re-read roadmap_v0.2.md"
            ),
        },
        "notes": baseline.notes + intervention.notes,
    }
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="Which LLM provider to drive the agent (default: anthropic).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate registry coverage and script structure without LLM calls.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every task's question + answer + tool calls.",
    )
    parser.add_argument(
        "--output",
        default="phase0_results.json",
        help="Where to write the JSON verdict (default: phase0_results.json).",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        _dry_run()
        return 0

    print(f"Phase 0.1 measurement spike — provider={args.provider}")
    print(f"Running {len(DEV_TASKS)} dev tasks twice (baseline + intervention).\n")

    print("=== BASELINE: web_search only ===")
    baseline = _run_with_langchain(
        args.provider, mode="baseline", tasks=DEV_TASKS, verbose=args.verbose
    )
    print(f"  web_search calls: {baseline.web_search_calls}")
    print(f"  task completions: {baseline.task_completions}/{len(DEV_TASKS)}")
    print(f"  elapsed: {baseline.elapsed_seconds:.1f}s")

    print("\n=== INTERVENTION: web_search + fake ask ===")
    intervention = _run_with_langchain(
        args.provider, mode="intervention", tasks=DEV_TASKS, verbose=args.verbose
    )
    print(f"  ask calls:        {intervention.ask_calls}")
    print(f"  ask hits:         {intervention.ask_hits}")
    print(f"  web_search calls: {intervention.web_search_calls}")
    print(f"  task completions: {intervention.task_completions}/{len(DEV_TASKS)}")
    print(f"  elapsed: {intervention.elapsed_seconds:.1f}s")

    verdict = _report(baseline, intervention)
    with open(args.output, "w") as f:
        json.dump(verdict, f, indent=2)
    print(f"\nResults written to {args.output}")

    print("\n=== DECISION GATE ===")
    m = verdict["metrics"]
    g = verdict["gate"]
    print(
        f"  Metric 1 — replaceable token fraction: "
        f"{m['estimated_replaceable_fraction']:.1%}  "
        f"(threshold {g['fraction_threshold']:.0%})  "
        f"{'PASS' if g['fraction_passed'] else 'FAIL'}"
    )
    print(
        f"  Metric 2 — tool-choice rate for ask:   "
        f"{m['tool_choice_rate_for_ask']:.1%}  "
        f"(threshold {g['tool_choice_threshold']:.0%})  "
        f"{'PASS' if g['tool_choice_passed'] else 'FAIL'}"
    )
    print(f"\n  >>> {g['verdict']} <<<\n")

    return 0 if g["fraction_passed"] and g["tool_choice_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
