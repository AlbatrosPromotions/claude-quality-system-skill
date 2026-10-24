---
name: quality-system
description: Autonomous quality system for projects where Claude writes the code and no human reviews it — a critical project audit, the "writer never verifies itself" rule, an independent reviewer, spec-first tests, mutation testing, gate and critic canaries, a findings→rule loop, handoff hygiene, and an installable 3-layer git guard for main. Use when setting up or auditing a project's quality gates, when the user asks for a critical or brutally honest review of a project ("tanqidiy ko'rik", "sifat tizimi", "kamchiliklar"), before trusting AI-written code without human review, or to protect main from direct and force pushes.
---

# Quality system: high quality without human code review

Built and proven on sos-scrum (2026-10-24), where the developers could not review code, the pace (~3 sessions/day) was not to be reduced and quality loss was forbidden. The answer was not "slow down" or "let a human read it" but a system in which every claim of "done" is checked by something other than its author.

Talk to the user in their language (Uzbek for this user). Never propose lowering the pace or human code review as the fix; state the residual risk honestly instead.

## The one rule

**The writer never verifies itself.** Every check is either a deterministic script or a fresh-context agent that receives the spec and the diff, never the writer's reasoning or brief. Why, from real incidents:
- A local CI script on macOS bash 3.2 swallowed frontend failures for 12 sessions (`[[ … ]]` false result does not trigger `set -e`): the author's own "green" was false.
- A table header bleeding out of a rounded corner passed text/JSON checks for two sessions; only a full-size screenshot read by eye caught it, then a geometry test made it permanent.
- The lead who wrote the briefs also reviewed the diffs: anchored on its own design.

## Three modes

1. **Audit** a project (the user asks for a critical review, or before installing): follow `references/critical-review.md`. Measure, compare plan vs execution, rank findings with evidence, list what to keep, what to change and what not to do. Ask decisions one at a time; record them.
2. **Install** the system in a project: order below. Never install blindly: audit first, get the user's decisions, then build.
3. **Operate**: the per-PR, per-domain, weekly and monthly routines below.

## Components

Status: **proven** = built and run in a real project; **designed** = specified, not yet battle-tested (when a project builds it, copy the working version back into this skill and mark it proven).

| # | Part | Catches | Status |
|---|---|---|---|
| 1 | Gate canary: the gate runs a deliberately failing step per lane and must exit non-zero | "green but false" CI | designed |
| 2 | Spec tests written before code by a separate agent from the spec only; `spec_guard` blocks non-`spec:` commits that edit them | tests that confirm the implementation instead of the requirement | designed |
| 3 | Mutation testing on changed domain files with a threshold that never goes down | tests that cannot fail | manual form proven; tools in `references/components.md` |
| 4 | Independent senior reviewer (fresh context, strongest model, read-only) + review stamp checked by the merge hook | logic, security, tenant isolation, money/time, scope creep | designed; template `references/reviewer.md` |
| 5 | Independent oracle for money and time (second implementation from the spec, randomized differential tests) + property tests for engines | calculation errors | designed |
| 6 | Traceability: every PR names its source (spec §, AC); untraceable code is returned | unrequested mechanisms (YAGNI) | designed |
| 7 | Weekly deep audit session (fresh context) | slowly accumulating debt and drift | designed |
| 8 | Module READMEs: purpose, entry points, invariants, where to start reading | context loss; future hires | designed |
| 9 | Critic canary: seed 3–5 known defects in a throwaway branch, give it to the reviewer as a normal PR, log the catch rate | a reviewer that misses | designed |
| 10 | Findings→rule loop: every finding logged with a category; a repeat or any blocker becomes a deterministic check proven by mutation | the same bug found twice by eye | proven pattern (geometry tests, test-clock rule) |
| 11 | **Git guard, 3 layers**: Claude PreToolUse hook, git `pre-push`, history check | direct/force push to main, gate bypass | **proven, installable** (`scripts/git-guard/`) |
| 12 | Framework strict modes + query-count tests on list endpoints | N+1, silently dropped attributes | designed |
| 13 | Volume tests from the first list API (seeded scale + `EXPLAIN` + time budget) | index/RLS performance found late | designed |
| 14 | Lead context limit ~250–300k tokens → handoff and new session | attention decay in huge contexts | rule |
| 15 | Model change → rerun critic canary; dual-model review for money/security PRs | model behaviour drift, shared blind spots | designed |
| 16 | Monthly dependency session (small upgrade PRs through the full gate) | big-bang upgrades on a bleeding-edge stack | rule |
| 17 | Comment discipline: comments say only "why", 1–3 lines; history lives in ADRs and READMEs | rotting references, unreadable code | rule |
| 18 | Domain scenarios (10–20 concrete cases in plain language) approved by a domain human before each big domain; accountant-approved cases feed the oracle | the spec itself being wrong | designed; the only human minutes in the system |

Details, stack-specific tools and file formats: `references/components.md`.

## Install order

0. **Audit** (`references/critical-review.md`), present findings, record the user's decisions (project memory + handoff).
1. **Git guard** (minutes): `python3 ~/.claude/skills/quality-system/scripts/git-guard/install.py <project-root> [--merge-style merge|squash|any]` (match the repo's GitHub merge method; `--dry-run` first; see `scripts/git-guard/README.md`). Every new clone runs `python3 .claude/quality/main_guard.py --setup` once (put it in the project's setup target). Then add its tests and `main_guard.py` to the project's gate, add one CLAUDE.md line, and test live: a dry-run push to main is blocked by the hook; the same push from inside a script is rejected by `pre-push`.
2. **Gate**: hosted CI with required checks, or, when CI is unavailable, the local gate pattern (tree-hash stamp + merge hook) in `references/components.md` §0. Add the canary (1).
3. **Cheap rules now**: findings journal (10), traceability line (6), lead context limit (14), comment discipline (17), strict modes (12) — CLAUDE.md lines plus small checks.
4. **Review pipeline**: reviewer agent and review stamp (4), mutation on changed files (3), spec tests + `spec_guard` (2). Pilot the reviewer on its own installation PR.
5. **Per domain**: scenarios (18) before the design; oracle/property tests (5) for money, time and state machines; volume tests (13) with the first list API.
6. **Schedules**: weekly audit + critic canary (7, 9), monthly dependencies (16), recalibration on model change (15).

## Operate

**Per PR**
- PR description carries `Source: <spec §>, <AC>`; nothing untraceable.
- Gate: canary, all tests, mutation on changed files, `spec_guard`, `main_guard`.
- Fresh reviewer gets diff + cited spec sections + CLAUDE.md, never the writer's brief. Blockers are fixed, then re-review on the new head. Money/time/tenant/auth PRs also get a security pass; money/security PRs get a second model (15).
- UI changes: full-size screenshots at desktop and phone width, light and dark, read by eye (text/JSON proves nothing about pixels); add a geometry test for every visual defect class found.
- Merge only when the gate stamp and the review stamp both match the head tree.

**Per finding**: journal row (date, PR, category, severity, how caught). Second occurrence or first blocker → deterministic rule (arch test, lint rule, geometry test), proven by reverting the fix and seeing it go red.

**Per session**: start from a short handoff (≤ ~20 KB), run the gate on main, check `main_guard` output; end with the handoff updated. Split at ~250–300k lead context. Hygiene rules: `references/handoff.md`.

## Telling the truth about residual risk

Always state it, never hide it: writer and reviewer share a model family (blind spots can overlap); if the spec is wrong every component agrees with it. Mitigations: spec tests from the source (2), an independent oracle (5), the critic canary (9), dual-model review (15), human-approved scenarios (18), and for payroll a shadow run against real numbers before declaring it trustworthy.

## Audit and advice style

- Brutally honest, numbers with evidence (`path:line`, command output), ranked by impact.
- Separate "keep (don't change)", "change (prioritized)", "don't do".
- Respect topics the user parked (check memory: e.g. budget-blocked infrastructure is not raised until the user asks).
- One decision per question; record each answer where future sessions read it.

## Files

- `references/critical-review.md` — audit method, measurement commands, report template.
- `references/components.md` — how to build each part, tools per stack, the local gate pattern.
- `references/reviewer.md` — reviewer agent template, checklist, review stamp, critic canary, findings journal.
- `references/handoff.md` — handoff size and structure, sources of truth, ADR discipline, guessed contracts.
- `scripts/git-guard/` — installable 3-layer main protection with tests.
