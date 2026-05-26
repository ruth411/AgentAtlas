# ayiru-client

Official Python client for [Ayiru](https://github.com/ruth411/ayiru) —
the verified, machine-readable knowledge layer for AI agents.

Agents using this client can ask natural-language questions about CLIs,
APIs, and developer tools and get **cited, verification-graded answers
from a curated knowledge graph** instead of paying the token cost of a
web search round-trip on every question.

## Install

PyPI publication ships with v0.2.5. Until then, install from a checkout:

```bash
git clone https://github.com/ruth411/ayiru.git
cd ayiru
pip install -e clients/python                     # core SDK
pip install -e 'clients/python[langchain]'        # + LangChain adapter
```

Requires Python 3.10+. Depends on `httpx` and `pydantic v2`. The
`langchain` extra adds `langchain-core>=0.2`.

Once 0.2.5 publishes the `pip install ayiru-client` path will work too.

## Quickstart

The client has two flavors with the same public surface — pick the one
that matches your codebase.

### Sync

```python
from ayiru_client import Ayiru

with Ayiru(base_url="http://localhost:8000") as client:
    answer = client.ask("how do I remove a docker volume")
    if answer.is_useful:
        print(answer.top.statement)
        # → "docker volume rm <volume> removes a local volume."
    else:
        # Miss — fall through to web_search.
        ...
```

### Async

```python
import asyncio
from ayiru_client import AsyncAyiru

async def main():
    async with AsyncAyiru(base_url="http://localhost:8000") as client:
        answer = await client.ask("how do I remove a docker volume")
        if answer.is_useful:
            print(answer.top.statement)

asyncio.run(main())
```

## Running the Ayiru server locally

The SDK talks to a running Ayiru backend. From the main repo:

```bash
cd backend
ayiru migrate
ayiru seed
ayiru serve  # http://localhost:8000
```

See the top-level README for full server setup including the bulk-ingest
graph (Stage 20).

## API

All methods are available on both `Ayiru` and `AsyncAyiru`; the async
versions are coroutines, otherwise identical.

### `ask(question, *, limit=5, tool_id_hint=None) -> AskResponse`

Natural-language query against the verified graph. Returns an
`AskResponse` with up to `limit` ranked `Answer`s. Pass `tool_id_hint`
when you already know which tool the question is about (e.g.
`"docker"`, `"kubectl"`) to narrow the search.

```python
resp = client.ask("how do I remove a docker volume")
# resp.answers: list[Answer]
# resp.fallback_recommended: bool  → True ⇒ agent should escalate
# resp.estimated_tokens_saved: int → heuristic token-savings credit
# resp.top: Answer | None          → convenience for resp.answers[0]
# resp.is_useful: bool             → resp.top.is_useful and not fallback
```

### `validate_command(*, tool_id, command) -> ValidateCommandResponse`

Lookup the verdict + risk classification for a specific command. Used
when an agent is about to *execute* something and needs a go/no-go
decision before the side-effect.

```python
v = client.validate_command(tool_id="github-cli", command="gh repo delete x")
# v.safe_to_auto_execute: bool
# v.requires_human_confirmation: bool
# v.risk_level: "low" | "medium" | "high" | "critical" | None
# v.reasons: list[str]            → human-readable rationale
# v.evidence: list[EvidenceCitation]
```

### `get_tool_spec(tool_id) -> ToolSpec` (dict)

Fetch the canonical ToolSpec — capability catalog, verified commands,
risk profile. Raises `AyiruError(status_code=404,
code="CANONICAL_SPEC_NOT_FOUND")` when no spec has been published for
`tool_id` (the v0.2 graph has published specs for 5 tools — `docker`,
`git`, `github-cli`, `vercel-cli`, plus `openai-api`'s pending claims).

### `search_tools(query="", *, limit=100, offset=0) -> SearchToolsResponse`

List tools known to the graph, optionally filtered by a substring
match. Useful for discovery (e.g. "what does Ayiru know about?").

```python
resp = client.search_tools("docker")
# resp.matches: list[ToolMatchSummary]
# resp.total: int  → pre-pagination total
```

### `savings(window="all") -> SavingsResponse`

Aggregate cost savings over the `QUERY_SERVED` audit stream. Pass one
of `"24h"`, `"7d"`, `"30d"`, `"all"`.

```python
s = client.savings("7d")
# s.total_queries_served: int
# s.total_tokens_saved: int
# s.estimated_usd_saved: float
# s.fallback_count: int            → hit rate = 1 - fallback_count / total
# s.by_top_claim: dict[str, int]   → per-claim hit counts ("__fallback__" key = misses)
```

### `Answer.is_useful`

Quick heuristic on each answer: `True` when `confidence ≥ 0.6 AND
verification_level != "L0_unverified"`.

> **Wire-format note.** `verification_level` is a string with an
> uppercase-prefix + lowercase-suffix shape: `"L0_unverified"`,
> `"L1_schema_valid"`, `"L2_source_verified"`, `"L3_runtime_verified"`,
> `"L4_cross_agent_verified"`, `"L5_human_audited"`. Pattern-match
> defensively if you string-compare.

The threshold is intentionally loose — agent code typically reads it
directly:

```python
answer = client.ask(question)
if answer.is_useful:
    return answer.top.statement  # use Ayiru
else:
    return web_search(question)  # escalate
```

Callers needing a tighter bar can inspect `confidence` and
`verification_level` on each `Answer` directly.

### Errors

Every server-side 4xx/5xx is wrapped in `AyiruError`:

```python
from ayiru_client import Ayiru, AyiruError

with Ayiru() as client:
    try:
        spec = client.get_tool_spec("docker")
    except AyiruError as exc:
        if exc.status_code == 404 and exc.code == "CANONICAL_SPEC_NOT_FOUND":
            # No canonical spec yet — fall back gracefully.
            ...
        else:
            raise
```

The `code`, `message`, and `details` fields mirror the server's
structured error envelope, so caller code can branch on the stable code
without parsing strings.

## LangChain integration

A drop-in `BaseTool` so a LangChain agent can call Ayiru with zero glue
code:

```bash
pip install 'ayiru-client[langchain]'
```

```python
from ayiru_client import Ayiru
from ayiru_client.langchain import AyiruTool

client = Ayiru(base_url="http://localhost:8000")
tool = AyiruTool(client=client)

# In an agent:
#     agent = create_react_agent(llm, tools=[tool])
# Or call directly:
result = tool.invoke({"question": "how do I remove a docker volume"})
```

The tool's `description` field is deliberately tuned to defeat the
default LLM meta-policy ("only use tools when uncertain") — without
that override, agents skip `ask` for stable technical questions and
the savings story collapses. See
[`ayiru_client/langchain.py`](ayiru_client/langchain.py) for the
exact wording and the Stage 17.4 / 22.1 reasoning.

A runnable 10-question demo is at
[`examples/langchain_demo.ipynb`](examples/langchain_demo.ipynb) — it
ends with a `/v1/stats/savings`-derived footer showing the token-savings
tally for the run.

### What "useful" means

`ask()` returns three semantically distinct outcomes that agent code
should handle separately:

| outcome | how to detect | what the agent should do |
|---|---|---|
| **Useful** | `top = response.answers[0]; top.is_useful` (confidence ≥ 0.6 AND verification_level != `L0_unverified`) | Return the answer verbatim with the citation. |
| **Weak match** | `response.fallback_recommended is False` *but* `top.is_useful is False` | Escalate to `web_search`. The graph saw a lexical match but with too little confidence to trust. |
| **Miss** | `response.fallback_recommended is True` | Escalate to `web_search`. |

The v0.2 graph has **5 tools curated to depth** plus 35 with one thin
index-page claim each, so weak matches are common on the bulk-ingest
tools today. See the main repo README's *Coverage today* section for
the per-tool reality.

## Auth

The Ayiru server exposes read endpoints (everything the SDK calls except
write operations) without auth. When the server has `AYIRU_API_KEY` set
and you need to call a write endpoint, pass the same key on the client:

```python
client = Ayiru(base_url="https://ayiru.example.com", api_key="...")
```

The key is sent as `Authorization: Bearer <key>`.

## Development

```bash
git clone https://github.com/ruth411/ayiru
cd ayiru
pip install -e 'backend[dev]'        # SDK tests need a live backend
pip install -e 'clients/python[dev]'
cd clients/python && pytest
```

The SDK itself runs on Python 3.10+, but **the SDK test suite requires
Python 3.12+** because the tests import the backend package (which
pins to 3.12+). Production users only need 3.10+.

Tests stand up a real uvicorn server on a free localhost port and
drive both `Ayiru` and `AsyncAyiru` against it — closest possible
mirror of production use. The backend test suite is the canonical
correctness contract; the SDK tests verify that the wire format
hasn't drifted.

## License

Apache-2.0 — same as the Ayiru server.
