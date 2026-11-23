# AI Code Review System

An agentic code review platform. Point it at a git repository, a local folder,
or a ZIP archive and seven specialised agents work through it in sequence —
identifying what the project *is*, mapping how it is organised, planning the
review, reading each file, hunting systemic issues, challenging their own
findings, and finally scoring and reporting.

Built on **FastAPI + LangGraph + React (JavaScript)**, running against the
**Groq** free tier with up to three API keys in rotation.

---

## The pipeline

```
ingest ─► profile_project ─► analyze_structure ─► plan_review ─► review_files
                                                                      │
                                     ┌────────────────────────────────┤
                                     ▼                                │
                        cross_cutting ─► critique ─► verify_findings  │
                                     │                     │          │
                                     └─────────────────────┴──────────┴─► synthesize ─► END
```

| # | Agent | What it produces |
|---|---|---|
| 1 | `ProjectProfiler` | Purpose, type, domain, stack, architecture, maturity |
| 2 | `StructureAnalyst` | Organising principle, layers, coupling, gaps, hotspots |
| 3 | `ReviewPlanner` | Which files to read, in what order, looking for what |
| 4 | `FileReviewer` | Per-file verdict, findings with line numbers, dimension scores |
| 5 | `SystemsAuditor` | Issues no single file can reveal |
| 6 | `Critic` | Adversarially rejects unsupported or duplicated findings |
| 7 | `ProofEngine` | Demonstrates findings — traced, executed, or dropped |
| 8 | `Synthesizer` | Executive summary, verdict, quick wins, strategic work |

Conditional edges skip work that cannot produce anything: no reviewable files
skips straight to synthesis, and the critic is bypassed when disabled or when
nothing was found.

---

## Why it is built this way

**The binding constraint is tokens-per-minute, not context.** Groq's free plan
gives `openai/gpt-oss-120b` a 131,072-token context window but only **8,000
tokens per minute per key**. A naive implementation stalls or 429s constantly.
Four mechanisms address it:

- **A rotating key pool** (`app/llm/key_pool.py`). Groq enforces limits per
  key, so up to three keys are rotated round-robin. A key that returns 429 is
  cooled down and skipped; the request immediately retries on another key
  rather than waiting. In practice a review that stalled ~31s on governor waits
  with one key runs with zero waits on three.
- **A rate governor** (`app/llm/rate_limiter.py`) enforces RPM, TPM, RPD and
  TPD as refilling token buckets *per key*, *before* dispatch, so requests
  queue instead of failing. Redis-backed when available so API and worker
  share one budget.
- **Two-tier model routing.** Small, low-risk files go to
  `llama-3.1-8b-instant` (15K TPM), preserving deep-tier budget for
  security-sensitive and large files. Anything matching auth/crypto/payment
  paths is always routed deep.
- **Batching and caching.** Files under 120 LOC are grouped into a single call;
  every response is cached by content hash, so re-reviewing an unchanged file
  is free.

**Scoring is deterministic.** The model supplies per-dimension judgements; the
arithmetic lives in `app/scoring/rubric.py`. Findings apply a
confidence-weighted penalty with diminishing returns and a hard ceiling, and
file scores are `sqrt(loc)`-weighted so one dramatic file cannot sink the
repository grade.

**The critic exists because false positives destroy trust.** Every
critical/high/medium finding is re-examined against its own quoted evidence and
discarded if it is not grounded.

**And the proof engine exists because a second opinion is still an opinion.**
Published false-positive rates for AI code review run 60–80%, and the only
technique that reliably fixes that is demonstrating the flaw. After the critic,
each critical and high finding is either:

* **traced** — established deterministically from the code (a live credential
  shape, a route with no auth in its chain, a password through a fast digest), or
* **executed** — the model designs an experiment, a sandbox runs it, and the
  verdict is read from *what the run printed*, never from the model's opinion
  of its own output, or
* **dropped** — the experiment ran and the flaw did not occur.

A demonstrated critical caps the grade, so a report can never say "do not
launch" beside a passing score. Proof scripts that try to edit the code under
test are rejected: proving a bug by writing it proves nothing.

---

## Quick start

### Option A — no Docker (SQLite + in-process worker)

```bash
git clone <this repo> && cd code-review
cp .env.example .env          # then add your GROQ_API_KEY
# in .env, use the SQLite fallback:
#   DATABASE_URL=sqlite+aiosqlite:///./codereview.db
#   REDIS_URL=

cd backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Without Redis the API runs reviews as in-process
background tasks — no separate worker needed.

### Option B — Docker (Postgres + Redis + worker)

```bash
cp .env.example .env          # add GROQ_API_KEY; keep the Postgres/Redis URLs
docker compose up --build
```

Brings up Postgres, Redis, the API (running migrations first), a dedicated
worker, and the Vite dev server.

Get a free Groq key at <https://console.groq.com/keys>.

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | — | Required |
| `GROQ_API_KEY_2`, `GROQ_API_KEY_3` | — | Optional extra keys; each one multiplies the ceiling |
| `GROQ_API_KEYS` | — | Comma-separated alternative to the numbered vars |
| `GROQ_KEY_STRATEGY` | `round_robin` | `round_robin`, `random` or `least_used` |
| `GROQ_KEY_COOLDOWN_SEC` | `20` | Sit-out after a 429 with no `Retry-After` |
| `GROQ_MODEL_DEEP` | `openai/gpt-oss-120b` | 131,072 ctx · 65,536 max completion |
| `GROQ_MODEL_FAST` | `llama-3.1-8b-instant` | Triage tier, higher TPM |
| `GROQ_DEEP_TPM` | `8000` | **Per key.** Also caps a *single* request: prompt + completion must fit inside it |
| `GROQ_REASONING_EFFORT` | `low` | gpt-oss bills its reasoning trace as completion tokens; raise for subtler findings |
| `DATABASE_URL` | SQLite | `postgresql+asyncpg://…` in Docker |
| `REDIS_URL` | empty | Empty = in-process queue, bus and limiter |
| `MAX_REPO_MB` | `200` | Rejects oversized repos and zip bombs |
| `MAX_FILES` | `4000` | Inventory cap |
| `MAX_CONCURRENT_FILE_REVIEWS` | `3` | Raise only if you raise the TPM ceiling |

Free-tier ceilings as published by Groq — **these are per key**, so three
keys triple every column:

| Tier | Model | RPM | TPM | RPD | TPD |
|---|---|---:|---:|---:|---:|
| deep | `openai/gpt-oss-120b` | 30 | 8,000 | 1,000 | 200,000 |
| fast | `llama-3.1-8b-instant` | 30 | 15,000 | 14,400 | 500,000 |

`GET /api/v1/keys` reports each key's live state (available / cooling /
exhausted) and today's spend. Only non-reversible fingerprints such as
`key2:f4f6d2` are ever returned or logged — never the secrets.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/reviews` | Start a review of a git URL or local path |
| `POST` | `/api/v1/reviews/upload` | Start a review of an uploaded ZIP |
| `GET` | `/api/v1/reviews` | History |
| `GET` | `/api/v1/reviews/{id}` | Full report |
| `GET` | `/api/v1/reviews/{id}/stream` | **SSE** live agent trace |
| `GET` | `/api/v1/reviews/{id}/events` | Persisted trace (replay) |
| `GET` | `/api/v1/reviews/{id}/findings` | Filter by severity/category |
| `GET` | `/api/v1/reviews/{id}/report.md` | Markdown export |
| `POST` | `/api/v1/reviews/{id}/cancel` | Cancel a running review |
| `DELETE` | `/api/v1/reviews/{id}` | Delete |
| `GET` | `/api/v1/health` · `/budget` | Status and pool-wide token budget |
| `GET` | `/api/v1/keys` | Per-key health, rotation count and daily spend |

Interactive docs at `/docs`.

```bash
curl -X POST localhost:8000/api/v1/reviews \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"git","source_ref":"https://github.com/psf/requests",
       "options":{"max_files_to_review":20,"focus_areas":["security"]}}'
```

SSE carries `Last-Event-ID`, so a reconnecting client replays only what it
missed.

---

## Layout

```
backend/app/
  agents/       seven agents + LangGraph wiring + shared context
  llm/          Groq client, rotating key pool, rate governor, cache, tokens
  ingestion/    source resolution (git/local/zip), walker, language detection
  scoring/      deterministic rubric
  tools/        AST metrics, complexity, secret scanning
  api/v1/       REST + SSE + Markdown renderer
  workers/      runner, queue, worker entrypoint
  db/           SQLAlchemy models and session management
frontend/src/            plain JavaScript + JSX, no TypeScript
  pages/        NewReview · ReviewPage · History
  components/   AgentTrace, PhaseRail, FindingCard, FileReportCard, primitives
  hooks/        useReviewStream (SSE with replay + de-duplication)
```

---

## Security

Reviewing arbitrary user-supplied code means the ingestion layer is a trust
boundary. It enforces:

- **Zip-slip and absolute-path rejection**, plus an uncompressed-size ceiling
  (zip-bomb guard).
- **Scheme and host validation** on git URLs; only `http(s)` is accepted.
- **PATs are never persisted or logged** — injected into the clone URL for the
  clone only, and redacted from any error output.
- **Local directories are never deleted** on cleanup; only workspaces the
  system created are ephemeral.
- `git clone` runs with `GIT_TERMINAL_PROMPT=0` and a fixed `PATH`, shallow and
  single-branch, under a timeout.
- **Groq keys are never returned by the API or written to logs.** The pool
  identifies them by a truncated SHA-256 fingerprint; a key rejected with
  401/403 is dropped from rotation rather than retried.

These paths are covered by tests in `backend/tests/test_source_security.py`.

---

## Development

```bash
cd backend
make test          # 94 tests
make lint          # ruff
make migrate       # alembic upgrade head
make revision m="add x"

cd ../frontend
npm run lint       # eslint
npm run build
```

## Working within the free tier

Three constraints shape every prompt budget in this system, all learned the
hard way against the live API:

1. **A single request must fit one TPM window.** Groq counts prompt +
   completion against the 8,000/min limit and rejects anything larger outright
   ("Limit 8000, Requested 8870"). The client therefore clamps
   `max_completion_tokens` to what is left after the prompt, and refuses with a
   clear message if the prompt alone will not fit.
2. **Reasoning models bill their reasoning.** `gpt-oss` spends completion
   tokens thinking before it answers, so `GROQ_REASONING_EFFORT` defaults to
   `low`.
3. **Plain JSON mode is unreliable on reasoning models.** They return
   `json_validate_failed`. Agents use strict `json_schema` (constrained
   decoding) instead, falling back to schema-guided output plus tolerant
   Pydantic parsing when the model still omits a field.

Diagnose any provider problem with:

```bash
cd backend && ./.venv/bin/python -m app.llm.diagnose
```

## Known limits

- A review of 40 files takes roughly 15–25 minutes on a single free key. That
  is the 8K TPM ceiling, not the pipeline. Adding a second and third key
  divides that time by roughly the number of keys.
- Findings are only as good as the evidence in a single file plus the digest
  the systems auditor sees; there is no whole-repo call graph.
- Very large files are truncated head-and-tail to fit the per-file budget.
