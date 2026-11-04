# Changelog

All notable changes to the quality-system skill, newest first. The skill has no version numbers, so entries are dated. Format: Keep a Changelog.

## 2026-11-04

### Added
- Review-gate kit `scripts/review-gate/`: `review_stamp.py` (review-round budget, `--check`, `--allow`), `gate_stamp.py`, the merge-guard and review-rounds hooks, `quality_log.py`, `spec_guard.py`, the reviewer agent template and `install.py`, all with tests.
- Update propagation like the `delegate` skill: `scripts/update_check.py`, `references/settings-hooks.json`, `scripts/apply_hooks.py`, `scripts/publish.sh`, and the install repo's `install.sh` with `--hooks`.
- `references/auditor.md`.

### Changed
- The skill text is project-agnostic: no paths or names of the first project, the kits are driven by `.claude/quality/config.json`.
- Gate budget rules in SKILL.md (measured on 2026-11-04): one review per PR, a second only after a blocker or major finding, a third only with the user's yes; mutation testing is out of the gate.

## 2026-11-03

### Changed
- git-guard layer 1 reads more shell forms: `if/then`, `for/while`, `{ }`, `!`, unknown wrappers such as `xargs` and `find -exec`, shell fed through stdin, `$'…'`, `submodule foreach`, `rebase --exec`, aliases.

### Fixed
- The git-guard README and `references/components.md` now state correctly what layers 1 and 2 catch.

## 2026-10-24

### Added
- First release: audit method, the 18-part system, reviewer and canary templates, the git-guard kit (3 layers, 76 tests).
