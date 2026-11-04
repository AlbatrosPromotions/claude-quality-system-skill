---
name: quality-system
description: Autonomous quality system for projects where Claude writes the code and no human reviews it — a critical project audit, the "writer never verifies itself" rule, an independent reviewer, spec-first tests, mutation testing, gate and critic canaries, a findings→rule loop, handoff hygiene, and two installable kits: a 3-layer git guard for main and a review gate (review stamp with a round budget, merge guard, findings log, spec guard). Use when setting up or auditing a project's quality gates, when the user asks for a critical or brutally honest review of a project ("tanqidiy ko'rik", "sifat tizimi", "kamchiliklar"), before trusting AI-written code without human review, or to protect main from direct and force pushes.
---

# Quality system: high quality without human code review

First built and proven on a production monorepo (Laravel + Angular, code written by Claude, ~3 sessions a day, no human code review), 2026-10/11. The answer was not "slow down" or "let a human read it": every claim of "done" is checked by something other than its author.

Talk to the user in their language. Never propose lowering the pace or human code review as the fix; state the residual risk honestly instead.

## The one rule

**The writer never verifies itself.** Every check is either a deterministic script or a fresh-context agent that receives the spec and the diff, never the writer's reasoning or brief. Why, from real incidents:
- A local CI script on macOS bash 3.2 swallowed frontend failures for 12 sessions (a false `[[ … ]]` does not trigger `set -e`): the author's own "green" was false.
- A table header bleeding out of a rounded corner passed text/JSON checks for two sessions; a full-size screenshot read by eye caught it, then a geometry test made it permanent.
- The lead who wrote the briefs also reviewed the diffs: anchored on its own design.

## Three modes

1. **Audit** a project (the user asks for a critical review, or before installing): follow `references/critical-review.md`.
2. **Install** the system in a project: order below. Audit first, get the user's decisions, then build.
3. **Operate**: the per-PR, per-domain, weekly and monthly routines below.

## Components

Status: **proven** = built and run in a real project; **designed** = specified, not yet battle-tested (once a project builds it, copy it back here and mark it proven).

| # | Part | Catches | Status |
|---|---|---|---|
| 1 | Gate canary: the gate runs a deliberately failing step per lane and must exit non-zero | "green but false" CI | proven pattern (`references/components.md` §1) |
| 2 | Spec tests written before code by a separate agent from the spec only; `spec_guard` blocks non-`spec:` commits that edit them | tests that confirm the implementation instead of the requirement | proven (`spec_guard.py`) |
| 3 | Mutation testing, optional and by hand, never in the PR gate | tests that cannot fail | manual form proven |
| 4 | Independent senior reviewer (fresh context, strongest model, read-only) + review stamp checked by the merge guard | logic, security, tenant isolation, money/time, scope creep | **proven, installable** (`scripts/review-gate/`) |
| 5 | Independent oracle for money and time (second implementation from the spec, randomized differential tests) + property tests for engines | calculation errors | designed |
| 6 | Traceability: every PR names its source (spec §, AC); untraceable code is returned | unrequested mechanisms (YAGNI) | proven (`review_stamp.py` checks the `Source:` line) |
| 7 | Weekly deep audit session (fresh context, lean read-only auditor agent) | slowly accumulating debt and drift | designed (`references/auditor.md`) |
| 8 | Module READMEs: purpose, entry points, invariants, where to start reading | context loss; future hires | designed |
| 9 | Critic canary: 3–5 planted defects in a throwaway branch reviewed as a normal PR, catch rate logged | a reviewer that misses | designed |
| 10 | Findings→rule loop: blocker, major and product findings logged with a category; a repeat or any blocker becomes a deterministic check proven by mutation | the same bug found twice by eye | proven (`quality_log.py`) |
| 11 | **Git guard, 3 layers**: Claude PreToolUse hook, git `pre-push`, history check | direct/force push to main, gate bypass | **proven, installable** (`scripts/git-guard/`) |
| 12 | Framework strict modes + query-count tests on list endpoints | N+1, silently dropped attributes | designed |
| 13 | Volume tests from the first list API (seeded scale + `EXPLAIN` + time budget) | index/RLS performance found late | designed |
| 14 | Lead context limit ~250–300k tokens → handoff and new session | attention decay in huge contexts | rule |
| 15 | Model change → rerun critic canary; dual-model review for money/security PRs | model behaviour drift, shared blind spots | designed |
| 16 | Monthly dependency session (small upgrade PRs through the full gate) | big-bang upgrades on a bleeding-edge stack | rule |
| 17 | Comment discipline: comments say only "why", 1–3 lines; history lives in ADRs and READMEs | rotting references, unreadable code | rule |
| 18 | Domain scenarios (10–20 concrete cases in plain language) approved by a domain human before each big domain; accountant-approved cases feed the oracle | the spec itself being wrong | designed (the only human minutes) |

## Install order

0. **Audit** (`references/critical-review.md`), present findings, record the user's decisions (project memory + handoff).
1. **Git guard** (minutes): `python3 ~/.claude/skills/quality-system/scripts/git-guard/install.py <project-root> [--merge-style merge|squash|any]` (`--dry-run` first; README there). Every new clone runs `python3 .claude/quality/main_guard.py --setup` once. Add its tests and `main_guard.py` to the gate, one CLAUDE.md line, and test live: a push to main is blocked by the hook, and from inside a script by `pre-push`.
2. **Gate**: hosted CI with required checks, or, when CI is unavailable, the local gate pattern with `gate_stamp.py` (`references/components.md` §0). Add the canary (1); stay within the Gate budget (Operate).
3. **Cheap rules now**: findings journal (10, `quality_log.py --check`), traceability line (6), lead context limit (14), comment discipline (17), strict modes (12): CLAUDE.md lines plus small checks.
4. **Review pipeline** (4, 2, 10), after the git guard: `python3 ~/.claude/skills/quality-system/scripts/review-gate/install.py <project-root> [--prefix P] [--reviewer NAME] [--gate-command "…"] [--source-labels Source,…]`. Then wire `gate_stamp.py` into the gate script, add the CLAUDE.md lines the installer prints, and pilot the reviewer on the installation PR.
5. **Per domain**: scenarios (18) before the design; oracle/property tests (5) for money, time and state machines; volume tests (13) with the first list API.
6. **Schedules**: weekly audit + critic canary (7, 9), monthly dependencies (16), recalibration on model change (15).

## Operate

**Per PR**
- PR description carries `Source: <spec §>, <AC>`; nothing untraceable.
- Gate (≤ 10 min): canary, all tests, `spec_guard`, `main_guard`.
- Fresh reviewer gets only the PR number and repo directory; it reads the diff, the cited spec sections and CLAUDE.md itself, never the writer's brief. `review_stamp.py --pr N --check` says whether a round is allowed; a third round only after the user's yes, recorded with `review_stamp.py --pr N --allow`. Blockers and majors are fixed, then one re-review on the new head. Money/time/tenant/auth PRs also get a security pass; money/security PRs a second model (15).
- UI changes: full-size screenshots at desktop and phone width, light and dark, read by eye (text/JSON proves nothing about pixels); a geometry test for every visual defect class found.
- Merge only when the gate stamp matches the head tree and the review stamp's head is the PR head or its ancestor; the merge guard hook enforces both.

**Per finding** (blockers, majors, product findings): journal row (date, PR, category, severity, how caught). Second occurrence or first blocker → deterministic rule (arch test, lint rule, geometry test), proven by reverting the fix and seeing it go red.

**Per session**: start from a short handoff (≤ ~20 KB), run the gate on main, check `main_guard` output; end with the handoff updated. Split at ~250–300k lead context (`references/handoff.md`).

### Gate budget (measured 2026-11-04)

In the first project the verification loop grew several times longer than the coding. These limits win over the Components table and Install order where they differ.

1. **Deterministic gate ≤ 10 minutes wall time**; skip every stage whose inputs did not change. (A slow gate made the user want to drop it, 2026-10-26.)
2. **One independent review per PR is mandatory.** A second review only if the first found a blocker or a major; a third only when the user says yes, and that permission leaves a visible trace the stamp script checks (the allow file of `review_stamp.py --allow`). Enforce it in scripts and hooks, never in prose alone: the stamp script refuses extra rounds, the merge guard accepts an older stamp whose head is an ancestor of the PR head. (40 PRs: 54 re-reviews found 0 blockers, 9 majors, all in round two; 75% of gate time was re-reviews.)
3. **After a clean first review**, later commits (minor fixes, merging main) go through the gate only, no new review. Minor findings go to the next PR.
4. **Mutation testing does not belong in the PR gate**; if kept at all, it runs by hand or as an occasional measurement, never as a blocking step. (0 unique defects over 40 PRs, 455–644 s per run, 6 infrastructure PRs; the user dropped it.)
5. **The findings journal takes only blockers, majors and product findings**; system-side minor findings stay in the PR comment. (80% of 304 rows were about the system itself.)
6. **Guard hooks (11) protect against accidental mistakes and agent drift, not a deliberate adversary.** A bypass form that can only be written on purpose is at most minor; never hunt new shell forms with regexes; a table test over ordinary forms is enough; nobody writes a shell parser. (One guard-hook PR went 563 → 947 lines over 7 review rounds and 12.7 h.)
7. **Measure cost**: every gate stage and review round writes its duration; the weekly audit reads them before adding any mechanism, which needs a requirement or an observed defect.
8. **Stop rule**: when consecutive review rounds return the same class of finding, stop after the second or third and give the user the value and the cost of continuing.

## Residual risk

State it always: writer and reviewer share a model family (blind spots can overlap); if the spec is wrong every component agrees with it. Mitigations: spec tests from the source (2), an independent oracle (5), the critic canary (9), dual-model review (15), human-approved scenarios (18), and for payroll a shadow run against real numbers before declaring it trustworthy.

## Audit style

- Brutally honest, numbers with evidence (`path:line`, command output), ranked by impact.
- Separate "keep (don't change)", "change (prioritized)", "don't do".
- Respect topics the user parked (check memory before raising them).
- One decision per question; record each answer where future sessions read it.

## Files

- `references/critical-review.md`: audit method, measurements, report template.
- `references/components.md`: how to build each part, tools per stack, the local gate pattern.
- `references/reviewer.md`: reviewer flow, round rules, review stamp, critic canary, findings journal.
- `references/auditor.md`: weekly auditor agent template.
- `references/handoff.md`: handoff size and structure, sources of truth, guessed contracts.
- `scripts/git-guard/`: installable 3-layer main protection with tests.
- `scripts/review-gate/`: installable review pipeline with tests (stamp with round budget, gate stamp, merge-guard and review-rounds hooks, findings log, spec guard, reviewer template, installer).
- `scripts/update_check.py`, `apply_hooks.py`, `references/settings-hooks.json`: update notice at session start on installed machines.
- `scripts/publish.sh`: publishes to github.com/AlbatrosPromotions/claude-quality-system-skill.
