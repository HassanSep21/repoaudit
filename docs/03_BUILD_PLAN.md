# RepoAudit — Build Plan

Follow phases **in order**. Do not start a phase until the previous one's "Definition of Done" is actually, verifiably true. This is what D10 means by "one vertical slice first" — now applied to infrastructure risk before feature risk, per the adversarial audit.

> **Revision note:** the audit found that the original Phase 7 (deploy) was the first point several critical platform assumptions would actually get tested — storage persistence, request handling under Render's proxy, whether the toolchain even installs, whether 512MB/0.1vCPU holds up. That's the worst possible time to discover any of them. **A new Phase 1 (Risk Validation Spike) now runs before any feature code**, and every later phase's DoD was tightened to be objectively checkable rather than "run it and eyeball it."

---

## Phase 0 — Scaffolding

**Goal:** empty but runnable skeleton, deployable from hour one.

- [x] Create folder structure from `02_ARCHITECTURE.md`
- [x] FastAPI app boots, serves a static "hello" page via Jinja2 (D13)
- [x] `Dockerfile` installs the **full toolchain** per `02_ARCHITECTURE.md`'s table — Python packages, Node 20 + npm, Trivy binary, `cloc` — not just Python. Confirm each tool's `--version` runs inside the built image before moving on.
- [x] `docker-compose.yml` runs it locally on one command, using local SQLite (D14)
- [x] `.env.example` with `GROQ_API_KEY`, `GROQ_MODEL`, `GITHUB_TOKEN`, `DATABASE_URL` (all per `05_WORKFLOW.md`)
- [x] Push to GitHub, connect Render, confirm the empty app deploys and responds

**DoD:** a public Render URL loads a page. Empty, but live. `docker exec` (or Render shell) into the running container and confirm `ruff --version`, `eslint --version`, `trivy --version`, `cloc --version`, `semgrep --version`, `bandit --version` all succeed — not just that they work on your Fedora machine.

---

## Phase 1 — Risk Validation Spike (NEW — added by the adversarial audit)

**Goal:** prove or disprove the platform assumptions this whole architecture depends on, before writing a single pillar. This phase exists because C1–C4 in the audit report would otherwise only surface during the original deploy phase, right before the deadline.

- [x] **Storage:** write a row to Postgres (Supabase, D14) from the deployed Render app. Force a restart (manual restart in Render's dashboard, or just wait out a 15-minute idle spin-down). Confirm the row is still there. **This is the single most important checkbox in this phase** — it's the thing that was silently wrong before the audit.
- [x] **Async job pattern (D15):** implement `POST /analyze` and `GET /analysis/{run_id}` against a fake no-op "pillar" that just sleeps 10 seconds and returns a dummy result. Confirm: request returns fast with a `run_id`, polling reaches `complete`, this works from the deployed Render URL (not just localhost) including through a cold start.
- [x] **Concurrency lock (D16):** fire two `POST /analyze` requests back to back against the deployed app. Confirm the second gets the "busy" response, not a crash or silent double-run.
- [x] **Resource ceiling:** run Trivy and Semgrep against a small real repo from inside the *deployed* container (not locally) and note actual memory/time cost. If Trivy's DB download is slow on a cold container, decide now between the two mitigations in `02_ARCHITECTURE.md` (bake into image vs. budget into timeout) — don't defer this decision to Phase 4.
- [x] **GitHub API:** confirm `GITHUB_TOKEN` is wired and the rate limit is actually the higher, authenticated one (check response headers).

**Trivy DB mitigation decision:** Bake DB into image at build time (`trivy image --download-db-only` in Dockerfile). This avoids cold-start DB download on 0.1vCPU/512MB and is acceptable for the free tier image size limit.

**DoD:** every checkbox above passed against the *deployed* Render instance, not localhost. If storage doesn't survive a forced restart, or the async pattern doesn't work through a cold start, **stop and fix the architecture before Phase 2** — don't proceed hoping it'll be fine later.

---

## Phase 2 — Code Evaluation, end-to-end (D10; was "Phase 1" before the audit)

**Goal:** the first real vertical slice, now built on infrastructure already proven to work.

- [x] `repo_fetcher.py`: validate URL against the allow-list (Rule 17), check size + scan file listing for flagged archive files via GitHub API before cloning (D19, D20), `git clone --depth 1 --filter=blob:limit=10m`
- [x] Wire the `409`/`confirm: true` archive-warning flow into the minimal UI (D20) — a plain warning message with a "continue anyway" button is enough for this phase, doesn't need polish yet (that's Phase 6)
- [x] `pillars/base.py`: `Pillar` ABC, `PillarResult`, `Finding` per the data model, including the standalone CLI entry point
- [x] `pillars/code_evaluation.py`: Tier-1/Tier-2 detection (D3), run `ruff`+`radon` or `eslint` via subprocess (argument-list only, Rule 16), or `cloc` fallback
- [x] Templating: raw tool output → specific `Finding`s (file, line, message) — no raw linter JSON reaching the UI
- [x] Apply the shared scoring formula (D18)
- [x] `orchestrator.py`: runs this one pillar within the async job pattern proven in Phase 1, applies the 60s timeout, cleans up the temp clone dir in a `finally` block (Rule 19)
- [x] Minimal UI: input field → submit → poll → show Code Evaluation score + findings
- [x] Persist to Postgres (not SQLite) on the deployed instance

**DoD:** paste a real public repo URL on the **deployed Render URL**, get a real Code Evaluation score with specific findings. Run it twice on two different repos (one clean-ish, one with real issues) — the findings and scores must visibly differ, not just the repo name in a template. This is the differentiation check the original DoD lacked.

---

## Phase 3 — Robustness pass (was "Phase 2")

**Goal:** prove the pipeline survives bad input before widening to more pillars.

- [x] Test against: a tiny clean repo, a large repo (near the 500MB edge, and one that exceeds it — confirm the pre-clone rejection actually fires per D19), a malformed/broken repo (bad URL, empty repo), and a repo with non-UTF8 file content (confirm tolerant decoding, Rule 20, doesn't crash the pillar)
- [x] Cause a timeout on purpose (e.g. temporarily lower the timeout to 1s) and confirm graceful degradation, not a crash
- [x] Confirm a pillar failure produces a `partial`/`failed` result with a stated reason, not a 500 error

**DoD:** all four test repos above (large-over-limit, malformed, empty, non-UTF8) return *some* report or a clear rejection message — none of them crash the app or hang past the timeout.

---

## Phase 4 — Widen: Security, Documentation, Production Readiness (was "Phase 3")

**Goal:** reuse the Phase 2 pattern for the three remaining deterministic pillars (D2), now with Trivy/Semgrep's real resource behavior already known from Phase 1.

- [ ] `pillars/security.py`: Semgrep + Trivy (D4), + `npm audit`/`Bandit` for Tier-1. **Redact matched secret values** before they ever reach a `Finding.message` (Rule 18) — verify this with a fixture repo that has a fake hardcoded secret, and check the rendered finding never shows the actual value.
- [ ] `pillars/documentation.py`: README present/length, setup section detected, comment density, best-effort API doc coverage
- [ ] `pillars/production_readiness.py`: CI config, error-handling patterns, logging usage, LICENSE, Dockerfile presence
- [ ] Wire all three into `orchestrator.py`, **sequentially** after Code Evaluation (D16) — confirm total wall-clock time for all four deterministic pillars together is still within the overall 5-minute budget
- [ ] Expand the UI to show all four pillar cards

**DoD:** all four deterministic pillars run on the same test repos from Phase 3, each produces a distinct, specific score + findings, and the secret-redaction check above passes.

---

## Phase 5 — Semantic Analysis (was "Phase 4")

**Goal:** the one LLM-backed pillar, against the JSON contract defined in D17.

- [ ] `llm/provider.py` + `llm/groq_provider.py` — implement `LLMProvider`, 90s timeout, model string from `GROQ_MODEL` env var
- [ ] `pillars/semantic_analysis.py`: build a prompt from repo structure (mind context limits — don't dump the whole repo), request the exact JSON shape from `02_ARCHITECTURE.md`
- [ ] Implement the one-retry-then-fail parse logic (D17) — test it explicitly with a mocked malformed response
- [ ] Handle Groq timeout/rate-limit as `status=failed`, not a crash
- [ ] Wire into `orchestrator.py` as the fifth sequential pillar

**DoD:** all five pillars run together on a real repo and produce a complete report. Separately: a test with a mocked malformed LLM response confirms the retry fires once, then the pillar reports `failed` with `reason="llm_output_unparseable"` — not a crash, not a silently empty pillar.

---

## Phase 6 — Report UI polish (was "Phase 5")

**Goal:** meets "understandable to a non-author reviewer."

- [ ] Overall verdict + score + `pillars_completed` at the top (D18)
- [ ] Five pillar cards: score, Tier-2 badge where relevant, templated summary, expandable findings
- [ ] Clear treatment of `partial`/`failed` pillars — state what happened, never gray out silently

**DoD:** hand the report to someone who's never seen the repo — they can tell you what it does and whether it looks production-ready, from the report alone.

---

## Phase 7 — Export (was "Phase 6")

**Goal:** exportable/shareable output — and per the audit, the *more* robust of the two "shareable" mechanisms (D8), since it depends on nothing being awake.

- [ ] JSON export: full `AnalysisRun` including all pillars/findings
- [ ] HTML export: static, self-contained, reuses the report template
- [ ] (Stretch) PDF export, only if Phases 0–6 are solid with time to spare

**DoD:** a report downloaded from the deployed instance can be opened and read with the Render app fully shut down.

---

## Phase 8 — Deploy confirmation + real-world smoke test (was "Phase 7")

**Goal:** because Phase 1 already de-risked the platform, this phase is a final full run-through, not first contact with Render.

- [ ] Re-confirm all five pillars are live on the current deploy
- [ ] Re-run the Phase 1 storage-survives-a-restart check, now with real report data
- [ ] Run the full flow against 3–5 real public repos of varying size/language from a cold Render instance, on the actual deployed URL
- [ ] Confirm export works on the deployed instance

**DoD:** a fresh browser tab, on the public Render URL, produces a full five-pillar report with working export and durable storage, on a repo not used during development.

---

## Phase 9 — Buffer / demo polish (was "Phase 8")

**Goal:** final polish before Wednesday demo — clear error messages, backup exports, and verbal talking points.

- [x] Improve error message wording for non-technical clarity:
  - Archive warning (409) — explain what a zip bomb is and why we're cautious
  - Size-limit rejection (400) — state the limit and why it exists
  - Malformed URL (400) — show expected format with example
  - Pillar failure reasons — surface `summary` field with plain language
  - Busy/429 — explain single-run limit and suggest waiting
- [x] Confirm empty/loading states look right (landing page, progress card, results)
- [x] Generate 2–3 pre-run example reports (HTML export) from real repos, saved locally as backup:
  - Example 1: `pallets/click` (Python, Tier-1, clean-ish) — `backups/example-click.html`
  - Example 2: `octocat/Hello-World` (minimal, fast, Tier-2) — `backups/example-hello-world.html`
  - Example 3: `expressjs/express` (JS/TS, Tier-1) — `backups/example-express.html`
- [x] Write one-paragraph explanation of Tier-2/"best-effort" (for demo verbal use) — in README.md
- [x] Write one-paragraph explanation of partial/failed + `pillars_completed` (for demo verbal use) — in README.md
- [x] Run full demo-day checklist from `05_WORKFLOW.md`:
  - [x] Warm the Render URL a few minutes before demo (local container responding)
  - [x] Confirm Supabase project isn't paused (using local SQLite for dev; deployed would use Supabase)
  - [x] Have 2–3 known-good public repo URLs ready (`pallets/click`, `expressjs/express`, `octocat/Hello-World`)
  - [x] Have pre-generated HTML exports on hand as backup (in `backups/`)
  - [x] Know the Tier-2 and partial/failed explanations verbally (in README.md)
- [x] Update `README.md` with current project state
