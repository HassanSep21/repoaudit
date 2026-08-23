# RepoAudit — Decisions Log

Every decision below is **LOCKED** unless marked otherwise. "Locked" means: don't re-litigate this while building — if it turns out to be wrong, come back and edit this file with a reason, don't just quietly do something else in code.

> **Revision note:** this doc went through an adversarial audit after D1–D13 were first written. D1–D6 and D8–D13 were re-challenged and reaffirmed as-is (rationale below, not rewritten). **D7 was found to be wrong** and is marked `SUPERSEDED` rather than deleted — see D14. **D14–D20 are new**, added to close real gaps the audit found: two platform assumptions that were factually wrong (storage, request handling), and several ambiguities that would have forced an implementing agent to guess.

---

### D1 — LLM provider: Groq, behind an abstraction
**Decision:** Use Groq (a fast free-tier hosted 70B model, e.g. `llama-3.3-70b-versatile`) as the default LLM backend, accessed through an `LLMProvider` interface. A local Ollama-backed provider is a pluggable, optional alternative — not the default.
**Why:** Zero budget rules out paid APIs. Local inference (even on the RTX 3080 Ti) is materially slower than Groq's free tier for this timeline. The abstraction means swapping providers later costs one new class, not a rewrite.
**Re-challenged in audit:** reaffirmed, no change. The exact model string must be a single env-var default (not hardcoded in multiple files) so a future Groq model deprecation is a one-line fix — see D17.
**Status:** LOCKED.

### D2 — Pillar split: Option A (LLM only where structurally required)
**Decision:** Only the **Semantic Analysis** pillar uses an LLM call. Code Evaluation, Security, Documentation, and Production Readiness are **fully deterministic** — static tools + templated finding generation, no LLM in the loop.
**Why:** The brief's own suggested-stack table assigns "LLM-based code understanding" to exactly one row (Semantic Analysis) and gives every other pillar a specific deterministic tool. This is also the lowest-risk option against a Wednesday deadline and Groq free-tier rate limits.
**Re-challenged in audit:** reaffirmed, and actually strengthened — the audit found the deployment target has only 512MB RAM / 0.1 vCPU (D5), so minimizing external-API dependence per pillar matters even more than originally thought.
**Status:** LOCKED.

### D3 — Language support: tiered, honest "best-effort"
**Decision:**
- **Tier 1 (Python, JS/TS):** real static analysis — `ruff` + `radon` for Python, `eslint` for JS/TS.
- **Tier 2 (everything else):** `cloc` for language-agnostic composition/comment-ratio metrics, plus structural checks that don't depend on language (README present? tests dir present? Dockerfile present? CI config present? LICENSE present?).
**Why:** Makes "any language, best-effort" literally true without writing a bespoke analyzer per language this week.
**Re-challenged in audit:** reaffirmed, no change.
**Status:** LOCKED.

### D4 — Security tooling: Semgrep + Trivy as primary
**Decision:** Semgrep and Trivy are the primary security scanners (both genuinely multi-language). `npm audit` covers JS/TS dependency vulnerabilities (Tier 1). `Bandit` covers Python-specific static analysis (Tier 1).
**Why:** The brief names all four tools explicitly. Semgrep/Trivy being multi-language is what makes the Security pillar honor D3's "any language" scoping.
**Re-challenged in audit:** reaffirmed, but with a new attached risk — Trivy's vulnerability database normally downloads on first run, and Semgrep's memory footprint is non-trivial. Both interact badly with the 512MB/0.1vCPU ceiling (D5) and the ephemeral filesystem (D14). Mitigation is now explicit in `02_ARCHITECTURE.md` and validated in Build Plan Phase 1.
**Phase 0 note (Render cold-start timing):** Semgrep `--version` on Render free tier (with `SEMGREP_ENABLE_VERSION_CHECK=0`, `SEMGREP_SEND_METRICS=off`) returned in ~2–3s total endpoint time (all 8 tools). Record exact Semgrep latency from Render logs and budget it into the Security pillar's 60s timeout in Phase 4.
**Status:** LOCKED.

### D5 — Deployment target: Render, not AWS
**Decision:** Backend deploys as a Docker web service on **Render's free tier**, replacing the brief's AWS/ECS/Fargate/Lambda suggestions.
**Why:** Zero budget. Fly.io and Railway killed their free tiers in 2024/2025. Render is currently the platform with a real, permanent free tier for a Docker web service, no card required.
**Verified in audit (previously assumed, now confirmed against Render's own docs and current pricing pages, Aug 2026):**
- Free web services: **512 MB RAM, 0.1 vCPU.**
- Spin down after **15 minutes idle**; cold start **~30–60s**.
- **Ephemeral filesystem — confirmed, not assumed:** any local filesystem change, including a SQLite file, is lost on every redeploy, restart, *or spin-down*. Free tier cannot attach a persistent disk (paid tiers only).
**Consequences of this verification:** D7 (SQLite as default store) is wrong as originally written — see D14. The 512MB/0.1vCPU ceiling means pillar execution must be sequential, not parallel — see D16.
**Accepted trade-off (documented on purpose, not hidden):** the 30–60s cold start is a known, stated constraint for the Wednesday demo — mention it up front rather than being caught out by it live.
**Status:** LOCKED.

### D6 — Frontend hosting: same container as backend (see D13)
**Decision:** No separate frontend deploy for the Wednesday deadline.
**Why:** Fewer moving parts, one deploy target, no CORS to debug under time pressure.
**Re-challenged in audit:** reaffirmed, and reinforced — once analysis became an async job with polling (D15), a plain server-rendered page with a small polling script needs nothing a separate frontend framework/build step would provide.
**Status:** LOCKED for the Wednesday deliverable. Open to revisit the following week.

### D7 — Storage: SQLite by default — SUPERSEDED, see D14
**Original decision:** SQLite baked into the backend container as the default store, with a documented caveat that it wouldn't survive a restart on Render's ephemeral filesystem, "swap to Postgres only if persistence turns out to matter."
**Why this was wrong:** the adversarial audit verified Render's actual behavior directly against Render's docs: free-tier filesystem changes are lost not just on redeploys but on **every spin-down**, which happens automatically after 15 minutes of no traffic. That's not an edge case to plan around — it's the default resting state of an idle demo app. Persistence already "turns out to matter" the moment D8 (shareable reports) exists, so the original hedge ("only if it matters") was self-defeating: it always mattered, the original phrasing just didn't notice.
**Status:** SUPERSEDED by D14. Kept here, not deleted, so the reasoning trail is visible.

### D8 — Report export is a hard requirement, not a stretch goal
**Decision:** Minimum export formats: static HTML export of a report, and JSON export of the raw structured result. PDF export is a stretch goal if time allows.
**Why:** The brief lists "exportable/shareable report output" as an explicit deliverable, not a nice-to-have.
**Re-challenged in audit:** reaffirmed, and clarified — now that D14 fixes persistence, "shareable" has two valid mechanisms (a durable link backed by Postgres, and a downloaded export file that needs no server at all). Export is the *more* robust one since it has zero dependency on the app being awake. Both are supported; export is not "the fallback," it's co-primary.
**Status:** LOCKED.

### D9 — Robustness is a hard requirement
**Decision:** Explicit hard limits up front: max clone size, max file count, per-pillar timeout, overall pipeline timeout. On any limit breach or pillar failure, the pipeline returns a **partial report with a clear reason** — it never crashes the whole run.
**Why:** "Handles large, small, and malformed repos gracefully" is an explicit success criterion.
**Re-challenged in audit:** the *principle* was reaffirmed, but the audit found the original size-limit **mechanism** was hand-waved — "enforce 500MB during clone" doesn't correspond to any real git behavior (git has no built-in "abort mid-clone past N bytes" flag). Fixed concretely — see D19. The rule for how a `failed` pillar affects the *overall* score was also never defined — see D18.
**Status:** LOCKED (mechanism refined in D18/D19).

### D10 — Build order: one vertical slice first
**Decision:** Build Code Evaluation end-to-end before touching any other pillar.
**Why:** The brief's own closing recommendation; de-risks the deadline.
**Re-challenged in audit:** reaffirmed for *feature* scope, but the audit found a bigger, earlier risk: the deployment platform's own assumptions (storage, request handling, toolchain, resource ceiling) were unverified and would otherwise have surfaced during the original Phase 7 (deploy), i.e. right before the deadline. The build plan now validates *infrastructure* risk in a new Phase 1, before the first feature slice — the same "prove the risky thing first" spirit as this decision, just applied one layer down.
**Status:** LOCKED. See `03_BUILD_PLAN.md`.

### D11 — Dev environment
**Decision:** Primary development on the Fedora Linux machine (RTX 3080 Ti). GPU not used as primary LLM per D1.
**Re-challenged in audit:** reaffirmed, no change.
**Status:** LOCKED.

### D12 — Coding agent: open
**Decision:** Not yet decided between Claude Code (paired with a local model) and OpenCode (paired with free-tier hosted models).
**Re-challenged in audit:** reaffirmed as correctly deferred — genuinely orthogonal to every other decision in this file, and every doc here is written to be agent-agnostic.
**Status:** OPEN — pick before Build Plan Phase 0, doesn't block anything in this doc set.

### D13 — One deployable unit: FastAPI serves both API and frontend
**Decision:** Single Python 3.11 + FastAPI app. Frontend is server-rendered (Jinja2) with vanilla JS/`fetch` for the async bits. No separate React/Next.js build for the Wednesday deadline.
**Why:** The heaviest analysis tools (ruff, radon, Bandit) are Python-native; a Python backend avoids cross-language subprocess friction. One deployable container means one Render service and no CORS.
**Re-challenged in audit:** reaffirmed, and strengthened by the resource-ceiling finding in D5 — every extra moving part is extra risk on a 512MB/0.1vCPU box, and a plain polling script (needed anyway per D15) doesn't benefit from a frontend framework.
**Status:** LOCKED for the Wednesday deliverable, open to revisit.

---

## New decisions from the adversarial audit

### D14 — Storage: Supabase free-tier Postgres is the deployed default; SQLite is local/tests only
**Decision:** The deployed app on Render connects to a free-tier managed Postgres (Supabase is the standard zero-budget pick) via `DATABASE_URL`. SQLite remains the default for local development and the test suite only — never assumed to work as the deployed store.
**Why:** See D7's supersession note. Confirmed against Render's docs: free-tier local filesystem changes do not survive a spin-down, which happens automatically after 15 minutes idle.
**Trade-off accepted:** one more external dependency (Supabase) than the original "everything in one container" plan. Worth it — the alternative is a demo that loses its own data during a bathroom break.
**Caveat to watch:** Supabase free projects can themselves pause after a period of inactivity and need a dashboard visit to resume — worth confirming the project is awake the day before the demo, same as warming Render itself. See `05_WORKFLOW.md`.
**Status:** LOCKED.

### D15 — Analysis is an async job, not a blocking request
**Decision:** `POST /analyze` validates the URL and returns a `run_id` immediately (HTTP 202). The actual pipeline runs in a background task. The frontend polls `GET /analysis/{run_id}` for status until it reaches `complete`/`partial`/`failed`.
**Why:** The pipeline is allowed to take up to 5 minutes (D9's overall timeout). Holding an HTTP request open that long is fragile against reverse-proxy/browser timeouts and gives zero progress feedback either way. This was previously unspecified — a real ambiguity, not a style choice.
**Rejected alternative:** WebSockets/SSE for push-based updates — more moving parts than a 512MB/0.1vCPU free-tier deploy benefits from; plain polling is the "boring tool" call consistent with D13.
**Status:** LOCKED.

### D16 — Pillars execute sequentially; one analysis run in flight per process
**Decision:** The orchestrator runs the five pillars one at a time, not in parallel. Only one `AnalysisRun` executes at a time process-wide, enforced with a simple in-process lock. A submission that arrives while another run is in progress gets an explicit "busy, try again shortly" response — never silently queued with no visibility, never run concurrently.
**Why:** 0.1 vCPU (confirmed, D5) is a fraction of one core — parallelism buys nothing there and multiplies peak memory at the exact moment Semgrep/Trivy (D4) are the heaviest processes running. This also directly protects against an accidental OOM if two people test the demo at once.
**Trade-off accepted:** total pipeline latency is higher than a parallel design would give. Reliability under a hard resource ceiling matters more than shaving latency for this deadline.
**Status:** LOCKED.

### D17 — LLM output contract for Semantic Analysis
**Decision:** The Semantic Analysis pillar requests **structured JSON** from Groq against an explicit schema (purpose, architecture summary, module list, key dependencies, findings[], score). On a parse failure, retry once with a stricter "return ONLY valid JSON, no prose" instruction; if that also fails, mark the pillar `failed` with reason `"llm_output_unparseable"` rather than guessing at partial data.
**Why:** "Parse the response into findings and a score" was not implementable as originally written — different agents would reasonably build this three different ways (regex over prose, hope for the best, ad hoc JSON). The model string used is a single env-var default (see D1) so a future deprecation is a one-line fix, not a multi-file hunt.
**Status:** LOCKED.

### D18 — Unified scoring formula + overall aggregation rule
**Decision:**
- The four deterministic pillars (Code Evaluation, Security, Documentation, Production Readiness) share **one** severity-weighted scoring formula: start at 100, deduct per finding by severity (`high=-15, medium=-7, low=-2, info=0`), floor at 0.
- Semantic Analysis is scored differently on purpose (it's judgment, not point-deduction): the LLM is given an explicit 0–100 rubric in its prompt (architecture clarity, module-boundary clarity, dependency sanity) and asked to return a score against that rubric as part of its structured JSON (D17).
- **Overall verdict** = average of all pillars with `status=complete` or `status=partial`. Pillars with `status=failed` are excluded from the average, and the report explicitly states "X/5 pillars completed" whenever X<5, rather than silently treating a failure as a zero (which would unfairly tank the score) or silently omitting it (which would hide that something didn't run).
- Verdict labels: 80–100 "Production Ready," 50–79 "Needs Work," 0–49 "Not Ready" — shown alongside the numeric score, never instead of it.
**Why:** Neither the shared formula nor the aggregation rule existed anywhere in the original docs. Without them, four different pillars (or four different implementation sessions) could invent four incompatible scoring schemes, which would quietly break the brief's own "one verdict" framing.
**Status:** LOCKED.

### D19 — Repo size enforcement: a real mechanism, not a number in a table
**Decision:** Before cloning, call the GitHub API to read the repo's reported `size` field (KB, compressed `.git` size) and reject upfront with a clear message if it's over the threshold (with safety margin, since compressed size understates working-tree size). Clone with `git clone --depth 1 --filter=blob:limit=<N>` as a backstop against a small number of enormous blobs. The per-fetch timeout (D9) remains the final backstop for anything both checks miss.
**Why:** The original "enforce 500MB during clone" didn't correspond to any actual git capability — git has no native "abort mid-operation past a size threshold" flag. This is now a real, implementable mechanism instead of an aspirational number.
**Status:** LOCKED.

### D20 — Archive/zip-bomb handling: warn and ask, don't silently block or silently proceed
**Decision:** As part of the existing pre-clone size check (D19), also inspect the repo's file listing (via GitHub API) for archive files (`.zip`, `.tar.gz`, `.7z`, `.rar`, etc.) above a small size threshold (e.g. 5MB). If any are found and the request hasn't already opted in, `POST /analyze` returns `409` with the reason and the list of flagged files instead of starting analysis. The frontend shows this as a plain warning with a "continue anyway" option that resubmits with `confirm: true`.
**Why:** The original audit accepted zip-bomb-style repos as a known limitation covered only by the size/timeout backstops. That's weaker than necessary for something this cheap to fix properly — the size-precheck step already exists (D19), so extending it to flag archive files is a small addition to code that's being written anyway, not a new subsystem. Putting the decision in front of the user (rather than silently blocking or silently proceeding) is also just more honest about what the tool does and doesn't protect against.
**Scope:** this catches "a repo contains a suspicious archive," not "we safely detonate and inspect the archive's actual contents" — RepoAudit still never extracts or executes anything from a cloned repo (Rule 15 stands). It's a warning based on file listing, not a scan of what's inside the archive.
**Status:** LOCKED.

### D21 — `GITHUB_TOKEN` is effectively required, not optional
**Decision:** Unauthenticated GitHub API access is capped at 60 requests/hour. `GITHUB_TOKEN` (a personal access token with no special scopes needed for public repo metadata) is treated as required for any development beyond a couple of manual smoke tests, and required for the demo.
**Why:** 60/hour is exhausted trivially during normal iterative testing — this would have been a self-inflicted, entirely avoidable demo-day failure if left as "optional."
**Status:** LOCKED.
