# RepoAudit

Automated repository quality auditing across five dimensions of software quality.

Paste a public GitHub URL, get back a scored, structured audit — no setup, no login, no cost.

**Live demo:** [repoaudit-dko0.onrender.com](https://repoaudit-dko0.onrender.com)

---

## What it does

Submit any public GitHub repository URL → RepoAudit clones the repo → runs five independent analysis pillars → returns a scored, structured report you can read in the browser, export as JSON/HTML, or download as a PDF.

### The five pillars

| # | Pillar | What it checks | Tooling |
|---|--------|-----------------|---------|
| 1 | **Code Evaluation** | Quality, complexity, duplication, code smells, idiom adherence | `ruff` + `radon` (Python), `eslint` (JS/TS) |
| 2 | **Security** | Vulnerable dependencies, hardcoded secrets, unsafe patterns, attack surface | Semgrep, Trivy, `npm audit`, Bandit |
| 3 | **Documentation** | README quality, setup instructions, inline comments, API/interface coverage | Heuristic checks |
| 4 | **Production Readiness** | CI/CD presence, error handling, logging, licensing, containerization | Heuristic checks |
| 5 | **Semantic Analysis** | Purpose, architecture, module boundaries, key dependencies | LLM (Groq, structured JSON) |

Each pillar produces a 0–100 score and a list of specific findings (file, line, message, severity). The overall verdict is the average of every pillar that completed or partially completed — pillars that failed or timed out are excluded from the average, never counted as zero.

---

## Quick start

```bash
git clone https://github.com/HassanSep21/repoaudit.git
cd repoaudit
cp .env.example .env
# fill in GROQ_API_KEY and GITHUB_TOKEN at minimum
docker compose up --build
```

Visit `http://localhost:8000`.

Local dev uses SQLite automatically — no external database required.

### Required environment variables

| Variable | Required | Notes |
|---|---|---|
| `GROQ_API_KEY` | Yes (for Semantic Analysis) | Free tier, from [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | Yes (for Semantic Analysis) | e.g. `openai/gpt-oss-120b` — confirmed free-tier |
| `GITHUB_TOKEN` | Effectively required | Unauthenticated GitHub API is 60 req/hr — exhausted quickly during normal use |
| `DATABASE_URL` | Local: no. Deployed: yes | Points at a Postgres instance (Supabase free tier used in production) |
| `SECURITY_PILLAR_TIMEOUT` | No | Defaults to 180s — Trivy's filesystem scan is the slowest step in the pipeline |

### Running one pillar in isolation

Every pillar module has a standalone CLI entry point, useful for iterating without running the full async pipeline:

```bash
python -m app.pillars.code_evaluation --path /tmp/some-cloned-repo
```

### Tests

```bash
pytest backend/tests
```

`tests/fixtures/` holds small local repos (clean, has-issues, malformed/empty, non-UTF8) used for the robustness suite.

---

## Understanding your results

### Tier-1 vs. Tier-2 ("best-effort")

RepoAudit detects each repo's primary language via the GitHub API. Python and JavaScript/TypeScript repos get **Tier-1** analysis — real linters and scanners run. Any other language falls back to **Tier-2**: language-agnostic structural checks only (`cloc` for composition, README/tests/CI/LICENSE/Dockerfile presence). Tier-2 results are marked with a badge and should be read as a starting point, not a comprehensive audit.

### Pillar statuses

Every pillar ends in one of three states:

- **Complete** — full analysis ran, score and findings are reliable.
- **Partial** — ran but degraded (Tier-2 fallback, file-count limit hit, truncated output). The pillar's summary explains why.
- **Failed** — crashed or timed out. No score, excluded from the overall average.

The top of every report shows `pillars_completed` (e.g. `4/5`) — this only counts pillars that reached `complete` or `partial`. If you see fewer than 5/5, check the failed pillar's summary for the reason; one slow tool never takes the whole report down.

---

## Architecture

```
User pastes GitHub URL
        │
        ▼
  POST /analyze  →  202 { run_id }              (async job, not blocking)
        │
        ▼ (background task)
  Validate URL → size/archive pre-checks → git clone (depth 1, blob-limited)
        │
        ▼
  Run 5 pillars SEQUENTIALLY, one run in flight per process
        │
        ▼
  Aggregate score → persist to Postgres
        │
        ▼
  Frontend polls GET /analysis/{run_id} until complete/partial/failed
```

- **Frontend:** server-rendered Jinja2 + vanilla JS (polling for async results)
- **Backend:** FastAPI (Python 3.11), fully async — `asyncio.Lock`, `asyncio.create_task`, `asyncio.wait_for` for timeouts
- **Storage:** Postgres via Supabase (deployed), SQLite (local dev) — same schema either way
- **Analysis:** pillars run sequentially, one run per process, guaranteed temp-dir cleanup, per-pillar and overall pipeline timeouts
- **Tooling in the container:** `ruff`, `radon`, `bandit`, `semgrep`, `trivy`, `cloc`, Node 20 + `eslint`/`npm audit`, Playwright/Chromium for PDF export

### API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/analyze` | Start an analysis. `{ "url": "...", "confirm": false }` → `202 { run_id }`. `409` if the repo contains flagged archive files (resubmit with `confirm: true`). `429` if another run is in progress. |
| `GET` | `/analysis/{run_id}` | Poll for status/results. |
| `GET` | `/analysis/{run_id}/export.json` | Full structured export. |
| `GET` | `/analysis/{run_id}/export.html` | Self-contained static report — works offline. |
| `GET` | `/analysis/{run_id}/export.pdf` | Print-formatted PDF report. |
| `GET` | `/health` | Health check. |

---

## Limits

| Limit | Default | Behavior on breach |
|---|---|---|
| Max repo size | 500 MB | Rejected before clone |
| Archive files (zip-bomb risk) | any archive >5 MB | `409`, requires explicit `confirm: true` |
| Per-pillar timeout | 60s (180s for Security) | Marked `failed`/`partial`, pipeline continues |
| Overall pipeline timeout | 5 min | Returns whatever pillars completed |
| Concurrent runs | 1 per process | New submission gets `429` |

---

## What's explicitly out of scope

- No user accounts, login, or auth of any kind
- No private repo support — public GitHub URLs only
- No multi-repo comparison view
- No guaranteed permanent report history — persistence is best-effort, not a promised archive
- No per-language bespoke analyzers beyond the Tier-1/Tier-2 split
- **The analyzed repo's own code is never executed, built, or has its dependencies installed, under any circumstance.** Static analysis only — this is a hard boundary.

---

## Known limitations

- Semantic Analysis prompts a capped set of files (≤10 files + README) to stay within request-size limits — very large repos get a partial architectural picture, not a full-codebase read.
- Free-tier Render (0.1 vCPU / 512MB) means cold starts (~30–60s after 15 min idle) and Security-pillar runtimes that vary under load.
- No rate limiting beyond the single-run lock — the app is single-tenant by design, not hardened against abuse.

---

## Project structure

```
repoaudit/
  backend/
    app/
      main.py                 # FastAPI app, routes
      api/                    # (analysis, export routes)
      pillars/                # one module per pillar + shared base classes
      llm/                    # LLMProvider abstraction (Groq, optional Ollama)
      pipeline/                # orchestrator, repo fetcher
      models/                  # pydantic + ORM schema
      db/                      # session/engine (Postgres or SQLite)
      export/                  # HTML/JSON/PDF export
      templates/                # Jinja2 (UI + export + PDF report)
      static/                   # CSS/JS, favicon
    tests/
    Dockerfile
    requirements.txt
  docker-compose.yml
  .env.example
```

---

## Export formats

- **HTML** — static, self-contained, renders fully offline
- **JSON** — full structured data for programmatic use
- **PDF** — print-formatted report, distinct layout from the live UI (fully expanded findings, page-break-aware)

---

## License

MIT
