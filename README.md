# RepoAudit

Automated repository quality auditing across five dimensions of software quality.

## What it does

Submit any public GitHub repository URL → RepoAudit clones the repo → runs five independent analysis pillars → returns a scored, structured report.

### The Five Pillars

| # | Pillar | What it checks |
|---|--------|----------------|
| 1 | **Code Evaluation** | Quality, complexity, duplication, code smells, test coverage, idiom adherence (via ruff/eslint) |
| 2 | **Security** | Vulnerable dependencies, hardcoded secrets, unsafe patterns, attack surface (via Semgrep, Trivy, npm audit, Bandit) |
| 3 | **Documentation** | README quality, setup instructions, inline comments, API/interface coverage |
| 4 | **Production Readiness** | CI/CD presence, error handling, logging, licensing, containerization |
| 5 | **Semantic Analysis** | Purpose, architecture, module boundaries, key dependencies (via LLM) |

## Quick Start

```bash
# Local development
cp .env.example .env
# Fill in GROQ_API_KEY, GITHUB_TOKEN at minimum
docker compose up --build

# Visit http://localhost:8000
```

## Deployment

Deployed on Render (free tier) with Supabase Postgres for persistence.

Required environment variables:
- `GROQ_API_KEY` — Groq API key for Semantic Analysis
- `GROQ_MODEL` — e.g., `openai/gpt-oss-120b`
- `GITHUB_TOKEN` — GitHub personal access token (required for API rate limits)
- `DATABASE_URL` — Supabase Postgres connection string

## Demo-Day Checklist

- [ ] Warm the Render URL a few minutes before demo (cold start ~30-60s)
- [ ] Confirm Supabase project isn't paused
- [ ] Have 2–3 known-good public repo URLs ready (e.g., `pallets/click`, `expressjs/express`, `octocat/Hello-World`)
- [ ] Pre-generated HTML exports in `backups/` as fallback
- [ ] Know the Tier-2 and partial/failed explanations (see below)

## Understanding Results

### Tier-2 / "Best-Effort" Results

When a repository's primary language isn't Python or JavaScript/TypeScript, RepoAudit falls back to Tier-2 analysis. This means: we run language-agnostic checks only — `cloc` for code composition metrics, structural checks (README present? tests directory? CI config? LICENSE? Dockerfile?), and basic documentation heuristics. No language-specific linters or security scanners run. Scores reflect this limited scope and are labeled with a "Tier 2" badge. The findings are real but incomplete; treat them as a starting point, not a comprehensive audit.

### Partial / Failed Pillars and `pillars_completed`

Each pillar can complete with one of three statuses:
- **Complete** — Full analysis ran, score and findings are reliable
- **Partial** — Analysis ran but was degraded (e.g., Tier-2 fallback, file count limit hit, or truncated output). The `summary` field explains the limitation.
- **Failed** — The pillar crashed or timed out. No score or findings available.

The top of every report shows `pillars_completed` (e.g., "4/5"). The overall score averages only `complete` and `partial` pillars — `failed` pillars are excluded, never counted as zero. This prevents a single timeout from tanking the whole report. If you see fewer than 5/5, check each pillar's status badge and summary for the reason.

## Architecture

- **Frontend**: Server-rendered Jinja2 + vanilla JS (polling for async results)
- **Backend**: FastAPI (Python 3.11) with async pipeline
- **Analysis**: Sequential pillar execution (D16), per-pillar timeouts, temp cleanup guaranteed
- **Storage**: Supabase Postgres (deployed), SQLite (local dev)
- **Tools**: ruff, radon, bandit, semgrep, trivy, cloc, eslint, npm audit

## Export

Every report can be exported as:
- **HTML** — static, self-contained, works offline
- **JSON** — full structured data for programmatic use

## License

MIT