# ayiru-client

Official Python client for [Ayiru](https://github.com/ruth411/ayiru) —
the verified, machine-readable knowledge layer for AI agents.

Agents using this client can ask natural-language questions about CLIs,
APIs, and developer tools and get **cited, verification-graded answers
from a curated knowledge graph** instead of paying the token cost of a
web search round-trip on every question.

## Install

```bash
pip install ayiru-client
```

Requires Python 3.10+. Depends on `httpx` and `pydantic v2`.

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

| method | purpose |
|---|---|
| `ask(question, *, limit=5, tool_id_hint=None)` | Natural-language query against the verified graph. |
| `validate_command(*, tool_id, command)` | Lookup the verdict + risk classification for a specific command. |
| `get_tool_spec(tool_id)` | Fetch the canonical ToolSpec for a tool. |
| `search_tools(query="", *, limit=100, offset=0)` | List or filter tools known to the graph. |
| `savings(window="all")` | Aggregate cost savings (queries served, tokens saved, USD saved). |

### `Answer.is_useful`

Quick heuristic on each answer: `True` when `confidence ≥ 0.6 AND
verification_level != "L0_UNVERIFIED"`. The threshold is intentionally
loose — agent code typically reads it directly:

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
cd ayiru/clients/python
pip install -e .[dev]
pytest
```

Tests use `httpx.ASGITransport` to drive the real FastAPI backend
in-process — no sockets, no network. The backend test suite is the
canonical correctness contract; the SDK tests verify that the wire
format hasn't drifted.

## License

Apache-2.0 — same as the Ayiru server.
