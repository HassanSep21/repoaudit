# RepoAudit — Workflow

> **Revision note:** env vars now include `DATABASE_URL` and `GROQ_MODEL` (previously missing), `GITHUB_TOKEN` moved from optional to required (D21), and the deploy/demo checklists now account for D14 (Supabase) and D15 (async job pattern) — including a Phase-1-style smoke test to re-run right before demo day, per the audit's build-plan changes.

## Local development

```bash
cp .env.example .env       # fill in GROQ_API_KEY at minimum; DATABASE_URL can stay unset locally
docker compose up --build
```

Required env vars:

| Var | Required | Notes |
|---|---|---|
| `GROQ_API_KEY` | Yes, once Phase 5 starts | Free tier, from console.groq.com |
| `GROQ_MODEL` | Yes, once Phase 5 starts | e.g. `llama-3.3-70b-versatile` — pinned here so a future deprecation is a one-line fix (D1/D17) |
| `GITHUB_TOKEN` | **Yes, effectively required (D21)** | Unauthenticated GitHub API is 60 req/hr — exhausted trivially during normal testing |
| `DATABASE_URL` | Local: no (defaults to SQLite). **Deployed: yes (D14)** | Points at a free-tier Supabase Postgres project once deployed |

## Testing

```bash
pytest backend/tests
```

- `tests/fixtures/` holds small local repos: clean, has-issues, malformed/empty, and one with non-UTF8 file content — matches the Phase 3 robustness fixture set.
- Before marking any `03_BUILD_PLAN.md` phase done, also run the pipeline manually against 2–3 real public repos — fixtures catch regressions, real repos catch surprises.

## Running one pillar in isolation

```bash
python -m app.pillars.code_evaluation --path /tmp/some-cloned-repo
```

Every pillar module supports this (see the `Pillar` interface in `02_ARCHITECTURE.md`) — much faster to iterate on one pillar without running the whole async pipeline.

## Deployment (Render, D5) + storage (Supabase, D14)

1. Create a free-tier Supabase project, grab its Postgres connection string.
2. Push to GitHub. In Render: New → Web Service → connect the repo → Docker runtime.
3. Set env vars in Render's dashboard (Environment tab): `GROQ_API_KEY`, `GROQ_MODEL`, `GITHUB_TOKEN`, `DATABASE_URL` (the Supabase connection string). Never commit real secrets.
4. Deploy. Confirm the health-check page loads, then **immediately run the Phase 1 risk-validation checklist against the live URL** (storage survives a forced restart, async job pattern works through a cold start, toolchain versions all resolve inside the container) — don't assume it still holds just because it worked once.
5. **Cold start reminder (D5):** after ~15 min idle, the free instance sleeps; first request after that takes 30–60s. Expected — mention it in the demo itself rather than letting it look broken.
6. **Supabase pause reminder (D14):** free Supabase projects can pause after a period of inactivity and need a dashboard visit to resume. Check it's awake the day before the demo, same as warming Render.

## Demo-day checklist (Wednesday)

- [ ] Warm the Render URL a few minutes before the demo (hit it, wait for the cold start to clear)
- [ ] Confirm the Supabase project isn't paused
- [ ] Have 2–3 known-good public repo URLs ready, already smoke-tested in Build Plan Phase 8
- [ ] Have one pre-generated report exported (HTML) as a backup in case Groq or Render hiccups live — export doesn't depend on the server being up (D8)
- [ ] Know, in one sentence, what a Tier-2/"best-effort" result means, in case a demo repo isn't Python/JS
- [ ] Know, in one sentence, what a `partial`/`failed` pillar result and `pillars_completed` mean, in case something times out live — this is designed behavior (D9, D18), not a bug, and can be framed as such
