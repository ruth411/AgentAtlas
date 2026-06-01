"""Direct-insert openai-api error + recipe claims."""

from datetime import datetime, timezone
from hashlib import sha256

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import get_claim_store
from app.services.embedding_service import (
    EmbeddingService,
    claim_embedding_text,
    serialize_embedding,
)
from app.services.orchestrator import CanonOrchestrator

BASE = "https://platform.openai.com/docs"

CLAIMS = [
    # ============================================================
    # openai-errors — 30 common errors
    # ============================================================
    (
        "openai-errors", "openai error: 401 Incorrect API key provided",
        "Your API key is missing, malformed, or revoked. "
        "Fix: (1) verify env var: `echo $OPENAI_API_KEY` should start with `sk-`; "
        "(2) check key on https://platform.openai.com/api-keys — may have been revoked; "
        "(3) ensure no surrounding whitespace: `OPENAI_API_KEY=$(echo $OPENAI_API_KEY | tr -d '[:space:]')`; "
        "(4) project keys (sk-proj-): scoped to one project — verify project ID matches; "
        "(5) for org-restricted keys: pass `OpenAI-Organization` header matching the key's org.",
        f"{BASE}/guides/error-codes",
    ),
    (
        "openai-errors", "openai error: 401 No API key provided",
        "Request sent without Authorization header. "
        "Fix: (1) SDK auto-picks `OPENAI_API_KEY` env var — confirm it's exported, not just set in a sub-shell; "
        "(2) for curl: `-H \"Authorization: Bearer $OPENAI_API_KEY\"`; "
        "(3) for explicit client: `OpenAI(api_key=\"sk-...\")` (Python) or `new OpenAI({apiKey: \"sk-...\"})` (Node); "
        "(4) NEVER hardcode keys in code — use env vars or secret manager.",
        f"{BASE}/guides/error-codes",
    ),
    (
        "openai-errors", "openai error: 429 You exceeded your current quota (insufficient_quota)",
        "Account billing problem — out of credits OR payment method failed. "
        "Fix: (1) check usage + balance on https://platform.openai.com/usage and /settings/organization/billing/overview; "
        "(2) add credits (prepaid) or update payment method; "
        "(3) DIFFERENT from rate limit (429 + 'rate_limit_exceeded') — this one needs money, not a wait; "
        "(4) for new orgs: tier 1 limits apply — usage history unlocks higher tiers; "
        "(5) for enterprise: contact sales for committed-use discounts.",
        f"{BASE}/guides/error-codes",
    ),
    (
        "openai-errors", "openai error: 429 Rate limit reached for requests (RPM)",
        "Hit per-minute request count limit. "
        "Fix: (1) check your tier limits: https://platform.openai.com/settings/organization/limits; "
        "(2) implement exponential backoff (the SDK does this automatically with retries); "
        "(3) batch requests via Batch API for high-volume (50% discount + 24h window, no rate-limit pressure on real-time tier); "
        "(4) for spikes: shard across multiple API keys (different orgs); "
        "(5) parse `x-ratelimit-reset-requests` header to know when to retry.",
        f"{BASE}/guides/rate-limits",
    ),
    (
        "openai-errors", "openai error: 429 Rate limit reached for tokens (TPM)",
        "Hit per-minute token throughput limit (input + output combined). "
        "Fix: (1) check `x-ratelimit-remaining-tokens` header for remaining budget; "
        "(2) reduce prompt size: trim context, summarize history, use embeddings for RAG instead of dumping docs into prompt; "
        "(3) switch to smaller model: `gpt-4o-mini` has higher TPM limit than `gpt-4o`; "
        "(4) Batch API for offline workloads (separate higher limits); "
        "(5) implement token-aware throttling — count tokens before sending.",
        f"{BASE}/guides/rate-limits",
    ),
    (
        "openai-errors", "openai error: 400 model_not_found / Invalid model 'X'",
        "Model name typo, OR your org doesn't have access. "
        "Fix: (1) list available: `GET /v1/models` (your key only sees models you can use); "
        "(2) common typos: `gpt-4o` (not `gpt4o`), `gpt-4o-mini` (not `gpt-4o-mini-2024`); "
        "(3) deprecated names: `gpt-3.5-turbo-0301` (gone) → use `gpt-3.5-turbo` or upgrade to gpt-4o-mini; "
        "(4) reasoning models (o-series) require approved tier — check your org access; "
        "(5) preview models: may require waitlist signup.",
        f"{BASE}/models",
    ),
    (
        "openai-errors", "openai error: 400 max_tokens exceeds context length",
        "Total (input + reserved output) > model's context window. "
        "Fix: (1) reduce input — trim messages, summarize history; "
        "(2) lower `max_tokens` (or `max_completion_tokens` for reasoning models); "
        "(3) check model context: `gpt-4o` = 128K, `o3` = 200K, `gpt-4o-mini` = 128K; "
        "(4) for long docs: use RAG (retrieve top-k chunks via embeddings) instead of stuffing whole doc; "
        "(5) count tokens locally with `tiktoken` (Python) or `tiktoken-go` before sending.",
        f"{BASE}/guides/text-generation",
    ),
    (
        "openai-errors", "openai error: 400 'messages' is required (chat completions)",
        "Chat Completions needs a non-empty `messages` array. "
        "Fix: (1) minimum: `messages=[{\"role\": \"user\", \"content\": \"hi\"}]`; "
        "(2) common: passed `prompt` (old completions param) instead of `messages`; "
        "(3) Responses API uses `input` instead of `messages` — make sure you're hitting the right endpoint; "
        "(4) `role` must be one of: `system`/`developer`, `user`, `assistant`, `tool`.",
        f"{BASE}/api-reference/chat",
    ),
    (
        "openai-errors", "openai error: 400 Invalid 'response_format' / JSON mode fails",
        "Bad `response_format` spec. "
        "Fix: (1) basic JSON mode: `response_format={\"type\": \"json_object\"}` + you MUST include `JSON` in the system prompt; "
        "(2) structured outputs (better, K8s 1.40+ models): `response_format={\"type\": \"json_schema\", \"json_schema\": {...}}`; "
        "(3) schema must declare all properties as required when `strict: true`; "
        "(4) `additionalProperties: false` is required when strict; "
        "(5) max schema depth: 5 levels.",
        f"{BASE}/guides/structured-outputs",
    ),
    (
        "openai-errors", "openai error: 400 Tool/function call schema invalid",
        "Function `parameters` schema doesn't follow JSON Schema rules supported by OpenAI. "
        "Fix: (1) top-level must be object with `properties` and `required`; "
        "(2) for structured strict mode: `additionalProperties: false`, all properties in `required`; "
        "(3) supported types: string, number, boolean, integer, array, object, null, enum; "
        "(4) recursion: up to 5 levels; "
        "(5) for unions: use `anyOf` (limited support); for enums: `\"enum\": [\"a\",\"b\"]`.",
        f"{BASE}/guides/function-calling",
    ),
    (
        "openai-errors", "openai error: 400 image too large / unsupported format (vision)",
        "Image rejected by vision input. "
        "Fix: (1) max size: 20MB per image, 50MB total; "
        "(2) supported: PNG, JPEG, WEBP, non-animated GIF; "
        "(3) max dimension: 2048x2048 (anything larger gets auto-downscaled); "
        "(4) for huge images: pre-resize with PIL/ImageMagick: `convert in.jpg -resize 2048x2048\\> out.jpg`; "
        "(5) for HEIC (iPhone): convert to JPG first.",
        f"{BASE}/guides/vision",
    ),
    (
        "openai-errors", "openai error: 400 audio file exceeds 25MB (Whisper)",
        "Whisper has a 25MB file size limit. "
        "Fix: (1) compress: `ffmpeg -i in.wav -ab 64k -ac 1 -ar 16000 out.mp3` (mono 16kHz 64kbps mp3 is usually fine); "
        "(2) chunk and stitch: split into <25MB segments, transcribe each, concatenate; "
        "(3) use chunking with overlap to avoid cutting mid-word; "
        "(4) for very long files: use Whisper open-source locally (no size limit).",
        f"{BASE}/guides/speech-to-text",
    ),
    (
        "openai-errors", "openai error: 400 unsupported parameter for model",
        "Some params don't apply to all models. "
        "Fix: (1) reasoning models (o-series): NO `temperature`, NO `top_p`, NO `presence_penalty` — they're internally fixed; "
        "(2) `o3` uses `max_completion_tokens` (not `max_tokens`); "
        "(3) `seed`: only supported on some models for reproducibility; "
        "(4) `logit_bias`: not on reasoning models; "
        "(5) check error message — it names the unsupported param.",
        f"{BASE}/models",
    ),
    (
        "openai-errors", "openai error: 400 cannot use both 'tools' and 'functions'",
        "`functions` is the deprecated parameter — `tools` is the modern one. "
        "Fix: (1) migrate: ```python\\nold_funcs = [{\"name\": \"f\", \"parameters\": {...}}]\\nnew_tools = [{\"type\": \"function\", \"function\": {\"name\": \"f\", \"parameters\": {...}}}]\\n```; "
        "(2) drop `function_call`, use `tool_choice` instead: `tool_choice=\"auto\"` / `\"required\"` / `{\"type\": \"function\", \"function\": {\"name\": \"f\"}}`; "
        "(3) response: tool calls now come as `message.tool_calls` array (not `message.function_call`).",
        f"{BASE}/guides/function-calling",
    ),
    (
        "openai-errors", "openai error: 400 streaming + structured outputs / response_format conflict",
        "Streaming + JSON schema strict mode requires the new Responses API (or specific Chat Completions versions). "
        "Fix: (1) for Chat Completions streaming: use `response_format={\"type\": \"json_object\"}` (loose JSON) not json_schema; "
        "(2) for strict schema streaming: switch to Responses API; "
        "(3) for non-streaming: both `json_object` and `json_schema` work; "
        "(4) accumulate full response client-side then JSON-parse — partial JSON streams need a streaming parser.",
        f"{BASE}/guides/structured-outputs",
    ),
    (
        "openai-errors", "openai error: 408 Request timeout",
        "Server took too long to respond (often for very long reasoning runs). "
        "Fix: (1) raise SDK timeout: Python `OpenAI(timeout=600.0)`, Node `new OpenAI({timeout: 600 * 1000})`; "
        "(2) for o-series reasoning models: long thinking chains can take 60-120s — bump to 300s+; "
        "(3) for batch jobs: switch to Batch API (24h window, no per-request timeout pressure); "
        "(4) for streaming: timeout applies per-chunk in some SDKs.",
        f"{BASE}/guides/production-best-practices",
    ),
    (
        "openai-errors", "openai error: 500 / 502 / 503 server errors",
        "OpenAI infrastructure issue. "
        "Fix: (1) check status: https://status.openai.com; "
        "(2) retry with exponential backoff — SDK does this automatically (`max_retries=3` default); "
        "(3) for production: implement circuit breaker pattern + queue requests; "
        "(4) cross-region failover: not natively supported (single endpoint); "
        "(5) for HA: consider routing through Azure OpenAI as a backup (same models, separate uptime).",
        f"{BASE}/guides/production-best-practices",
    ),
    (
        "openai-errors", "openai error: model refused to answer / safety system",
        "Model detected request as policy-violating and returned a refusal. "
        "Fix: (1) check `message.refusal` (string with explanation) — only on safety-trained models; "
        "(2) if legitimate use case: rephrase, add context explaining intent; "
        "(3) for systems: log refusals as 'refusal' category (not error); "
        "(4) some refusals come back as `finish_reason=\"content_filter\"`; "
        "(5) for Moderations: use `/v1/moderations` to pre-screen user inputs.",
        f"{BASE}/guides/safety-best-practices",
    ),
    (
        "openai-errors", "openai error: finish_reason='length' (response was cut off)",
        "Output reached `max_tokens` before completing. "
        "Fix: (1) raise `max_tokens` (or `max_completion_tokens` for reasoning models); "
        "(2) for reasoning models: also includes reasoning tokens — bump higher; "
        "(3) ask model for shorter output: 'be concise' in system prompt, or specify max sentences/words; "
        "(4) for streaming JSON: pre-allocate larger budget — partial JSON is unparseable; "
        "(5) detect: `response.choices[0].finish_reason == 'length'`.",
        f"{BASE}/guides/text-generation",
    ),
    (
        "openai-errors", "openai error: SDK APIConnectionError",
        "Network couldn't reach api.openai.com. "
        "Fix: (1) check internet: `curl https://api.openai.com/v1/models -H \"Authorization: Bearer $OPENAI_API_KEY\"`; "
        "(2) corporate proxy: set `HTTPS_PROXY=http://proxy:8080`; "
        "(3) firewall may block api.openai.com — allowlist it; "
        "(4) DNS: `nslookup api.openai.com`; "
        "(5) Python SDK retries on this by default — for persistent failures, check infrastructure not code.",
        f"{BASE}/guides/error-codes",
    ),
    (
        "openai-errors", "openai error: SDK APITimeoutError",
        "SDK gave up waiting for response. "
        "Fix: (1) Python: `OpenAI(timeout=httpx.Timeout(60.0, connect=10.0))`; "
        "(2) Node: `new OpenAI({timeout: 60_000})`; "
        "(3) per-request: pass `timeout=` to the call site; "
        "(4) for streaming: keep-alive sends keep timeout fresh — but slow models still can hit it; "
        "(5) for reasoning: 300s+ is realistic.",
        f"{BASE}/guides/production-best-practices",
    ),
    (
        "openai-errors", "openai error: fine-tuning training file format error",
        "JSONL upload rejected. "
        "Fix: (1) one JSON object per line, no trailing comma; "
        "(2) chat format: ```{\"messages\": [{\"role\": \"system\", \"content\": \"...\"}, {\"role\": \"user\", \"content\": \"...\"}, {\"role\": \"assistant\", \"content\": \"...\"}]}```; "
        "(3) min: 10 examples; recommended: 50-100+; "
        "(4) validate locally first: `python -c 'import json; [json.loads(l) for l in open(\"train.jsonl\")]'`; "
        "(5) upload as `purpose=\"fine-tune\"`.",
        f"{BASE}/guides/fine-tuning",
    ),
    (
        "openai-errors", "openai error: 'cannot use temperature with reasoning models'",
        "o1, o3, o3-mini handle exploration internally — temperature is fixed. "
        "Fix: (1) drop `temperature` from request; "
        "(2) use `reasoning_effort`: `\"low\" | \"medium\" | \"high\"` controls thinking depth (more tokens, slower, smarter); "
        "(3) use `max_completion_tokens` (not `max_tokens`); "
        "(4) o-series tokens include hidden reasoning — usage reports both `reasoning_tokens` and `completion_tokens`.",
        f"{BASE}/models",
    ),
    (
        "openai-errors", "openai error: cannot get embedding for >8192 tokens",
        "Embedding models cap input at 8191 tokens (text-embedding-3-small/large). "
        "Fix: (1) chunk: split into ≤8000-token windows with overlap; "
        "(2) per-chunk embeddings; average or store separately for retrieval; "
        "(3) use `tiktoken.encoding_for_model('text-embedding-3-small')` to count tokens; "
        "(4) for very long docs: hierarchical embeddings (chunk → section summaries → doc-level).",
        f"{BASE}/guides/embeddings",
    ),
    (
        "openai-errors", "openai error: tool_choice malformed",
        "`tool_choice` syntax wrong. "
        "Fix: (1) `\"auto\"` — model picks tool or text; "
        "(2) `\"required\"` — model MUST call a tool (no plain text); "
        "(3) `\"none\"` — model won't call tools; "
        "(4) specific function: `{\"type\": \"function\", \"function\": {\"name\": \"my_func\"}}`; "
        "(5) ensure `tools` array is non-empty when `tool_choice` is non-`none`.",
        f"{BASE}/guides/function-calling",
    ),
    (
        "openai-errors", "openai error: 400 invalid 'voice' for TTS",
        "TTS voice must be one of: alloy, echo, fable, onyx, nova, shimmer. "
        "Fix: (1) pick from approved list; "
        "(2) for new voices (Realtime API): different voice catalog; "
        "(3) format param: `\"mp3\"` (default), `\"opus\"`, `\"aac\"`, `\"flac\"`, `\"wav\"`, `\"pcm\"`; "
        "(4) speed range: 0.25-4.0 (`speed=1.0` default).",
        f"{BASE}/guides/text-to-speech",
    ),
    (
        "openai-errors", "openai error: 400 invalid 'size' for image generation",
        "DALL-E 3 sizes: `1024x1024`, `1024x1792`, `1792x1024` only. "
        "Fix: (1) for square: `1024x1024`; "
        "(2) for portrait: `1024x1792`; "
        "(3) for landscape: `1792x1024`; "
        "(4) gpt-image-1 has more options including `auto`; "
        "(5) DALL-E 2 (legacy): 256, 512, or 1024 squares.",
        f"{BASE}/guides/images",
    ),
    (
        "openai-errors", "openai error: Batch API job stuck / failed",
        "Batch job didn't complete. "
        "Fix: (1) status: `client.batches.retrieve(batch_id)` shows status (validating / in_progress / completed / failed / expired); "
        "(2) `failed`: check `errors` field on the batch object; "
        "(3) line-level errors: download `error_file_id` to see per-request failures; "
        "(4) batches expire after 24h — re-submit if you missed the window; "
        "(5) cancellation: `client.batches.cancel(batch_id)`.",
        f"{BASE}/guides/batch",
    ),
    (
        "openai-errors", "openai error: organization_not_active / org disabled",
        "Org has been suspended (billing, ToS violation, etc.). "
        "Fix: (1) check email — OpenAI usually notifies via the org owner's email; "
        "(2) login to platform.openai.com — banner shows reason; "
        "(3) contact support: help@openai.com; "
        "(4) for billing: update payment method on https://platform.openai.com/settings/organization/billing.",
        f"{BASE}/guides/error-codes",
    ),
    (
        "openai-errors", "openai error: tiktoken encoding mismatch",
        "Counting tokens with wrong encoder gives wrong numbers. "
        "Fix: (1) Python: `tiktoken.encoding_for_model('gpt-4o')` (auto-picks correct encoding); "
        "(2) fallback: `tiktoken.get_encoding('o200k_base')` (gpt-4o family) or `cl100k_base` (older GPT-4/3.5); "
        "(3) for chat: count needs +4 tokens per message + 3 for reply priming; "
        "(4) library wraps this: see `openai-cookbook` `num_tokens_from_messages` helper.",
        f"{BASE}/guides/rate-limits",
    ),

    # ============================================================
    # openai-recipes — 38 common workflows
    # ============================================================
    (
        "openai-recipes", "openai recipe: basic chat completion (Python)",
        "```python\\nfrom openai import OpenAI\\nclient = OpenAI()  # reads OPENAI_API_KEY\\n\\nresp = client.chat.completions.create(\\n    model=\"gpt-4o-mini\",\\n    messages=[\\n        {\"role\": \"system\", \"content\": \"You are concise.\"},\\n        {\"role\": \"user\", \"content\": \"What is 2+2?\"},\\n    ],\\n)\\nprint(resp.choices[0].message.content)\\n```. "
        "Total tokens: `resp.usage.total_tokens`. Finish reason: `resp.choices[0].finish_reason`.",
        f"{BASE}/quickstart",
    ),
    (
        "openai-recipes", "openai recipe: basic chat completion (Node/TypeScript)",
        "```ts\\nimport OpenAI from \"openai\";\\nconst client = new OpenAI(); // reads OPENAI_API_KEY\\n\\nconst resp = await client.chat.completions.create({\\n  model: \"gpt-4o-mini\",\\n  messages: [{ role: \"user\", content: \"What is 2+2?\" }],\\n});\\nconsole.log(resp.choices[0].message.content);\\n```",
        f"{BASE}/quickstart",
    ),
    (
        "openai-recipes", "openai recipe: basic chat completion (curl)",
        "```bash\\ncurl https://api.openai.com/v1/chat/completions \\\\\\n  -H \"Content-Type: application/json\" \\\\\\n  -H \"Authorization: Bearer $OPENAI_API_KEY\" \\\\\\n  -d '{\\n    \"model\": \"gpt-4o-mini\",\\n    \"messages\": [{\"role\": \"user\", \"content\": \"Hi\"}]\\n  }'\\n```. "
        "Parse with jq: `... | jq -r '.choices[0].message.content'`.",
        f"{BASE}/api-reference/chat",
    ),
    (
        "openai-recipes", "openai recipe: streaming chat completion (Python)",
        "```python\\nstream = client.chat.completions.create(\\n    model=\"gpt-4o-mini\",\\n    messages=[{\"role\": \"user\", \"content\": \"Tell a short story\"}],\\n    stream=True,\\n)\\nfor chunk in stream:\\n    if chunk.choices[0].delta.content:\\n        print(chunk.choices[0].delta.content, end=\"\", flush=True)\\n```. "
        "Stream usage: pass `stream_options={\"include_usage\": True}` to get token counts in final chunk.",
        f"{BASE}/api-reference/chat",
    ),
    (
        "openai-recipes", "openai recipe: multi-turn conversation",
        "Keep `messages` array growing — model has no memory between requests. "
        "```python\\nhistory = [{\"role\": \"system\", \"content\": \"You are helpful.\"}]\\nwhile True:\\n    user_msg = input(\"> \")\\n    history.append({\"role\": \"user\", \"content\": user_msg})\\n    resp = client.chat.completions.create(model=\"gpt-4o-mini\", messages=history)\\n    reply = resp.choices[0].message.content\\n    print(reply)\\n    history.append({\"role\": \"assistant\", \"content\": reply})\\n```. "
        "Watch token cost: full history sent every turn. For long sessions: summarize older turns.",
        f"{BASE}/guides/text-generation",
    ),
    (
        "openai-recipes", "openai recipe: function calling / tools",
        "```python\\ntools = [{\"type\": \"function\", \"function\": {\\n    \"name\": \"get_weather\",\\n    \"description\": \"Get weather for a city\",\\n    \"parameters\": {\\n        \"type\": \"object\",\\n        \"properties\": {\"city\": {\"type\": \"string\"}},\\n        \"required\": [\"city\"],\\n    }\\n}}]\\nresp = client.chat.completions.create(model=\"gpt-4o\", messages=[...], tools=tools)\\nfor tc in resp.choices[0].message.tool_calls or []:\\n    args = json.loads(tc.function.arguments)\\n    result = my_get_weather(args[\"city\"])\\n    # send result back: append {\"role\": \"tool\", \"tool_call_id\": tc.id, \"content\": result}\\n```",
        f"{BASE}/guides/function-calling",
    ),
    (
        "openai-recipes", "openai recipe: structured outputs (strict JSON schema)",
        "```python\\nfrom pydantic import BaseModel\\nclass Person(BaseModel):\\n    name: str\\n    age: int\\n\\nresp = client.beta.chat.completions.parse(\\n    model=\"gpt-4o-mini\",\\n    messages=[{\"role\": \"user\", \"content\": \"Extract: Alice is 30.\"}],\\n    response_format=Person,\\n)\\nperson = resp.choices[0].message.parsed  # Person instance\\n```. "
        "Strict mode guarantees schema compliance (no hallucinated fields). Models: gpt-4o-2024-08-06+, gpt-4o-mini-2024-07-18+.",
        f"{BASE}/guides/structured-outputs",
    ),
    (
        "openai-recipes", "openai recipe: JSON mode (loose)",
        "For pre-strict-schema models or quick use. "
        "```python\\nresp = client.chat.completions.create(\\n    model=\"gpt-4o-mini\",\\n    messages=[\\n        {\"role\": \"system\", \"content\": \"Output ONLY JSON.\"},\\n        {\"role\": \"user\", \"content\": \"...\"},\\n    ],\\n    response_format={\"type\": \"json_object\"},\\n)\\ndata = json.loads(resp.choices[0].message.content)\\n```. "
        "MUST include 'JSON' in system prompt. No schema enforcement — model picks structure.",
        f"{BASE}/guides/structured-outputs",
    ),
    (
        "openai-recipes", "openai recipe: vision (image URL)",
        "```python\\nresp = client.chat.completions.create(\\n    model=\"gpt-4o\",\\n    messages=[{\\n        \"role\": \"user\",\\n        \"content\": [\\n            {\"type\": \"text\", \"text\": \"What's in this image?\"},\\n            {\"type\": \"image_url\", \"image_url\": {\"url\": \"https://example.com/cat.jpg\", \"detail\": \"high\"}},\\n        ],\\n    }],\\n)\\n```. "
        "`detail`: `low` (fast/cheap, 85 tokens), `high` (more tokens, better detail), `auto`.",
        f"{BASE}/guides/vision",
    ),
    (
        "openai-recipes", "openai recipe: vision (base64-encoded local image)",
        "```python\\nimport base64\\nwith open(\"cat.jpg\", \"rb\") as f:\\n    b64 = base64.b64encode(f.read()).decode()\\n\\nresp = client.chat.completions.create(\\n    model=\"gpt-4o\",\\n    messages=[{\"role\": \"user\", \"content\": [\\n        {\"type\": \"text\", \"text\": \"Describe this.\"},\\n        {\"type\": \"image_url\", \"image_url\": {\"url\": f\"data:image/jpeg;base64,{b64}\"}},\\n    ]}],\\n)\\n```. "
        "Max 20MB. Auto-downscaled to 2048x2048 if larger.",
        f"{BASE}/guides/vision",
    ),
    (
        "openai-recipes", "openai recipe: embeddings (single + batch)",
        "Single: ```python\\nresp = client.embeddings.create(model=\"text-embedding-3-small\", input=\"hello world\")\\nvec = resp.data[0].embedding  # list[float] of length 1536\\n```. "
        "Batch: pass `input=[\"text1\", \"text2\", ...]` — up to 2048 inputs per call. "
        "Models: `text-embedding-3-small` (1536d, cheap), `text-embedding-3-large` (3072d, better). "
        "Reduce dimensions: `dimensions=512` (saves storage, small quality loss).",
        f"{BASE}/guides/embeddings",
    ),
    (
        "openai-recipes", "openai recipe: cosine similarity from embeddings",
        "```python\\nimport numpy as np\\nfrom openai import OpenAI\\nclient = OpenAI()\\n\\ndef embed(text):\\n    return np.array(client.embeddings.create(model=\"text-embedding-3-small\", input=text).data[0].embedding)\\n\\nv1, v2 = embed(\"cat\"), embed(\"dog\")\\nsim = v1 @ v2 / (np.linalg.norm(v1) * np.linalg.norm(v2))\\n```. "
        "OpenAI embeddings are already normalized: `sim = v1 @ v2` works directly. "
        "For vector DB: pgvector, Pinecone, Qdrant, Weaviate.",
        f"{BASE}/guides/embeddings",
    ),
    (
        "openai-recipes", "openai recipe: Whisper transcription",
        "```python\\nwith open(\"audio.mp3\", \"rb\") as f:\\n    transcript = client.audio.transcriptions.create(\\n        model=\"whisper-1\",\\n        file=f,\\n        language=\"en\",  # ISO-639-1\\n        response_format=\"verbose_json\",  # or \"text\", \"srt\", \"vtt\"\\n    )\\nprint(transcript.text)\\n```. "
        "Word-level timestamps: `timestamp_granularities=[\"word\"]`. "
        "Max 25MB — compress with ffmpeg if larger.",
        f"{BASE}/guides/speech-to-text",
    ),
    (
        "openai-recipes", "openai recipe: Whisper translation to English",
        "```python\\nwith open(\"french.mp3\", \"rb\") as f:\\n    translation = client.audio.translations.create(\\n        model=\"whisper-1\",\\n        file=f,\\n    )\\nprint(translation.text)  # English text from any source language\\n```. "
        "Translation is always TO English — no other targets via this endpoint.",
        f"{BASE}/guides/speech-to-text",
    ),
    (
        "openai-recipes", "openai recipe: text-to-speech (TTS)",
        "```python\\nresp = client.audio.speech.create(\\n    model=\"tts-1\",  # or tts-1-hd for higher quality\\n    voice=\"nova\",  # alloy, echo, fable, onyx, nova, shimmer\\n    input=\"Hello, world!\",\\n    response_format=\"mp3\",  # opus, aac, flac, wav, pcm\\n    speed=1.0,  # 0.25 to 4.0\\n)\\nresp.stream_to_file(\"output.mp3\")\\n```",
        f"{BASE}/guides/text-to-speech",
    ),
    (
        "openai-recipes", "openai recipe: streaming TTS to speaker",
        "```python\\nwith client.audio.speech.with_streaming_response.create(\\n    model=\"tts-1\",\\n    voice=\"alloy\",\\n    input=\"Streaming audio\",\\n    response_format=\"pcm\",\\n) as resp:\\n    # pipe to audio player or sounddevice\\n    for chunk in resp.iter_bytes():\\n        player.write(chunk)\\n```. "
        "PCM format has lowest latency (no decoding).",
        f"{BASE}/guides/text-to-speech",
    ),
    (
        "openai-recipes", "openai recipe: DALL-E 3 image generation",
        "```python\\nresp = client.images.generate(\\n    model=\"dall-e-3\",\\n    prompt=\"A serene Japanese garden at dawn\",\\n    size=\"1024x1024\",  # or 1024x1792, 1792x1024\\n    quality=\"standard\",  # or \"hd\"\\n    style=\"vivid\",  # or \"natural\"\\n    n=1,  # DALL-E 3 only supports n=1\\n)\\nurl = resp.data[0].url  # expires after ~1 hour\\nrevised_prompt = resp.data[0].revised_prompt  # DALL-E 3 may rewrite\\n```",
        f"{BASE}/guides/images",
    ),
    (
        "openai-recipes", "openai recipe: GPT-Image-1 (newer image generation)",
        "Higher quality, supports edits + variations via the same endpoint. "
        "```python\\nresp = client.images.generate(\\n    model=\"gpt-image-1\",\\n    prompt=\"...\",\\n    size=\"auto\",  # or 1024x1024, 1536x1024, 1024x1536\\n    quality=\"high\",  # low/medium/high\\n    output_format=\"png\",  # or webp, jpeg\\n)\\nb64 = resp.data[0].b64_json  # returns base64 by default\\n```. "
        "Edits: pass `image` + `mask` to `/images/edits`.",
        f"{BASE}/guides/images",
    ),
    (
        "openai-recipes", "openai recipe: moderations (pre-screen user input)",
        "```python\\nresult = client.moderations.create(\\n    model=\"omni-moderation-latest\",\\n    input=user_text,  # or [text1, text2] for batch\\n)\\nif result.results[0].flagged:\\n    return \"Refused\"\\nelse:\\n    # safe to send to chat completions\\n    ...\\n```. "
        "Categories: hate, harassment, self-harm, sexual, violence (+ subcategories). "
        "FREE — no token cost.",
        f"{BASE}/guides/moderation",
    ),
    (
        "openai-recipes", "openai recipe: fine-tuning a model",
        "Steps: (1) prepare JSONL with chat-format examples (50-100+ recommended); (2) `file = client.files.create(file=open(\"train.jsonl\",\"rb\"), purpose=\"fine-tune\")`; "
        "(3) `job = client.fine_tuning.jobs.create(training_file=file.id, model=\"gpt-4o-mini-2024-07-18\")`; "
        "(4) poll: `client.fine_tuning.jobs.retrieve(job.id)` — status `succeeded` gives `fine_tuned_model` ID; "
        "(5) use: `model=\"ft:gpt-4o-mini:my-org::abc123\"` in chat completions.",
        f"{BASE}/guides/fine-tuning",
    ),
    (
        "openai-recipes", "openai recipe: Batch API for high-volume async",
        "50% cheaper, 24h window. "
        "(1) Prepare JSONL with one request per line (custom_id + method + url + body); "
        "(2) `file = client.files.create(file=..., purpose=\"batch\")`; "
        "(3) `batch = client.batches.create(input_file_id=file.id, endpoint=\"/v1/chat/completions\", completion_window=\"24h\")`; "
        "(4) poll: `client.batches.retrieve(batch.id)`; "
        "(5) when `completed`: download `output_file_id` for results.",
        f"{BASE}/guides/batch",
    ),
    (
        "openai-recipes", "openai recipe: use o-series reasoning models",
        "```python\\nresp = client.chat.completions.create(\\n    model=\"o3\",  # or o3-mini, o1\\n    messages=[{\"role\": \"user\", \"content\": \"Solve: ...\"}],\\n    reasoning_effort=\"high\",  # low/medium/high\\n    max_completion_tokens=10000,  # NOT max_tokens\\n)\\nprint(resp.choices[0].message.content)\\nprint(resp.usage.completion_tokens_details.reasoning_tokens)  # hidden thinking cost\\n```. "
        "NO `temperature`/`top_p`/`presence_penalty`. For function calling: supported on o3 + o3-mini.",
        f"{BASE}/models/o3",
    ),
    (
        "openai-recipes", "openai recipe: retry with exponential backoff",
        "SDK retries by default (3x). Customize: ```python\\nclient = OpenAI(max_retries=5)\\n# or per-request:\\nresp = client.chat.completions.with_options(max_retries=5).create(...)\\n```. "
        "For Node: `new OpenAI({ maxRetries: 5 })`. "
        "Read `Retry-After` header on 429: respect that delay before retrying. "
        "For custom: catch RateLimitError, sleep + retry with backoff (1s, 2s, 4s, 8s...).",
        f"{BASE}/guides/rate-limits",
    ),
    (
        "openai-recipes", "openai recipe: track token usage + estimate cost",
        "Per response: `resp.usage` has `prompt_tokens`, `completion_tokens`, `total_tokens`. "
        "Reasoning: also `completion_tokens_details.reasoning_tokens`. "
        "Pricing: https://openai.com/api/pricing (per-1M tokens). "
        "Estimate before sending: `tiktoken.encoding_for_model(\"gpt-4o\").encode(prompt)` then `len(tokens) / 1_000_000 * input_price`. "
        "Org-wide: `https://platform.openai.com/usage` dashboard.",
        f"{BASE}/guides/rate-limits",
    ),
    (
        "openai-recipes", "openai recipe: count tokens locally with tiktoken",
        "```python\\nimport tiktoken\\nenc = tiktoken.encoding_for_model(\"gpt-4o\")\\nn_tokens = len(enc.encode(\"Hello world\"))\\n# for chat: +4 per message + 3 reply priming (approx)\\ndef num_tokens_from_messages(messages):\\n    n = 3  # reply priming\\n    for m in messages:\\n        n += 4 + len(enc.encode(m[\"content\"]))\\n    return n\\n```",
        f"{BASE}/guides/rate-limits",
    ),
    (
        "openai-recipes", "openai recipe: use the new Responses API",
        "Modern unified endpoint (preferred for new code over Chat Completions). "
        "```python\\nresp = client.responses.create(\\n    model=\"gpt-4o-mini\",\\n    input=\"What is 2+2?\",  # string OR full messages array\\n)\\nprint(resp.output_text)\\n```. "
        "Features: built-in tool use (web search, code interpreter), stateful conversations via `previous_response_id`. "
        "Use for: agentic flows, multi-modal pipelines, stateful chat.",
        f"{BASE}/api-reference/responses",
    ),
    (
        "openai-recipes", "openai recipe: file upload + retrieval (for Assistants / RAG)",
        "Upload: `file = client.files.create(file=open(\"doc.pdf\", \"rb\"), purpose=\"assistants\")` (or `\"user_data\"`, `\"vision\"`, `\"batch\"`, `\"fine-tune\"`). "
        "List: `client.files.list(purpose=\"assistants\")`. "
        "Get content: `client.files.content(file.id).read()`. "
        "Delete: `client.files.delete(file.id)`. "
        "Limits: 512MB per file, ~100GB per org.",
        f"{BASE}/api-reference/files",
    ),
    (
        "openai-recipes", "openai recipe: use Assistants API",
        "Stateful, tool-using agents. (1) Create assistant: `asst = client.beta.assistants.create(name=\"helper\", model=\"gpt-4o\", instructions=\"...\", tools=[{\"type\": \"code_interpreter\"}])`; "
        "(2) Create thread: `thread = client.beta.threads.create()`; "
        "(3) Add message: `client.beta.threads.messages.create(thread.id, role=\"user\", content=\"Solve x^2=10\")`; "
        "(4) Run: `run = client.beta.threads.runs.create_and_poll(thread.id, assistant_id=asst.id)`; "
        "(5) Read messages: `client.beta.threads.messages.list(thread.id)`. "
        "Caveat: Assistants API will be sunset in favor of Responses API in 2026.",
        f"{BASE}/api-reference/assistants",
    ),
    (
        "openai-recipes", "openai recipe: switch between OpenAI and Azure OpenAI",
        "Azure has different auth + endpoint. ```python\\nfrom openai import AzureOpenAI\\nclient = AzureOpenAI(\\n    api_key=os.environ[\"AZURE_OPENAI_KEY\"],\\n    api_version=\"2024-10-21\",\\n    azure_endpoint=\"https://my-resource.openai.azure.com\",\\n)\\n# model = deployment name, not OpenAI model name\\nresp = client.chat.completions.create(model=\"my-gpt4o-deployment\", messages=...)\\n```",
        f"{BASE}/libraries",
    ),
    (
        "openai-recipes", "openai recipe: use SDK with OpenAI-compatible providers (Groq, Together, etc.)",
        "Many providers expose OpenAI-format endpoints. ```python\\nclient = OpenAI(\\n    api_key=os.environ[\"GROQ_API_KEY\"],\\n    base_url=\"https://api.groq.com/openai/v1\",  # or together.xyz, fireworks, anyscale, etc.\\n)\\n# Use their model names: groq has llama-3.1, mixtral, etc.\\nresp = client.chat.completions.create(model=\"llama-3.1-70b-versatile\", messages=...)\\n```. "
        "Caveat: not every endpoint is identical — function calling + structured outputs vary.",
        f"{BASE}/libraries",
    ),
    (
        "openai-recipes", "openai recipe: handle refusal in structured outputs",
        "Strict outputs may include `.refusal` instead of `.parsed`. "
        "```python\\nresp = client.beta.chat.completions.parse(\\n    model=\"gpt-4o-mini\",\\n    messages=[...],\\n    response_format=MySchema,\\n)\\nmsg = resp.choices[0].message\\nif msg.refusal:\\n    print(f\"Refused: {msg.refusal}\")\\nelse:\\n    data = msg.parsed\\n```",
        f"{BASE}/guides/structured-outputs",
    ),
    (
        "openai-recipes", "openai recipe: streaming function calls with delta accumulation",
        "Tool calls arrive in deltas — must accumulate manually. ```python\\ntool_calls = []\\nfor chunk in stream:\\n    delta = chunk.choices[0].delta\\n    if delta.tool_calls:\\n        for tc in delta.tool_calls:\\n            if tc.index >= len(tool_calls):\\n                tool_calls.append({\"id\": tc.id, \"name\": \"\", \"arguments\": \"\"})\\n            if tc.function.name:\\n                tool_calls[tc.index][\"name\"] = tc.function.name\\n            if tc.function.arguments:\\n                tool_calls[tc.index][\"arguments\"] += tc.function.arguments\\n```. "
        "After stream ends: `json.loads(tc[\"arguments\"])` to parse each call.",
        f"{BASE}/guides/function-calling",
    ),
    (
        "openai-recipes", "openai recipe: log and replay requests for debugging",
        "Save full requests + responses for debugging. ```python\\nimport json, datetime\\ndef logged_call(client, **kwargs):\\n    req = {\"timestamp\": datetime.datetime.utcnow().isoformat(), \"kwargs\": kwargs}\\n    try:\\n        resp = client.chat.completions.create(**kwargs)\\n        req[\"response\"] = resp.model_dump()\\n        return resp\\n    except Exception as e:\\n        req[\"error\"] = str(e)\\n        raise\\n    finally:\\n        with open(\"openai-log.jsonl\", \"a\") as f:\\n            f.write(json.dumps(req, default=str) + \"\\n\")\\n```. "
        "Replay: parse JSONL, re-run kwargs.",
        f"{BASE}/guides/production-best-practices",
    ),
    (
        "openai-recipes", "openai recipe: chunk long documents for embeddings",
        "Strategy: split by tokens (not chars). ```python\\nimport tiktoken\\nenc = tiktoken.encoding_for_model(\"text-embedding-3-small\")\\n\\ndef chunk(text, chunk_size=500, overlap=50):\\n    tokens = enc.encode(text)\\n    chunks = []\\n    for i in range(0, len(tokens), chunk_size - overlap):\\n        chunks.append(enc.decode(tokens[i:i+chunk_size]))\\n    return chunks\\n\\nembeddings = client.embeddings.create(model=\"text-embedding-3-small\", input=chunk(long_doc)).data\\n```",
        f"{BASE}/guides/embeddings",
    ),
    (
        "openai-recipes", "openai recipe: implement RAG (retrieval-augmented generation)",
        "Pipeline: (1) chunk + embed your docs offline; (2) at query time, embed the question; "
        "(3) cosine-search top K chunks from your vector DB; (4) stuff into prompt: `Context:\\n{chunks}\\n\\nQuestion: {q}`. "
        "Vector DBs: pgvector (Postgres extension, easy), Pinecone (managed), Qdrant/Weaviate (self-hosted). "
        "For prod: re-rank top 20 with a reranker model before stuffing top 5.",
        f"{BASE}/guides/embeddings",
    ),
    (
        "openai-recipes", "openai recipe: cancel a long-running streaming request",
        "Python httpx: ```python\\nimport threading, time\\nstop = threading.Event()\\ndef cancel_after(s): time.sleep(s); stop.set()\\nthreading.Thread(target=cancel_after, args=(5,)).start()\\n\\nfor chunk in stream:\\n    if stop.is_set():\\n        stream.response.close()\\n        break\\n    print(chunk.choices[0].delta.content or \"\", end=\"\")\\n```. "
        "Closing the underlying response interrupts the server-sent events.",
        f"{BASE}/api-reference/chat",
    ),
    (
        "openai-recipes", "openai recipe: use system / developer messages effectively",
        "System (or `developer` for o-series): set persona, rules, format. Comes FIRST. "
        "Best practices: (1) be specific: 'You are a senior Python reviewer. Reply in bullet points.'; "
        "(2) put constraints in system, examples in user; "
        "(3) for o-series: use `developer` role instead of `system` (clearer semantic in reasoning models); "
        "(4) avoid putting volatile data in system (no benefit, fills context); "
        "(5) for few-shot: structure as alternating user/assistant pairs BEFORE the real user query.",
        f"{BASE}/guides/text-generation",
    ),
    (
        "openai-recipes", "openai recipe: production-ready client setup",
        "```python\\nfrom openai import OpenAI, RateLimitError, APITimeoutError\\nimport httpx\\n\\nclient = OpenAI(\\n    timeout=httpx.Timeout(60.0, connect=5.0),\\n    max_retries=3,\\n    default_headers={\"User-Agent\": \"my-app/1.0\"},\\n)\\n\\ntry:\\n    resp = client.chat.completions.create(...)\\nexcept RateLimitError as e:\\n    # log + queue retry\\nexcept APITimeoutError as e:\\n    # log + maybe retry\\nexcept Exception as e:\\n    # log + alert\\n```. "
        "For observability: log token usage + latency per call.",
        f"{BASE}/guides/production-best-practices",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (tool_id, subject, statement, evidence_url) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-oa-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-oa-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="openai-catalog",
            evidence=[evidence],
            risk_level=RiskLevel.LOW,
            created_at=now,
        )
        try:
            store.create(claim)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ #{suffix} create: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        try:
            verification = orchestrator.verify_claim(claim)
            store.save_verification_result(verification)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ #{suffix} verify: {type(exc).__name__}: {exc}")
        text = claim_embedding_text(subject=subject, statement=statement)
        vec = svc.embed_one(text)
        store.set_embedding(claim.claim_id, serialize_embedding(vec))
        ok += 1
        print(f"  ✓ #{suffix} [{tool_id}] {subject[:70]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()
