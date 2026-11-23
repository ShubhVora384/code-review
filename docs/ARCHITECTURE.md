# Architecture

## Design principles

**1. Every agent has one job and a typed output contract.**
Agents never return free text. Each declares a Pydantic model in
`app/schemas/agent.py`, which is converted to JSON Schema and injected into the
system prompt. `GroqLLM.structured()` validates the reply and, on failure,
issues a single cheap repair round-trip rather than re-running the whole turn —
which matters when the budget is 8,000 tokens per minute.

**2. Orchestration is explicit, not emergent.**
There is no agent deciding which agent runs next. The LangGraph in
`app/agents/graph.py` fixes the sequence; conditional edges only *skip* work
that cannot produce output. This makes runs reproducible and the trace
legible, and it caps token spend at a knowable ceiling.

**3. The LLM judges; the code computes.**
Scores come from the model per dimension, but all arithmetic —
weighting, penalties, aggregation, grading — lives in
`app/scoring/rubric.py`. Asking a model to average numbers is how you get
scores that drift between runs.

**4. Facts are gathered locally and handed to the model.**
`app/tools/static_analysis.py` computes cyclomatic complexity, function
lengths, bare excepts, mutable default arguments and secret patterns via AST
and regex. These go into the prompt as verified facts, so the model spends its
attention on judgement rather than counting.

**5. The reviewer is adversarially checked.**
The `Critic` agent sees each finding with its own quoted evidence and rejects
anything not grounded in it. Rejected findings are never persisted.

---

## State flow

`ReviewState` is a `TypedDict` threaded through the graph. Each node returns a
partial dict that LangGraph merges.

```
ingest          → inventory
profile_project → profile
analyze_structure → structure
plan_review     → plan
review_files    → file_rows, all_findings   (findings get stable `_id`s here)
cross_cutting   → cross, all_findings       (appends `x-N` ids)
critique        → kept_findings             (a filtered subset)
synthesize      → synthesis, scores, metrics, file_rows, kept_findings
```

Findings carry a stable `_id` from the moment they are created so the critic
can reject them individually and `save_results` can match survivors back to
their file. Anything not in `kept_findings` is simply never written.

> **Node names must not collide with state keys.** LangGraph rejects a node
> named `profile` when `profile` is also a state field — hence
> `profile_project`, `analyze_structure`, `plan_review`.

---

## The token budget problem

At 8,000 TPM, a single 3,500-token file review consumes 26 seconds of budget.
Reviewing 40 files naively takes far longer than it needs to. Mitigations, in
order of impact:

| Mechanism | Where | Effect |
|---|---|---|
| Key rotation | `app/llm/key_pool.py` | N keys multiply every limit by N |
| Batching small files | `FileReviewerAgent._partition` | Up to 5 files under 120 LOC per call |
| Fast-tier routing | `FileReviewerAgent._tier_for` | Low-risk files use the 15K TPM model |
| Content-hash caching | `app/llm/cache.py` | Unchanged file re-reviews cost nothing |
| Pre-dispatch reservation | `app/llm/rate_limiter.py` | Queues instead of 429-ing |
| Head-and-tail truncation | `app/llm/tokens.py` | Keeps exports and main guards, not just the top |

The governor reserves an *estimated* token count before dispatch, then
`settle()` reconciles against the real usage returned by the API. Estimation
deliberately biases high (3.0 chars/token for code) so it over-reserves rather
than under-reserves.

---

## The key pool

Groq applies its free-tier limits **per API key**, so the governor namespaces
every bucket by key id (`crg:{key_id}:{tier}:tok`). Three keys therefore give
three independent 8K-TPM budgets rather than one shared ceiling.

`KeyPool.select()` runs on every call:

1. Build the candidate list — drop keys that are cooling, daily-exhausted, or
   already tried on *this* request.
2. Order them by strategy: `round_robin` (default, rotates the head each call),
   `random`, or `least_used` (fewest tokens spent today).
3. Walk that order and return the first key whose governor grants budget
   immediately. If a key's daily cap has gone, mark it exhausted and continue.
4. If nothing is free, sleep until the soonest key frees up.

On a 429, `_dispatch` calls `penalize()`, which sets a cooldown from the
`Retry-After` header — multiplied by that key's consecutive-failure count, so a
persistently bad key backs off further — and immediately retries on another
key. **A 429 costs one retry, not a stall**, as long as another key is free.

Two failure modes are distinguished deliberately:

* `AllKeysBusyError` — every key is rate limited *right now*. Transient.
* `BudgetExhaustedError` — every key has spent its daily cap. Resets 00:00 UTC.

A key rejected with 401/403 is dropped from rotation immediately rather than
retried, since a bad credential will never succeed.

Rotations surface in the agent trace through `rotation_sink`, a ContextVar set
per review — the same pattern as `usage_sink` — so the UI shows *"Rate limit on
key1:3607c9 — switched to key2:f4f6d2"* rather than an invisible retry.

Keys are identified everywhere by a truncated SHA-256 fingerprint
(`key2:f4f6d2`). The secret never leaves `ApiKey.secret`, is excluded from
`repr`, and appears in no log line, API response, or database row.

---

## Streaming

`ReviewContext.emit()` does three things atomically per event: assigns a
monotonic `seq`, writes a `ReviewEvent` row, and publishes to the bus. That
persistence is what makes the SSE endpoint resumable — a client reconnecting
with `Last-Event-ID: 42` replays rows 43+ from the database and then joins the
live stream.

The endpoint subscribes to the bus *before* querying the backlog, so an event
landing between the two is duplicated rather than lost; the client de-duplicates
on `seq`.

`EventBus` uses Redis pub/sub when configured — required when the worker is a
separate process — and in-process `asyncio.Queue` fan-out otherwise.

---

## Adding an agent

1. Define its output contract in `app/schemas/agent.py`.
2. Write its prompt in `app/agents/prompts.py`. Demand evidence; explicitly
   permit an empty result.
3. Subclass `BaseAgent`, implement `run()`, call `self.ask(...)`.
4. Add a node function in `graph.py` that calls it, emits a trace event, and
   returns its slice of state.
5. Register the node and its edges in `build_graph()` — **name it something
   that is not already a state key**.
6. Add its phase to `PHASE_PROGRESS` and `PHASE_LABEL` in `context.py`, and to
   `PHASE_SEQUENCE` in the frontend's `lib/format.js`.

## Swapping the LLM provider

`app/llm/client.py` and `app/llm/key_pool.py` are the only modules that import
`groq`. The client exposes `text()` and `structured()`. A different provider
needs a new `_dispatch` implementation and an `ApiKey.client` factory; the
governor, cache, key rotation logic and token estimator are all
provider-agnostic.
