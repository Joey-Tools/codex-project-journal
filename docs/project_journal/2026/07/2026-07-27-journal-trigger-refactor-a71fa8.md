---
id: 20260727-a71fa8
title: Project Journal Trigger Refactor
status: completed
created: 2026-07-23
updated: 2026-07-29
branch: codex/journal-trigger-refactor
pr: https://github.com/Joey-Tools/codex-project-journal/pull/5
supersedes: []
superseded_by:
---

# Project Journal Trigger Refactor

## Summary

- Project journals now auto-apply only after repo policy or a valid tracked non-generated entry establishes adoption.
- A task spanning Codex sessions, a PR, or a durable workstream triggers an adoption assessment but does not authorize the first tracker; first adoption still requires an explicit product need.
- Automatic updates target the smallest applicable layer, while index generation and hook installation remain workflow-specific and opt-in.
- The three findings from GitHub Codex review `4784362350` are addressed without changing index-authoritative adoption semantics.
- The final fresh-review findings are closed by adding bounded active/archive session coverage, one aggregate discovery deadline and cap ledger, and explicit generated-index marker outcomes.
- The newest fresh-review findings are closed by making every non-empty rollout parse failure explicit, unioning same-name active/archive evidence without double-counting a logical rollout, and validating bounded strict-UTF-8 CWDs before path construction.
- The latest fresh-review finding is closed by carrying one absolute candidate deadline through every adoption, Git, and auxiliary file probe and by making generated-index, exclude, hook, and index inspections nonblocking, type-safe, and byte-bounded.

## Current State

- The skill frontmatter, body, UI metadata, templates, migration guidance, helper, and tests implement the adoption boundary and preserve concise top-level tracker and squash-merge target-branch semantics.
- The index parser ignores a structurally valid record whose path is exactly `docs/project_journal`, so a same-name file, symlink, or gitlink remains unadopted while real child entries retain normal validation.
- Repository discovery gives a still-inconclusive same-root adoption check one retry only when a later CWD retains at least one additional second of its deadline; two attempts per root is the hard cap, and auxiliary-only uncertainty does not trigger the retry.
- Repository discovery now streams both `sessions` and flat `archived_sessions`, consumes every physical copy sharing a rollout basename, unions their CWD evidence, and counts each logical rollout-to-repository association once.
- The sources share one 60-second absolute deadline plus filesystem-entry, logical-rollout, total-byte, line-byte, record, distinct-CWD, JSON-depth, JSON-integer-digit, CWD-UTF-8-byte, CWD-component, and retained-error limits. Strict CWD validation occurs before `Path` construction, existence checks, or parent fallback.
- Complete candidate coverage is explicit. A source I/O failure, non-empty record parse failure, invalid CWD encoding, deadline, or cap preserves healthy rows already found but marks `coverage_status: partial` with bounded counters and structured `discovery_coverage`; when no repository was found, an inconclusive sentinel prevents partial coverage from looking like authoritative `[]`.
- The helper and hook rename primitive now reject every host other than macOS and Linux before Git selection or platform-specific libc lookup.
- Missing auxiliary paths remain authoritative negatives, while EACCES, EIO, and other inspection failures independently null `has_journal_dir`, `journal_count`, `has_index`, `index_ignored`, or `hooks_installed` and attach structured errno evidence.
- Repository resolution establishes one candidate deadline capped by the aggregate scan deadline. Adoption, journal enumeration, generated-index classification, exclude-path Git lookup and read, hook configuration lookup and reads, and index presence inspection all consume that same absolute deadline.
- Worktree generated-index, exclude, and hook reads use no-follow/nonblocking descriptors, require stable regular-file identity, and enforce hard retained-byte ceilings. A truly disappeared entry is an authoritative negative, while a deadline, EACCES, EIO, FIFO, symlink, directory, unstable file, or oversized input produces structured partial/limit evidence and leaves only the affected field unknown.
- This is the repository's intentional first journal entry because the finalization task explicitly requires durable repo-owned validation and handoff evidence; no top-level tracker, generated index, or local hook is introduced.

## Next Steps

- None for this completed workstream.

## Evidence

- Original refactor range: `15521e327477444bf11e8b83502720fe4237aa8c..a71fa8cb0261c6e04fc79e77cadc668498442b5c`; fresh-review closure continues after journal commit `45a912083bc82557c582eaeb055fa2126d4e0c7e`.
- Focused exact-root adoption regressions: 3 passed in 7.883 seconds with Python 3.13.0.
- Full test suite: 189 passed, 2 skipped in 244.487 seconds with Python 3.13.0.
- `python3 -m py_compile scripts/project_journal.py tests/test_project_journal.py`: passed.
- `ruff check` and `ruff format --check` for the helper and tests: passed.
- Joey skill validation wrapper: 1 skill passed with no runtime errors.
- Project journal validation and `git diff --check`: passed.
- GitHub review follow-up: review `4784362350`; inline comments `3654974552`, `3654974557`, and `3654974559`.
- Review-finding focused suite: 14 passed in 0.452 seconds with Python 3.13.0 and 14 passed in 1.087 seconds with Xcode Python 3.9.6.
- Final Python 3.13 full suite: 198 passed, 2 skipped in 241.751 seconds.
- The Python 3.9.6 full-suite attempt hit the existing runtime gate because this host interpreter omits required POSIX `WNOWAIT`; its focused compatibility suite and `py_compile` both passed without installing another runtime.
- Final discovery/marker focused suite: 17 passed in 0.465 seconds with Xcode Python 3.9.6; the same focused coverage and all existing discovery regressions passed with Python 3.13.0.
- Final Python 3.13 full suite after the coverage and marker fixes: 215 passed, 2 skipped in 248.617 seconds.
- Xcode Python 3.9.6 `py_compile` passed for the helper and tests. Its fail-fast full-suite probe again stopped at the existing explicit `POSIX WNOWAIT status observation is unavailable` safety gate.
- New rollout-parse, duplicate-evidence, and pre-`Path` CWD-cap regressions: 8 passed in 10.238 seconds with Python 3.13.0 and 8 passed in 0.361 seconds with Xcode Python 3.9.6.
- Latest Python 3.13 full suite: 223 passed, 2 skipped in 317.097 seconds.
- Latest Python 3.13 and Xcode Python 3.9.6 `py_compile` checks passed for the helper and tests; the Xcode cache was redirected into a task-scoped worktree directory and removed afterward.
- Ruff check and format verification, Joey's installed OpenAI skill-validator wrapper, project journal validation, and `git diff --check` passed.
- New shared-deadline, FIFO, unsafe-file, oversize, and slow-Git regressions: 17 passed in 0.595 seconds with Python 3.13.0 and 17 passed in 0.742 seconds with Xcode Python 3.9.6.
- Combined discovery, hook, and Git-configuration regressions: 28 passed in 18.637 seconds with Python 3.13.0.
- Latest Python 3.13 full suite after auxiliary-probe hardening: 232 passed, 2 skipped in 254.705 seconds.
- Python 3.13.0 and Xcode Python 3.9.6 `py_compile` checks passed for the helper and tests.
- Final Python 3.13 full suite after README, skill, and journal synchronization: 232 passed, 2 skipped in 233.502 seconds.
- The installed OpenAI validator wrapper could not acquire PyYAML because sandbox DNS was unavailable and the local Python runtime did not provide the module. Its documented fallback passed by parsing `SKILL.md` frontmatter and `agents/openai.yaml`, enforcing the validator's frontmatter constraints, and verifying referenced resources; no external runtime was installed.
