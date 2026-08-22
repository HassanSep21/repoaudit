# RepoAudit — Canonical Brief

> This file is the ground-truth spec, reconciled directly against the original brief document (not a secondhand paraphrase). If anything in the other docs in this set ever conflicts with this file, this file wins.
>
> **Revision note:** this doc set went through an adversarial audit after the initial draft. This file's content is unchanged from that audit (the brief itself wasn't wrong) except for the new "Explicitly out of scope" section below, which was missing entirely and is exactly the kind of gap the audit was meant to catch — see `01_DECISIONS.md` for everything else that changed.

## One-line description

User submits a public GitHub URL → platform fetches the repo → runs it through five analysis pillars → renders one scored, structured dashboard per repo.

## Mission (from the brief)

1. **Accept a public GitHub URL** — validate the link, resolve the default branch, fetch repo metadata via the GitHub API.
2. **Pull the source** — clone/fetch the repo into an isolated analysis environment.
3. **Run the five-pillar review** — each pillar produces findings + a score.
4. **Present a structured report** — one dashboard per repo: overall verdict, category scores, specific findings.

## The five pillars

| # | Pillar | What it checks |
|---|--------|-----------------|
| 1 | Semantic Analysis | Purpose, architecture, module boundaries, key dependencies between components |
| 2 | Code Evaluation | Quality, complexity, duplication, code smells, test coverage, idiom adherence |
| 3 | Security | Vulnerable dependencies, hardcoded secrets, unsafe patterns, attack surface |
| 4 | Documentation | README quality, setup instructions, inline comments, API/interface coverage |
| 5 | Production Readiness | CI/CD presence, error handling, logging, licensing, shippability |

## Deliverables (explicit checklist from the brief)

- [ ] Working web app with a single GitHub-URL input field
- [ ] Backend pipeline that fetches and analyzes any public repo
- [ ] Report UI showing all five pillars with scores + findings
- [ ] Overall production-readiness verdict per repo
- [ ] Exportable / shareable report output

## Success criteria (explicit, from the brief)

- [ ] Works on any valid public GitHub repo URL
- [ ] Returns a complete report in a reasonable time budget
- [ ] Findings are **specific**, not generic pass/fail
- [ ] Handles large, small, and malformed repos gracefully
- [ ] Report is understandable to a non-author reviewer

## Explicitly out of scope

Not in the original brief document, but absent from every version of this doc set until the adversarial audit — added now so no implementing agent invents one of these:

- No user accounts, login, or auth of any kind.
- No private repo support — public GitHub URLs only.
- No multi-repo comparison view.
- No guaranteed permanent report history — persistence is best-effort (see D14 in `01_DECISIONS.md`), not a promised archive.
- No per-language bespoke analyzers beyond the Tier-1/Tier-2 split in D3 — "any language, best-effort" is scoped, not literal.
- **The analyzed repo's own code is never executed, built, or have its dependencies installed, under any circumstance.** Static analysis only. This is a hard boundary, not a stretch goal — see `04_RULES.md` Rule 15.

## Suggested stack — explicitly "starting points, not requirements"

React/Next.js frontend · Node.js or Python/FastAPI backend · GitHub REST/GraphQL API + git clone · ESLint/Pylint for code eval · Trivy/npm audit/Bandit/Semgrep for security · Claude API for semantic analysis · heuristic checks for docs · PostgreSQL for storage · Docker Compose locally, mirrored to AWS for deploy.

**Everywhere this project deviates from the above, the deviation is logged in `01_DECISIONS.md` with a reason.** Nothing is swapped silently.

## The brief's own closing recommendation

Don't build all five pillars at once. Start with **one pillar end-to-end** (the brief itself suggests Code Evaluation as the easiest first slice), then widen. A thin, working pipeline beats five half-built analyzers.

**Audit-era refinement (see `03_BUILD_PLAN.md`):** this is still true for *feature* scope. But before any feature slice, the plan now validates *infrastructure* risk first (deploy platform, storage, toolchain, job pattern) — because those turned out to be a bigger threat to the deadline than which pillar gets built first.

## Constraints this project is operating under (not in the original brief, added by the intern)

- Zero budget — free-tier or local tools only, no paid APIs.
- Wednesday = deliverable deadline. The following week is the last week of the internship.
- Preference: Wednesday's deliverable is a **working, deployed** app, not just code.
- "Any language, best-effort" — see `01_DECISIONS.md` (D3) for how this is scoped honestly.
