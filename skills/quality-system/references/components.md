# Components: how to build each part

Numbers match the table in SKILL.md. "Proven" parts have working code in sos-scrum (`/Users/olimjon/Projects/sos-scrum`) to copy from.

## §0 Local gate when hosted CI is unavailable (proven)

Pattern from sos-scrum (`infra/ci/local-ci.sh`, `.claude/hooks/require-local-ci.sh`):
- One script runs every check in parallel lanes, prints a stage timing table and a budget (e.g. 600 s).
- On green it writes the **tree hash of the working tree** (tracked + untracked, honouring `.gitignore`, computed with a temporary index: `GIT_INDEX_FILE=tmp git add -A && git write-tree`) to `$(git rev-parse --git-path <prefix>-ci-ok)`; if files changed during the run, no stamp.
- A PreToolUse hook on `gh pr merge` compares the PR head commit's tree with the stamp; mismatch → block. Read the stamp from the session's `cwd` (worktrees have their own git dir).
- Pitfalls: macOS ships bash 3.2 — `[[ $x -eq 0 ]]` as the last command of a function does not trigger `set -e`; use `if …; then return 1; fi`. Two gate runs at once share databases and ports: forbid it. Load from parallel lanes can time out unit tests: raise per-test timeouts rather than serializing.

## 1. Gate canary (designed)

At the start of the gate run, a dry pass where each lane gets one step that must fail (`CI_CANARY=1`): the script must exit non-zero. If it exits 0, the gate itself is broken → red. Seconds of cost; prevents a repeat of "green but false".

## 2. Spec tests and `spec_guard` (designed)

- A separate agent that sees only the spec (requirement sections, AC, domain scenarios from 18) writes acceptance tests before implementation, into a dedicated place (e.g. `tests/Spec/`, `e2e/spec/`), committed with subject prefix `spec:`.
- `tools/spec_guard.py` in the gate: any commit in the PR range whose subject does not start with `spec:` and touches the spec dirs → red. A wrong spec test is fixed in its own `spec:` commit with a reviewer note.
- The coder makes them pass; it never edits them.

## 3. Mutation testing (manual form proven)

Run only on files changed in the PR (diff against `origin/main`), domain/application layers first; record the starting score, then never lower the threshold.

| Stack | Tool |
|---|---|
| PHP | Pest ≥ 3 `--mutate` (built in), Infection |
| JS/TS | Stryker (`@stryker-mutator/vitest-runner` / jest runner) |
| Python | mutmut, cosmic-ray |
| Go | gremlins, go-mutesting |
| Rust | cargo-mutants |
| Java/Kotlin | PIT |

Until a tool is wired, the manual form: for each new rule, revert/break it and show a test goes red (sos-scrum did this every session).

## 4. Reviewer + review stamp — see `reviewer.md`.

## 5. Independent oracle and property tests (designed)

- Money, time, payroll: a second implementation written by a different agent from the spec only, in a different language (Python in `tools/oracle/`), compared with the production code on thousands of seeded random cases including boundary dates (month ends, DST, business timezone). Disagreement = bug in one of them; both are suspects.
- State machines and engines: property tests (PHP eris, JS fast-check, Python hypothesis): unreachable states, immutability of published versions, ordering of validators.

## 6. Traceability (designed)

PR template line `Source: <spec §>, <arch §>, AC-NN`. The reviewer returns code that no source asks for (hardening without users, speculative options). A tracking matrix (AC → tests) in the gate keeps acceptance criteria honest (sos-scrum `tools/ac_matrix.py --check`).

## 7. Weekly audit (designed)

Fresh-context session: tenant isolation, authorization matrix vs catalogue, dependency advisories, architecture drift (module boundaries), tech debt, findings-journal rule candidates, critic canary (9), volume tests (13). Output: a fix list into the next sessions' messages.

## 8. Module READMEs (designed)

Per module: purpose (2 lines), entry points (routes, commands, listeners), invariants, data owned, where to start reading, links to ADRs. Updated in the same PR as the module.

## 9. Critic canary — see `reviewer.md`.

## 10. Findings→rule loop — see `reviewer.md`. Visual class: geometry checks in the accessibility/screenshot suite (e.g. `cornerBleeds`: no painted descendant outside a rounded container's corner; `horizontalOverflow`: no page scroll at 320 px).

## 11. Git guard (proven, installable)

`scripts/git-guard/` in this skill (`README.md` there). Three layers because each alone has a hole:
- PreToolUse hook reads the command text (catches intent early, explains the rule to the agent; misses git hidden inside `python -c`, `xargs`, scripts).
- git `pre-push` sees every push however invoked (misses `--no-verify`, which layer 1 blocks, and unset `core.hooksPath`, which layer 3 detects).
- History check: first-parent history of main after a baseline must be only "Merge pull request #N from" commits; also verifies `core.hooksPath` in the repo and submodules. Runs in the gate and at session start (`--fetch --warn-only`).
Server-side protection (paid GitHub plan or a public repo) is the only full lock; say so.

## 12. Strict modes and query counts (designed)

Laravel `Model::shouldBeStrict(! app()->isProduction())`; Django `assertNumQueries`; Rails `strict_loading`; TS `strict`. For every list endpoint: query count equal for 5 and 50 rows.

## 13. Volume tests (designed)

From the first list API: a seed command (e.g. 20 tenants × 50k rows × 10 history rows each), time budgets and `EXPLAIN` checks (no sequential scans on large tables, row-level-security predicates use indexes) for the 5 most important list endpoints; weekly, results in a perf log.

## 14–17. Rules (CLAUDE.md lines)

- 14: lead hands off at ~250–300k context.
- 15: on model change rerun the critic canary; money/security PRs get a second model's review.
- 16: monthly dependency session (`composer outdated --direct`, `pnpm outdated`, `pip list --outdated`…), one small PR per upgrade group through the full gate.
- 17: comments say "why" in 1–3 lines; history in ADRs/READMEs; long legacy comments are trimmed when the file is touched.

## 18. Domain scenarios (designed)

`docs/tests/scenarios/<domain>.md`: 10–20 numbered cases in plain language with expected outcomes ("post due 15:00, client returns it at 16:00 — who is late?"). A domain human (PM, team lead, accountant) approves them in 30–60 minutes per domain; approved cases become spec tests (2) and oracle fixtures (5). This is not code review; it is the only defence against a wrong specification.
