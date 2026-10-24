# Independent reviewer, critic canary, findings journal

## Reviewer agent (component 4)

Create `.claude/agents/<project>-reviewer.md` (or a user-level `opus-reviewer.md`):

```markdown
---
name: opus-reviewer
description: Independent senior code reviewer for a PR. Fresh context, read-only. Receives the diff, the cited spec sections and CLAUDE.md — never the writer's brief or reasoning. Returns findings with severity.
model: opus
tools: Read, Grep, Glob, Bash
---
You are a senior engineer with 10+ years of experience reviewing a pull request you did not write.
Inputs: `git diff origin/main...HEAD`, the spec sections named in the PR's "Source:" line, CLAUDE.md.
You may run tests and scripts; you must not edit files or change git state.
Check, in this order:
1. Correctness against the spec, edge cases, error paths.
2. Tenant isolation / row-level security; authorization (policies, scope filters) on every new endpoint.
3. Money as integers in minor units; time as timestamptz with business-timezone boundaries.
4. Error format, idempotency of mutations and consumers.
5. Security: injection, secrets, PII or salary in logs, unsafe file handling.
6. Untraceable code: mechanisms not required by the cited source (return them).
7. Readability and size limits; comments say "why" only.
8. Tests: would they fail if the code were wrong? Use the mutation report; point at survivors.
Output: a list of findings, each `severity (blocker|major|minor) — file:line — problem — failure scenario — fix`. No praise, no summary of the diff. If nothing survives scrutiny, say "no findings" and list what you checked.
```

Lead's job: spawn it with the PR number and the Source line only, apply or reject each finding with a reason, fix blockers, re-run it on the new head. For auth, tenant, file or money PRs add a security pass (e.g. the `security-review` skill); for money/security PRs add a second model (component 15) and merge the findings.

**Review stamp**: after a clean review write `<git-dir>/<prefix>-review-ok` = `<tree-hash> <PR> <date>`; the merge hook requires both the gate stamp and the review stamp to equal the PR head tree. The git guard blocks manual writes to `*-review-ok`.

## Critic canary (component 9)

Weekly (in the audit session) and after a model change:
1. Branch from main (throwaway, never merged), plant 3–5 defects from different categories, each a realistic one-line slip. Rotate the set every time so the reviewer cannot memorize it. Examples:
   - list endpoint without the scope filter / a query bypassing row-level security;
   - request without validation (no FormRequest/schema);
   - money as float; time without timezone;
   - email or salary written to a log;
   - endpoint without a policy; mutation without idempotency key handling.
2. Open it as a normal PR description (with a plausible Source line); spawn the reviewer as usual. It must not know it is a canary.
3. Log `planted / caught / missed per category` in the findings journal. A missed blocker, or a catch rate below the previous run → strengthen the reviewer prompt (add the missed category explicitly), rerun.
4. Delete the branch.

## Findings journal and the rule loop (component 10)

File `docs/ops/sifat-jurnali.md` (or `docs/quality-log.md`):

```markdown
| Date | PR | Category | Severity | Caught by | Rule created |
|---|---|---|---|---|---|
| 2026-10-24 | #49 | ui.corner-bleed | major | user (screenshot) | a11y `cornerBleeds` geometry test |
| 2026-10-24 | #34 | ci.hidden-failure | blocker | lead (37th session) | gate canary (planned) |
```

Rules:
- Every finding from the reviewer, canary, weekly audit or the user gets a row.
- A category's second occurrence, or any blocker, becomes a deterministic check: arch test, static-analysis or lint rule, geometry test, script. Prove the rule by reverting the fix: the rule must go red.
- The weekly audit scans the journal for rule candidates.
- Goal: the same mistake is never found twice by eye.

Examples of findings turned into rules (sos-scrum): hard-coded past dates in tests → `TestClockTest`; rounded-corner bleed → `cornerBleeds`; 320 px horizontal scroll → `horizontalOverflow`; direct push risk → git guard.
