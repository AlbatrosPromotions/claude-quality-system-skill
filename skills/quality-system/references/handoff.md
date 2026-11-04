# Handoff and documentation hygiene

In an autonomous project the docs are read by agents, not people; stale or contradictory text becomes wrong decisions. Hygiene is a quality control, not tidiness.

## Handoff file (e.g. `docs/handoff.md`)

- Size ≤ ~20 KB. The first project's handoff grew to 284 KB with 81 open questions, some contradicting the code ("audit table does not exist yet" weeks after it shipped). Every session paid for reading it and some facts misled.
- Structure: current state (short, links to docs), next session message (self-contained prompt), ≤ 10 open questions each with owner and date, usage before/after.
- Session logs go to an archive file or the changelog; module-specific notes go into that module's README or the relevant `docs/` file.
- Every session: remove answered questions, fix any fact it found stale, never append without pruning.
- Add a size check (e.g. in the docs checker) so growth fails the gate.

## Sources of truth

- One place per topic. Typical split: code + `docs/api` + ADRs are truth for built parts; the original specification is the plan for unbuilt parts, with a "built, truth: <link>" note per implemented section. The alternative (apply corrections to the frozen spec weekly) costs a recurring session; a growing "pending corrections" list (152 items in the first project) means the spec is no longer truth.
- ADRs record decisions (context, alternatives, consequence). Session reports belong in the changelog; an ADR per session inflates the set and hides real decisions.

## Guessed contracts

- UI built on APIs that do not exist yet is debt: freeze new mock-only screens once the guessed surface is large.
- When the backend API is designed, start from a diff list against the frontend's guessed contract; the server decides, the frontend parsers are then aligned.

## Lead context

- Hand off at ~250–300k tokens (leads of the first project reached 382k and ~480k). Long contexts degrade attention and multiply cost.
- Push bulk reading and writing to subagents; keep design, review and acceptance in the lead (see the `delegate` skill).

## Memory

- Durable user decisions and parked topics go to project memory with Why / How to apply, and are linked from the handoff so new sessions do not re-ask.
- Example parked topic: "infrastructure blocked by budget — do not raise until the user asks"; the audit must respect it.
