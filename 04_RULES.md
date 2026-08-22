# RepoAudit — Rules

These apply regardless of which coding agent or model is doing the implementation (D12). If a rule here conflicts with something that feels convenient in the moment, the rule wins.

> **Revision note:** Rules 1–14 are unchanged from the original draft (reaffirmed during the audit). Rules 15–21 are new — each one closes a gap the adversarial audit found: an unstated boundary around executing untrusted code, an injection vector, a secret-leak path, a resource-exhaustion path, or an encoding crash.

## Scope discipline

1. **Don't silently revert to the brief's original suggested stack.** AWS, Postgres-by-default-in-a-different-way-than-decided, Docker Compose-as-deploy — these were explicitly overridden in `01_DECISIONS.md`. If a decision looks wrong, propose an edit to the decisions file — don't quietly build around it.
2. **Don't build ahead of the current phase.** If you're in Phase 2 and find yourself writing the Security pillar, stop.
3. **No pillar is ever a stub that fakes a score.** Unsupported tool/language → the pillar says so explicitly and lowers `tier`/marks `partial` — it never invents a plausible number.

## Reliability

4. **Every subprocess call and every LLM call gets an explicit timeout.** No exceptions. Use `02_ARCHITECTURE.md`'s Limits table as the default.
5. **One pillar's failure never takes down the whole run.** Catch it at the pillar boundary, record `status=failed`/`partial` with a reason, let the orchestrator continue.
6. **Test against at least the fixture set in `03_BUILD_PLAN.md` Phase 3 before calling a phase done:** clean, large-over-limit, malformed/empty, non-UTF8. Not optional polish.

## Findings quality

7. **Every finding must be specific.** File + line where available, a concrete description — never "some issues were found."
8. **Deterministic pillars are fully templated before reaching the UI.** No raw linter/scanner JSON or stack traces shown to the user.
9. **Semantic Analysis still needs structure.** Extract discrete findings + a score from the LLM's JSON response (D17) — never let free-form prose reach the UI unparsed.

## Security & secrets

10. **No secrets in code, ever.** `GROQ_API_KEY`, `GROQ_MODEL`, `GITHUB_TOKEN`, `DATABASE_URL` — env vars only. Commit `.env.example`, never `.env`. Real values live in Render's dashboard for deploy.
11. **Treat every cloned repo as untrusted input.** Run analysis tools against it; don't `exec`/`eval`/`import` anything from inside the target repo.

## Process

12. **Commit per vertical slice, not big-bang.** A commit should correspond to a checkbox in `03_BUILD_PLAN.md`.
13. **Keep `LLMProvider` usage confined to `pillars/semantic_analysis.py`.** If any other pillar module imports from `llm/`, that's a sign D2 is being violated — stop and reconsider.
14. **When in doubt, prefer the boring, already-solved tool** (subprocess out to `ruff`/`eslint`/`semgrep`/`cloc`) over hand-rolling analysis logic.

## New rules from the adversarial audit

15. **Never execute, build, install dependencies for, or run scripts/tests belonging to an analyzed repo, under any circumstance.** No `npm install`, no `pip install -r requirements.txt`, no running the target repo's own test suite "for real coverage numbers," no invoking any script found inside it. Static analysis only — this is a hard boundary (see `00_BRIEF.md`'s explicit out-of-scope section), not a "handle gracefully" judgment call. The tempting shortcut here is exactly the one to refuse.
16. **All subprocess calls use argument lists, never `shell=True` or string-interpolated shell commands.** This applies to every tool invocation (`git`, `ruff`, `eslint`, `semgrep`, `trivy`, `cloc`, `bandit`, `npm`) without exception.
17. **Validate every submitted URL against a strict allow-list pattern** (`https://github.com/{owner}/{repo}(.git)?`, GitHub host only) **before it ever reaches `git clone` or any subprocess.** Reject anything else outright — no `git@` SSH form, no other hosts, no local paths, no scheme other than `https`.
18. **Redact matched secret values from Security-pillar findings.** Semgrep/Trivy secret-detection rules often include the matched string in their raw output — when templating that into a `Finding`, keep the file location and rule name, never the actual secret value.
19. **Every analysis run cleans up its temp clone directory in a `finally` block**, guaranteed even on pillar failure, timeout, or crash. Free-tier disk is small; repeated runs without cleanup will fill it.
20. **File reads for heuristic/documentation checks use tolerant decoding** (e.g. `errors="replace"` in Python). A decode error on one file is never allowed to crash a pillar — skip that file, note it, move on.
21. **Only one `AnalysisRun` executes at a time per process** (D16). A submission that arrives while another is running gets an explicit `429`/"busy" response — never silently queued with no visibility to the user, never run concurrently with another.
