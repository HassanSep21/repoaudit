# RepoAudit — Architecture

Reflects all decisions in `01_DECISIONS.md`, including D14–D21 added during the adversarial audit. This is the shape to build toward — see `03_BUILD_PLAN.md` for what exists after each phase.

> **Revision note:** this file changed substantially after the audit. The biggest changes: analysis is now an explicit async job (D15), storage is Postgres by default (D14), pillar execution is sequential with a single-run lock (D16), the LLM output contract is a defined JSON schema (D17), scoring has one shared formula plus an explicit aggregation rule (D18), and the size limit has a real enforcement mechanism (D19). The full toolchain the container actually needs is now spelled out (it was previously undersold as just "Python 3.11 + FastAPI").

## High-level flow

```
User pastes GitHub URL
        │
        ▼
  POST /analyze  →  202 Accepted, { run_id }        (D15 — async, not blocking)
        │
        ▼ (background task)
  [Validate URL against allow-list]                  (Rule 17)
        │
        ▼
  [Check repo size via GitHub API — reject if over threshold]   (D19)
        │
        ▼
  [Fetch: git clone --depth 1 --filter=blob:limit=<N>, time-limited]
        │
        ▼
  [Orchestrator: run 5 pillars SEQUENTIALLY, one AnalysisRun in flight per process]   (D16)
        │  1. Code Evaluation      (deterministic)
        │  2. Security             (deterministic)
        │  3. Documentation        (deterministic)
        │  4. Production Readiness (deterministic)
        │  5. Semantic Analysis    (LLM via LLMProvider — Groq, structured JSON, D17)
        ▼
  [Aggregate: shared scoring formula + defined overall-verdict rule]   (D18)
        │
        ▼
  [Persist: Supabase Postgres]                        (D14)
        │
        ▼
  Frontend polls GET /analysis/{run_id} until status = complete/partial/failed
        │
        ▼
  [Render report UI]  +  [Export: HTML / JSON]
```

Each pillar has its own timeout. One pillar failing or timing out produces a `failed`/`partial` result for *that pillar only* — the run continues to the next pillar (D9, Rule 5).

## API contract (D15 — this did not exist before the audit)

```
POST /analyze
  body: { "url": "https://github.com/owner/repo", "confirm": false }
  → 202 { "run_id": "..." }
  → 400 if URL fails the allow-list check (Rule 17) or the size precheck (D19)
  → 409 if the repo contains flagged archive files and "confirm" wasn't true (D20)
       { "reason": "contains_archive_files", "files": ["assets/bundle.zip", ...] }
       → frontend shows a plain warning + "continue anyway" button, which resubmits with confirm: true
  → 429 if another run is already in flight (D16) — response includes a short "busy" message

GET /analysis/{run_id}
  → 200 {
      "status": "running" | "complete" | "partial" | "failed",
      "overall_score": number | null,
      "overall_verdict": string | null,
      "pillars_completed": "X/5",
      "pillars": [ { "name", "status", "tier", "score", "summary", "findings": [...] }, ... ]
    }

GET /analysis/{run_id}/export.html
GET /analysis/{run_id}/export.json
```

Frontend polls `GET /analysis/{run_id}` every ~2–3s while `status == "running"`.

## Folder structure

```
repoaudit/
  backend/
    app/
      main.py                 # FastAPI app, routes, template rendering
      api/
        routes_analysis.py    # POST /analyze, GET /analysis/{id}
        routes_export.py      # GET /analysis/{id}/export.html, /export.json
      pillars/
        base.py               # PillarResult, Finding, Pillar ABC (see interface below)
        code_evaluation.py
        security.py
        documentation.py
        production_readiness.py
        semantic_analysis.py
      llm/
        provider.py            # LLMProvider ABC
        groq_provider.py
        ollama_provider.py     # stub, optional, D1
      pipeline/
        orchestrator.py        # sequential pillar execution, single-run lock (D16), aggregation (D18)
        repo_fetcher.py        # size precheck (D19), clone, timeout
      models/
        schema.py               # Repo, AnalysisRun, PillarResult, Finding (pydantic + ORM)
      db/
        session.py              # Postgres (Supabase) in deployment, SQLite for local/tests (D14)
      export/
        html_export.py
        json_export.py
      templates/                # Jinja2, D13
      static/                   # vanilla JS/CSS — includes the polling script, D15
    tests/
      fixtures/                 # small local repos: clean / large / malformed / non-UTF8
      test_pillars/
      test_pipeline/
    Dockerfile                  # see Toolchain section below — this is not a plain Python image
    requirements.txt
  docker-compose.yml
  .env.example
  README.md
  00_BRIEF.md … 05_WORKFLOW.md
```

## Toolchain the container actually needs (new — this was previously implicit and wrong)

"Python 3.11 + FastAPI" undersold what Phase 3 (Security/Code Eval widening) actually requires inside the same image:

| Tool | Needs | Install approach |
|---|---|---|
| `ruff`, `radon`, `bandit`, `semgrep` | Python | `pip install` |
| `eslint`, `npm audit` | **Node.js + npm runtime** | Install Node 20 LTS in the Dockerfile alongside Python |
| `trivy` | standalone Go binary | install via Trivy's install script or apt, in the Dockerfile |
| `cloc` | Perl (or standalone binary) | `apt-get install cloc` |

**Trivy's vulnerability database** downloads over the network on first run by default. Combined with Render's ephemeral filesystem (D14's finding), a fresh container may re-pay that cost on every cold start. Two acceptable resolutions — pick one during Phase 1 (the risk spike) based on what's actually observed:
1. Bake the DB into the image at build time (`trivy image --download-db-only` in the Dockerfile), accepting a larger image.
2. Leave it to download at runtime, but explicitly budget that cost into the Security pillar's timeout and surface "scanner warming up" to the user rather than letting it look like a hang.

Don't guess between these — Phase 1 measures the actual cold-start cost and decides.

## Data model (conceptual — unchanged from original except `PillarResult` now records `tier` consistently and `status` values are fixed to `complete|partial|failed`, matching `AnalysisRun`)

**Second-pass fix:** the original draft of this file included a `skipped` status for `PillarResult` that was never actually assigned anywhere — every pillar always attempts to run (Tier-2 repos still get a `cloc` fallback, they're never skipped outright). Removed for consistency; a pillar is always `complete`, `partial` (ran but degraded — e.g. Tier-2 fallback, or truncated due to file-count limits), or `failed` (crashed, timed out, or unparseable LLM output).

```
Repo
  url, owner, name, default_branch, primary_language(s), size_kb, fetched_at

AnalysisRun
  id, repo_id, status (running/complete/partial/failed), started_at, completed_at,
  overall_score, overall_verdict, pillars_completed ("X/5")

PillarResult
  id, run_id, pillar_name, status (complete/partial/failed),
  score (0–100, nullable if failed), tier (1 or 2, for language-scoped pillars),
  summary (1–2 sentence templated verdict — must state *why* if not complete),
  findings[]

Finding
  id, pillar_result_id, severity (info/low/medium/high),
  category, message (specific, not generic — never a redacted secret's actual value, Rule 18),
  file_path (nullable), line (nullable)
```

## `Pillar` interface (reconciles the CLI entry point mentioned in `05_WORKFLOW.md`, which the original architecture never accounted for)

```python
class Pillar(ABC):
    name: str

    def run(self, repo_path: Path, *, timeout_s: int) -> PillarResult: ...

# every pillar module also exposes a small CLI so it can be run standalone
# during development, independent of the orchestrator:
#   python -m app.pillars.code_evaluation --path /tmp/some-cloned-repo
if __name__ == "__main__":
    ...
```

## `LLMProvider` interface (D1, used only by Semantic Analysis, D2)

```python
class LLMProvider(ABC):
    def generate(self, prompt: str, *, max_tokens: int, timeout_s: int) -> str: ...

class GroqProvider(LLMProvider): ...   # default, model string from GROQ_MODEL env var (D1/D17)
class OllamaProvider(LLMProvider): ... # optional, local, pluggable later
```

Nothing outside `pillars/semantic_analysis.py` imports `llm/` directly (Rule 13, unchanged) — keeps D2's boundary enforceable in code review.

### Semantic Analysis JSON contract (D17 — new)

```json
{
  "purpose": "string",
  "architecture_summary": "string",
  "modules": ["string", ...],
  "key_dependencies": ["string", ...],
  "findings": [ { "severity": "...", "category": "...", "message": "..." }, ... ],
  "score": 0-100
}
```
Requested explicitly in the prompt as "respond with ONLY this JSON shape, no prose before or after." One retry on parse failure with a stricter version of the same instruction. Second failure → `status=failed`, `reason="llm_output_unparseable"`.

## Scoring (D18 — new, previously undefined)

- **Code Evaluation, Security, Documentation, Production Readiness** share one formula: start at 100, deduct `high=-15, medium=-7, low=-2, info=0` per finding, floor at 0.
- **Semantic Analysis** score comes from the LLM against an explicit rubric in the prompt (architecture clarity, module-boundary clarity, dependency sanity) — judgment-based by design (D2), not point-deduction.
- **Overall score** = average of pillars with `status in {complete, partial}`. `failed` pillars are excluded from the average, never counted as zero. Report always states `pillars_completed` ("X/5").
- **Verdict labels:** 80–100 "Production Ready," 50–79 "Needs Work," 0–49 "Not Ready" — shown next to the number, never replacing it.

## Limits (D9, mechanism refined by D19)

| Limit | Default | Enforcement | Behavior on breach |
|---|---|---|---|
| Max repo size | 500 MB | GitHub API `size` field, checked **before** clone (D19) | Reject upfront, whole-run result: "repo too large for analysis" |
| Archive files (zip-bomb risk) | any `.zip`/`.tar.gz`/`.7z`/`.rar`/etc. over ~5MB in the file listing | GitHub API file listing, checked in the same pre-clone step as the size check (D20) | `409` with the file list unless the request already opted in with `confirm: true` — user decides, nothing silently blocked or silently proceeded |
| Blob-level backstop | `--filter=blob:limit=10m` on clone | git clone flag | Oversized individual blobs skipped, not fetched |
| Max file count analyzed per pillar | 5,000 files | in-pillar | Sample/truncate, note truncation in `summary` |
| Per-pillar timeout (deterministic) | 60s | orchestrator | Mark pillar `failed`/`partial`, continue to next pillar |
| Per-pillar timeout (Semantic Analysis / LLM call) | 90s | orchestrator | Mark pillar `failed`, note "semantic analysis unavailable this run," continue |
| Overall pipeline timeout | 5 min | orchestrator | Return whatever pillars completed as a `partial` `AnalysisRun` |
| Concurrent runs | 1 per process (D16) | in-process lock | New submission gets `429` "busy" response |

## Tiering in practice (D3)

`repo_fetcher.py` detects primary language(s) via GitHub API's `languages` endpoint. Each pillar module checks: is this repo's primary language in my Tier-1 list? If yes, run the real tool. If no, fall back to Tier-2 structural/`cloc` checks and set `PillarResult.tier = 2` — surfaced in the UI as "best-effort," never presented identically to a Tier-1 result.

## Report rendering

One dashboard per `AnalysisRun`: overall verdict + score up top (with `pillars_completed`), then five pillar cards (score, tier badge if Tier 2, templated summary — including *why* if not complete — expandable findings list). Export pulls from the same `AnalysisRun` — export is a serialization of data that already exists (D8).

## Storage (D14 — changed from the original SQLite-default)

- **Deployed (Render):** Postgres via `DATABASE_URL`, pointed at a free-tier Supabase project.
- **Local dev / tests:** SQLite, no external dependency needed.
- `db/session.py` picks the engine based on whether `DATABASE_URL` is set — same schema either way (SQLAlchemy), so this is a connection-string difference, not two codepaths.
