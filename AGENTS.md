# RepoAudit — Agent Instructions

Read these six files, in this order, before writing any code or making any changes:

1. `00_BRIEF.md` — what we're building, and what's explicitly out of scope. Don't add anything listed as out of scope.
2. `01_DECISIONS.md` — every locked technical decision (D1–D21) and why. Don't silently revert or ignore any of these.
3. `02_ARCHITECTURE.md` — system design, API contract, data model, folder structure, the full toolchain the container needs.
4. `03_BUILD_PLAN.md` — the phase-by-phase build order. Follow it strictly, in order. Do not start a phase until the previous one's Definition of Done is objectively, verifiably true — not "looks about right."
5. `04_RULES.md` — hard engineering rules (21 of them). Never violate these, even if a shortcut looks convenient in the moment. Rule 15 in particular: never execute, build, or install dependencies for a repo being analyzed.
6. `05_WORKFLOW.md` — how to run, test, and deploy this project, including required env vars.

**Current phase: Phase 0 (Scaffolding).** Do not skip ahead to later phases even if it seems faster.

If anything here conflicts with something that seems more convenient, follow the docs — they were written deliberately and went through an adversarial audit specifically to remove ambiguity. If a decision genuinely looks wrong once you're implementing it, say so and propose an edit to `01_DECISIONS.md` — don't just quietly do something else.
