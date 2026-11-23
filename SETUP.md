# Setup

Everything below assumes the project directory `~/projects/code-review`.

On **this machine** the Python venv and npm packages are already installed, so
you only need Step 1 and Step 2. Steps marked *(fresh machine)* are for a
clone onto a different computer.

---

## Step 1 — Get your Groq API keys

1. Go to <https://console.groq.com/keys> and sign in (free, no card).
2. Click **Create API Key**, name it anything, copy the value.
3. Repeat until you have **three** keys.

> Groq's free limits are enforced **per key**, so three keys give you three
> times the throughput. One key works fine — the review is just slower.
>
> A key is shown **once**. Copy it before closing the dialog.

---

## Step 2 — Paste the keys into `.env`

Open `~/projects/code-review/.env` and fill in the three lines near the top:

```bash
GROQ_API_KEY=gsk_your_first_key_here
GROQ_API_KEY_2=gsk_your_second_key_here
GROQ_API_KEY_3=gsk_your_third_key_here
```

Nothing else in `.env` needs changing. Leave `GROQ_API_KEY_2` / `_3` blank if
you only made one key.

Editor of your choice:

```bash
cd ~/projects/code-review
nano .env          # or: code .env
```

---

## Step 3 *(fresh machine)* — Install dependencies

```bash
cd ~/projects/code-review/backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cd ../frontend
npm install
npm approve-scripts esbuild     # only if npm warns about blocked scripts
```

---

## Step 4 — Run it (two terminals)

**Terminal 1 — backend API**

```bash
cd ~/projects/code-review/backend
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

Wait for:

```
startup.ready  api_keys=3  key_strategy=round_robin  deep_model=openai/gpt-oss-120b
Application startup complete.
```

`api_keys=3` confirms all three keys were picked up. If it says `api_keys=0`,
your keys did not load — see Troubleshooting.

**Terminal 2 — frontend**

```bash
cd ~/projects/code-review/frontend
npm run dev
```

**Then open <http://localhost:5173>**

The database file and workspace directory are created automatically on first
run. No migration command is needed in SQLite mode.

---

## Step 5 — Confirm the keys and models work

```bash
cd ~/projects/code-review/backend
./.venv/bin/python -m app.llm.diagnose
```

This makes one real call per key, in the exact mode the agents use, and prints
what Groq says. Run it first whenever a review fails — it separates a key or
model problem from anything in the pipeline.

```
key1:97cec1
   ok   deep  strict schema  openai/gpt-oss-120b   1140 tokens · schema validated
   ok   fast  strict schema  openai/gpt-oss-20b    1275 tokens · schema validated
key2:03bc41
   FAIL deep  strict schema  openai/gpt-oss-120b   HTTP 400 [organization_restricted] — …
```

A dead key is not fatal: the pool drops it and reviews continue on the rest.

## Step 5b — Confirm the keys are live

```bash
curl -s localhost:8000/api/v1/keys | python3 -m json.tool
```

Expected:

```json
{
  "total": 3,
  "available": 3,
  "cooling": 0,
  "exhausted": 0,
  "strategy": "round_robin",
  "keys": [ { "id": "key1:a3f9c1", "state": "available", ... } ]
}
```

Only fingerprints are returned — your secrets are never exposed by the API.

The header of the web UI shows the same thing: one dot per key, green for
available, amber for cooling after a 429, red for daily-exhausted.

---

## Step 6 — Run your first review

In the UI: paste a repository URL, or switch to **Local folder** and give an
absolute path, or drag in a ZIP. Start with something small — 10–15 files.

Or from the command line:

```bash
curl -X POST localhost:8000/api/v1/reviews \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"git",
       "source_ref":"https://github.com/psf/requests",
       "options":{"max_files_to_review":10,"focus_areas":["security"]}}'
```

Reviewing this project itself is a good first test:

```bash
curl -X POST localhost:8000/api/v1/reviews \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"local",
       "source_ref":"'"$HOME"'/projects/code-review/backend",
       "options":{"max_files_to_review":10}}'
```

---

## Everyday commands

| What | Where | Command |
|---|---|---|
| Start API | `backend/` | `./.venv/bin/uvicorn app.main:app --reload --port 8000` |
| Start UI | `frontend/` | `npm run dev` |
| Run tests | `backend/` | `./.venv/bin/python -m pytest tests/ -q` |
| Lint backend | `backend/` | `./.venv/bin/ruff check app tests` |
| Lint frontend | `frontend/` | `npm run lint` |
| Production build | `frontend/` | `npm run build` |
| API docs | browser | <http://localhost:8000/docs> |
| Key status | shell | `curl -s localhost:8000/api/v1/keys \| python3 -m json.tool` |
| Budget used today | shell | `curl -s localhost:8000/api/v1/budget \| python3 -m json.tool` |
| Reset everything | `backend/` | `rm -f codereview.db && rm -rf .workspaces` |

`make` shortcuts also exist in `backend/`: `make dev`, `make test`, `make lint`.

---

## Troubleshooting

**Any review failure — start here**
```bash
cd ~/projects/code-review/backend && ./.venv/bin/python -m app.llm.diagnose
```
The UI also shows the provider's own error text under the failure banner, plus
an error code such as `schema_rejected` or `daily_budget_exhausted`.

**`api_keys=0` at startup, or the UI shows "No Groq API key set"**
The key was not recognised. Keys must start with `gsk_` and be longer than 20
characters; the literal placeholder `gsk_replace_me` is ignored on purpose.
Check for quotes or trailing spaces — write `GROQ_API_KEY=gsk_abc123`, not
`GROQ_API_KEY="gsk_abc123"`.

**`organization_restricted` on one key**
That Groq account has been blocked — nothing to do with this app. Generate a
replacement key from a working account. Reviews keep running on your other keys.

**Files show as "failed" inside an otherwise successful review**
Every planned file always gets a row, so the reason is on the row itself.
Common causes: the daily token budget ran out mid-run, or the file was too
large to fit a single per-minute window.

**Reviews are slow**
Expected on the free tier: roughly 8,000 tokens per minute per key. Add the
second and third key, or lower **Files to review** in the UI. Watch the key
dots in the header — amber means a key is cooling after a 429, which the
system handles by rotating to another.

**`Every configured Groq key is rate limited`**
All keys were throttled at once. Reduce concurrency in `.env`:
`MAX_CONCURRENT_FILE_REVIEWS=1`, then restart the API.

**`All Groq keys have spent their daily free-tier budget`**
You have used the daily allowance (1,000 requests / 200K tokens per key).
It resets at 00:00 UTC.

**Port already in use**
```bash
lsof -ti:8000 | xargs -r kill     # API
lsof -ti:5173 | xargs -r kill     # UI
```

**Frontend loads but every request fails**
The backend is not running, or is not on port 8000. Vite proxies `/api` to
`http://localhost:8000` — start Terminal 1 first.

**`npm run dev` fails with an esbuild error**
```bash
cd frontend && npm approve-scripts esbuild
```

---

## Later: switching to Postgres + Redis

Not required — the SQLite fallback is fully functional. Do this when you want
the API and worker as separate processes, or persistence across machines.

Docker Desktop is installed but **WSL integration is off** for this distro, so
`docker` is not currently usable here. Enable it in
*Docker Desktop → Settings → Resources → WSL Integration*, tick this distro,
then Apply & Restart. Verify with `docker info`.

Then edit `.env`:

```bash
DATABASE_URL=postgresql+asyncpg://codereview:codereview@localhost:5432/codereview
REDIS_URL=redis://localhost:6379/0
```

And run the whole stack — Postgres, Redis, API, worker and UI:

```bash
cd ~/projects/code-review
docker compose up --build
```

Compose runs `alembic upgrade head` automatically. With Redis present, reviews
are dispatched to the dedicated worker container and the rate-limit budget is
shared across processes rather than held in memory.
