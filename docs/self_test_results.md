# Solo self-test results (Stage 20.9 / Gate 1 criterion 3)

This file records the outcome of the maintainer self-test against the
v0.2 bulk-ingest knowledge graph. It's the final criterion of Gate 1
in [plan_v02.md](../plan_v02.md): **≥ 7 of 10 dev questions produce a
verdict the maintainer would accept as useful**.

## Run — 2026-05-25

**Graph state at run time.** Post-Stage-20.8 bulk DB
([backend/ayiru_v0.2_bulk.db](../backend/ayiru_v0.2_bulk.db)): 82
claims across 40 distinct tools. 5 tools curated to depth (docker,
git, github-cli, vercel-cli, openai-api); 35 tools with one thin
index-page claim each from the bulk crawl.

**Method.** The self-test was executed as the Stage 22.1 demo
notebook ([`clients/python/examples/langchain_demo.ipynb`](../clients/python/examples/langchain_demo.ipynb)),
driving the same 10 questions through `Ayiru.ask` via the LangChain
adapter — that's the canonical agent-facing path. The notebook splits
results into USEFUL (passes `Answer.is_useful`), WEAK (server matched
but below the threshold), and MISS (server fallback).

**Result.** 7 of 10 questions matched the graph (server-side
`fallback_recommended=False`) but **only 2 produced USEFUL answers
under the strict `is_useful` heuristic**. The remaining 5 matches
came from the bulk-ingest tools whose single index-page claim
returned at low confidence (0.35–0.45) and L1/L2 verification.

| # | question | tool | server match | confidence | level | verdict |
|---|---|---|---|---|---|---|
| 1 | how do I delete a github repo with gh | github-cli | hit | 1.00 | L2_source_verified | USEFUL ✓ |
| 2 | what does git log do | git | hit | 0.69 | L2_source_verified | USEFUL ✓ |
| 3 | how do I list docker volumes | docker | hit | 0.35 | L1_schema_valid | WEAK |
| 4 | how do I authenticate with the openai api | openai-api | hit | 0.40 | L2_source_verified | WEAK |
| 5 | what does kubectl describe pod do | kubectl | hit | 0.45 | L2_source_verified | WEAK |
| 6 | how do I install a helm chart | helm | hit | 0.45 | L2_source_verified | WEAK |
| 7 | how do I install a package with apt | apt | hit | 0.45 | L2_source_verified | WEAK |
| 8 | how do I configure my ergonomic keyboard | — | fallback | — | — | MISS |
| 9 | what is the best programming language for embedded systems | — | fallback | — | — | MISS |
| 10 | what is the airspeed velocity of an unladen swallow | — | fallback | — | — | MISS |

**Score.**

- By the lax criterion (server fallback): **7/10**.
- By the strict criterion (`Answer.is_useful`): **2/10**.

## Verdict on Gate 1 criterion 3

The Stage 16.4 / 20.9 criterion is *"≥ 7 of 10 realistic dev questions
produce a verdict the maintainer would accept as useful."* The word
"useful" is doing the load-bearing work here. Reading the criterion
strictly against `Answer.is_useful`, **the score is 2/10 and Gate 1
criterion 3 is NOT cleared**.

The failure mode is concentrated and known: the 35 bulk-ingest tools
each have one thin index-page claim. Adding per-command URLs to
[`tools/v0.2_seed_keep.json`](../tools/v0.2_seed_keep.json) (e.g.
`kubectl describe`, `kubectl logs`, …) and re-running
`ayiru ingest --resume` is the planned v0.2.x depth work. The
infrastructure (CLI, contracts, robots, rate limit, lockstep mirrors,
SDK, LangChain adapter) is all production-ready — the gap is graph
content, not code.

## Recommendation

Treat v0.2 as **build cycle complete, depth pass pending**:

- v0.2.5 publish work (PyPI, hosted demo, README pivot, OSS hygiene)
  is deferrable until the depth pass lands.
- Either:
  - **(A) Ship now with honest framing** — README's "Coverage today"
    section accurately describes the 5-curated + 35-thin reality, and
    early adopters get the agent-framework integration today while we
    expand depth on a rolling basis.
  - **(B) Hold launch until depth pass clears 7/10 strict** — adds
    1–2 weeks of `seed_keep.json` expansion + bulk crawl re-runs but
    the launch-day demo would show a clean 7+/10 useful.

The "build > publish" preference and the empirical evidence that the
infrastructure works argue for (A). Track the depth pass as the first
v0.2.x milestone.

---

## Run — 2026-05-27 (depth-pass attempt #1)

**Graph state at run time.** Same bulk DB after the depth pass on 5
high-traffic tools (kubectl, helm, terraform, pip, ansible). Each
swapped its single index URL for 6 per-command URLs. Claim count
**82 → 112** (+30 per-command claims). Failures during crawl: 1
(gcloud, the known max_bytes case carried over from Stage 20.8).

**Method.** Same 10-question demo notebook from Stage 22.1, no code
changes. SDK shipped a small P0 fix in parallel: `_RiskLevel` Literal
now accepts `"none"` (caught by the second-pass audit).

**Result.** Same headline numbers as run #1: **USEFUL=2 / WEAK=5 / MISS=3**.

| # | question | tool | top subject (NEW) | confidence | verdict | change vs run #1 |
|---|---|---|---|---|---|---|
| 1 | how do I delete a github repo with gh | github-cli | gh repo delete | 1.00 | USEFUL ✓ | unchanged |
| 2 | what does git log do | git | git log | 0.69 | USEFUL ✓ | unchanged |
| 3 | how do I list docker volumes | docker | docker-compose.yml: volumes | 0.35 | WEAK | matched a different docker claim |
| 4 | how do I authenticate with the openai api | openai-api | POST /embeddings | 0.40 | WEAK | matched a different openai claim |
| 5 | what does kubectl describe pod do | kubectl | **kubectl describe** | 0.45 | WEAK | **now per-command** (was: kubectl index) |
| 6 | how do I install a helm chart | helm | **helm install** | 0.45 | WEAK | **now per-command** (was: helm index) |
| 7 | how do I install a package with apt | apt | apt | 0.45 | WEAK | unchanged (no depth pass) |
| 8 | how do I configure my ergonomic keyboard | — | — | — | MISS | unchanged |
| 9 | what is the best programming language for embedded systems | — | — | — | MISS | unchanged |
| 10 | what is the airspeed velocity of an unladen swallow | — | — | — | MISS | unchanged |

### Key finding: depth worked at the matching layer, not the confidence layer

The depth pass surfaced **per-command** claims for kubectl and helm
(rows 5 and 6) — the matcher now returns the canonical
`kubectl describe` claim and the canonical `helm install` claim
instead of the generic index page. **Answer quality is meaningfully
better**, but the token-overlap score still clusters at ~0.45, below
the `is_useful` threshold of 0.6.

### Next bottleneck: matcher calibration, not graph depth

`_ASK_SCORE_THRESHOLD = 0.30` in
[backend/app/services/query_engine.py](../backend/app/services/query_engine.py)
admits an answer to the response set; `Answer.is_useful` in the SDK
gates at `≥ 0.6`. The 0.30–0.60 band is the matcher's "found something
that probably matches" zone, where the per-command claims now sit.

To clear Gate 1 strictly, one of the following is needed (v0.2.x work,
out of scope for this depth-pass plan):

1. **Phrase-exact subject boost** — when a question contains the
   subject as a contiguous substring (e.g. "kubectl describe" appears
   in "what does kubectl describe pod do"), boost the confidence by
   a calibration constant. Cheap to implement; addresses the headline
   failure mode directly.
2. **Recalibrate the `is_useful` threshold** — Stage 18 telemetry
   was designed to enable exactly this. With real per-query confidence
   distributions in hand, the 0.6 floor can be re-derived empirically
   instead of guessed.
3. **Hybrid retrieval (embeddings)** — the planned Stage 22-stretch
   work. Would re-rank lexical candidates by semantic similarity.

### Verdict on Gate 1 criterion 3

Still **NOT cleared by the strict heuristic** (2/10 USEFUL).

The depth-pass plan's stop conditions said "if USEFUL is still < 5/10
after the crawl, don't iterate inside this plan; record the result,
ship the partial improvement, and queue a follow-up plan." That's
this section.

The follow-up should target option (1) above — phrase-exact subject
boost — because:
- It's a self-contained matcher change with a small surface area.
- It addresses the observed failure mode (right claim, low score).
- It's testable with the existing self-test harness.

After the matcher tweak, this same 10-question demo should run again.
If USEFUL ≥ 7, Gate 1 closes. If not, the next bottleneck is harder
(hybrid retrieval) and the launch / depth-pass trade-off resurfaces.

### Side wins from this run

- **30 new per-command claims** in the graph; future questions about
  `kubectl get`, `helm upgrade`, `terraform apply`, etc. now have
  canonical answers to surface even if confidence calibration is
  pending.
- **SDK P0-1 fixed** — `risk_level="none"` no longer crashes the
  client.
- **Lockstep mirrors validated** — depth-pass mutation kept
  `contracts/` and `backend/app/contracts/` byte-identical, as
  required by the Stage 14 lockstep contract.
