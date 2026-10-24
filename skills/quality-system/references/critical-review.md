# Critical project review (audit mode)

Goal: an honest, evidence-backed picture of where the project really is, what is wrong, what to keep, what to change, and what not to do. The audit precedes any installation: the user's decisions shape the system.

## 1. Read the state (cheaply)

- Project CLAUDE.md, the handoff file (current state, next session, open questions), retros, the ADR list (titles only), the plan's phases, milestones and critical path.
- Project memory (parked topics, user decisions, team facts).
- Read section headings first (`grep -n '^#'`), then only the sections you need. Delegate bulk reading to a scout.

## 2. Measure (numbers beat impressions)

Adapt paths to the stack; batch into one or two shell calls.

```bash
# code vs tests per module (backend example)
for d in app/Modules/* app/Shared; do echo "$(find $d -name '*.php' -exec cat {} + | wc -l) $(find $d -name '*.php' | wc -l) $d"; done | sort -rn
find tests -name '*.php' -exec cat {} + | wc -l
# frontend: real features vs mock-only, generated code
for d in src/app/features/*; do echo "$(find $d -name '*.ts' ! -name '*.spec.ts' -exec cat {} + | wc -l) $d"; done | sort -rn
find src -type d -name mock | xargs -I{} sh -c 'echo "$(find {} -name "*.ts" -exec cat {} + | wc -l) {}"'
# docs weight, handoff size, open questions, ADR rate
find docs -name '*.md' -exec cat {} + | wc -c; wc -c docs/holat.md
sed -n '/^## Ochiq savollar/,/^## /p' docs/holat.md | grep -c '^- '
ls docs/adr | wc -l
# pace and history
git log --merges --oneline | grep -c 'Merge pull request'
git log --since=<start> --format='%ad' --date=short | sort | uniq -c
git log --first-parent --format='%h %s' origin/main | grep -v 'Merge pull request'   # direct commits to main
# guards and risky patterns
cat .claude/settings.json; git config core.hooksPath
git ls-files | grep -iE '\.env($|\.)|secret|\.pem$|\.key$'
grep -rnE '(whereRaw|DB::raw|DB::statement|->select)\(.*\{\$' app | head     # interpolated SQL
grep -rn 'preventLazyLoading\|shouldBeStrict' app bootstrap tests             # strict mode
grep -rliE 'glitchtip|sentry|grafana|loki|prometheus|uptime' infra config     # observability
```

Also note: CI duration trend, whether hosted CI runs at all, real-backend E2E count, the largest files vs the project's size limits, lead context sizes in the usage log.

## 3. Compare plan and execution

- **Critical path**: has it started? (In sos-scrum the workflow engine package was empty and five domain modules were 15-line stubs after 37 sessions, while infrastructure was polished.)
- **Vertical vs horizontal**: the plan said "vertical slices"; was it built as deep horizontal layers with an empty domain?
- **Guessed contracts**: how much UI is built on APIs that do not exist yet? That is reconciliation debt.
- **YAGNI**: hardening mechanisms with zero users (delivery tokens, replay tools, archive policies…). Do not delete built and tested ones; stop adding new ones without a requirement or a real incident.
- **Data that must exist from day 1**: if the business pain needs a metric (e.g. on-time rate), the history that feeds it must be recorded from the first version; it cannot be reconstructed later. Check the plan does not schedule the measuring tool after the pilot that needs it.
- **Business pain vs order**: is the pain the system exists for addressed early?

## 4. Human factors and process

- Who actually reviews the code? Is there a record? Does the plan's estimate assume human review that is not happening? (If review is skipped, plan dates look faster than they are.)
- Gate integrity: who attests "green"? Has a failure ever been hidden? Single machine?
- Sources of truth: frozen spec vs code vs ADRs vs handoff — how many places say different things? Pending correction lists?
- Handoff bloat and stale facts (examples of contradictions); ADR inflation (session reports filed as ADRs).
- Lead context sizes (attention decay above ~250–300k).

## 5. Report template (in the user's language)

1. **Verdict** in 2–3 sentences: strengths, the main misdirection, the biggest risk.
2. **Numbers** table (code, tests, docs, PRs, empty domain modules, mock-only UI, handoff size, open questions).
3. **Shortcomings**, ranked by severity, each with evidence and consequence.
4. **Keep — don't change** (what is good and what would be wasteful to reverse: stack, isolation model, contract tests, mutation habit…).
5. **Change — prioritized**, concrete (files, commands, which session).
6. **Don't do** (scope traps before the pilot, more mock screens, mechanical doc work).
7. Offer to publish the report as a page only if it will be shared; otherwise answer in chat.

Then ask the decisions one at a time (the next-step box), record answers in memory and the handoff, and build only what was approved.

## 6. Second pass ("what else?")

When the user asks for more, look where the first pass did not: git protection (direct pushes possible?), strict modes, volume/performance timing, lead context, model-change drift, dependency rhythm, comment readability, spec-error risk (the only place a human minute pays off), product sequencing (pilot order, metrics from day 1, dogfooding the system on its own development).
