# Independent reviewer, critic canary, findings journal

## Reviewer agent (component 4, proven, installable)

`scripts/review-gate/install.py` (Install order step 4 in `SKILL.md`) renders the kit template `scripts/review-gate/reviewer.md` into `.claude/agents/<reviewer>.md`. `<reviewer>` is config `reviewer` (default `opus-reviewer`; inside the project it overrides a global agent of the same name). The agent has fresh context, the strongest model and `tools: Read, Grep, Glob, Bash`, and never sees the writer's brief or reasoning. `<prefix>` below is config `prefix` (default `quality`).

Flow:
1. The lead spawns it with only the PR number and the repo directory. The hook `review-rounds.py` (PreToolUse on Agent) refuses the spawn when the round budget is used up.
2. The reviewer's first command is `python3 .claude/quality/review_stamp.py --pr N --check`. Exit 4 means the budget is used up: it reviews nothing and returns the message.
3. It reads the PR (`gh pr view`), the diff against the merge-base with the base branch, the sections named in the PR's `Source:` line and CLAUDE.md, then reviews correctness against the cited source, security, data and contracts, tests that could fail, gates and tooling failing closed, measurements and project rules. On gate or tooling PRs a bypass form that can only be written on purpose is at most minor (`SKILL.md`, Gate budget 6). When a changed path matches config `sensitive`, a security pass is mandatory and the report says `security=done`, otherwise `security=n/a`.
4. It ends with `python3 .claude/quality/review_stamp.py --pr N --head <sha> --report <file>`; the report's last line is `VERDICT: blockers=<n> major=<n> minor=<n> security=<done|n/a>`. The script first checks the PR (open, based on the base branch, head equal to `--head`). With blockers it posts the report plus the marker `<!-- <prefix>-review ... round=k -->` as a PR comment, deletes this PR's stamp and exits 3. Otherwise it also requires a `Source:` line in the PR body (config `source_labels`) and `security=done` when a changed path matches `sensitive`, posts the comment, and writes the review stamp `<git-dir>/<prefix>-review-ok-<PR>` = `<tree> <PR> <head> <time> round=k blockers=b major=m minor=n`.
5. Only the reviewer agent may run the `--head`/`--report` form: the merge guard blocks it for anyone else and blocks `gate_stamp.py --write` outside the gate; the git guard blocks shell writes to the stamp files. `--check` and `--allow` are open to everyone.

Rounds (round k = marker comments already on the PR + 1, so the count survives sessions and machines):
- Round 1 is mandatory for every PR.
- Round 2 only if round 1 found blockers or majors; it reviews the new head.
- Round 3 and later only with the user's yes, which the lead records with `python3 .claude/quality/review_stamp.py --pr N --allow [--round K]` (K defaults to 3 and must be at least 3). One allowance covers one round and is consumed when that round's comment is posted.
- Outside the budget `review_stamp.py` exits 4 and writes nothing.
- After a clean round 1 (blockers 0, major 0) later commits go through the gate only.

Merge guard (`.claude/hooks/merge-guard.py`, PreToolUse on Bash) lets `gh pr merge N` through only when:
- the gate stamp `<prefix>-ci-ok` holds the tree of the PR head (`components.md` §0);
- the review stamp `<prefix>-review-ok-N` exists for this PR in the current format, with blockers 0 and, for a round-1 stamp, major 0;
- the stamp's head is the PR head or its ancestor, so gate-only commits after a clean review still merge.

It also blocks merge forms it cannot read safely: another repo (`-R`, `GH_REPO`), a target that is not a plain number, `gh pr merge` inside `bash -c`, `eval` or `$(...)`. Merge with the plain form `gh pr merge <N> ...` from the project directory.

Lead's job: spawn the reviewer with the PR number and the repo directory only, apply or reject each finding with a reason, fix blockers, re-run it on the new head. For auth, tenant, file or money PRs add a security pass (e.g. the `security-review` skill); for money/security PRs add a second model (component 15) and merge the findings. A repo nested as a submodule (config `submodules`) has its own PRs, stamps and rounds: name it in the spawn prompt and the reviewer works inside it.

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

## Findings journal and the rule loop (component 10, proven)

File `docs/quality-log.md` (config `quality_log.file`), section `## Findings`, English columns exactly:

```markdown
| Date | PR | Category | Severity | Caught by | Rule |
|---|---|---|---|---|---|
| 2026-10-24 | #49 | ui.corner-bleed | major | user (screenshot) | `web/e2e/geometry.spec.ts` |
| 2026-10-24 | #34 | ci.hidden-failure | blocker | lead | planned: gate canary |
```

Date is `YYYY-MM-DD`, PR is `#N` or `-`, Category is a lower-case `area.kind` with at least one dot, Severity is `blocker`, `major` or `minor`. The Rule cell is one of:
- `-` (no rule yet);
- `planned: <text>`, live for 7 days after the row's date;
- `accepted: <reason>`, not allowed for a category that has a blocker;
- a named check in backticks: a git-tracked test or check file (under test, tools, scripts, hook or workflow directories, or matching a test-file pattern such as `test_*.py` or `*.spec.ts`; never a `.md` file; paths are overridable with `quality_log.check_prefixes`, `check_patterns`, `check_files`), or a gate stage name when `quality_log.gate_script` is set.

`python3 .claude/quality/quality_log.py --check [--file PATH] [--today YYYY-MM-DD] [--root PATH] [--config PATH]` (run it in the gate) enforces:
- a category with two or more rows, or with any blocker, needs a named check, an `accepted:` row (never for blocker categories) or a live `planned:` row;
- an expired `planned:` (older than 7 days) is red on its own;
- two or more rows of a category after its last closing row (named, `accepted:` or live `planned:`) are red: the rule did not work or the closure went stale;
- duplicate rows (first five cells equal) are red, and so is a table interrupted by a non-table line followed by more rows;
- system-minor rule: when `quality_log.minor_rule_from` is a date, a `minor` row dated on or after it whose category starts with one of `quality_log.system_prefixes` (`ci`, `docs`, `test`, `review`, ... by default) is red, because system-side minor findings stay in the PR comment.

An optional section `## Critic canary` holds the results of the routine above: columns `Date | Branch | Planted | Caught | Missed (category) | Rate | Note`; Planted and Caught are integers, Caught is at most Planted, Rate is `Caught/Planted`.

Rules:
- Blockers, majors and product findings (any severity) from the reviewer, canary, weekly audit or the user get a row; system-side minor findings stay in the PR comment.
- A category's second occurrence, or any blocker, becomes a deterministic check: arch test, static-analysis or lint rule, geometry test, script. Prove the rule by reverting the fix: the rule must go red.
- The weekly audit scans the journal for rule candidates.
- Goal: the same mistake is never found twice by eye.

Examples of findings turned into rules (from the first project): hard-coded past dates in tests → `TestClockTest`; rounded-corner bleed → `cornerBleeds`; 320 px horizontal scroll → `horizontalOverflow`; direct push risk → git guard.
