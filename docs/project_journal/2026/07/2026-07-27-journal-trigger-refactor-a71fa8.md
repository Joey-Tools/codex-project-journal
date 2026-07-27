---
id: 20260727-a71fa8
title: Project Journal Trigger Refactor
status: completed
created: 2026-07-23
updated: 2026-07-27
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

## Current State

- The skill frontmatter, body, UI metadata, templates, migration guidance, helper, and tests implement the adoption boundary and preserve concise top-level tracker and squash-merge target-branch semantics.
- The index parser ignores a structurally valid record whose path is exactly `docs/project_journal`, so a same-name file, symlink, or gitlink remains unadopted while real child entries retain normal validation.
- Repository discovery gives a still-inconclusive same-root adoption check one retry only when a later CWD retains at least one additional second of its deadline; two attempts per root is the hard cap, and auxiliary-only uncertainty does not trigger the retry.
- Repository discovery now streams both `sessions` and flat `archived_sessions`, deduplicates an active/archive rollout basename, normalizes harmless CWD aliases before caching, and shares one 60-second absolute deadline plus filesystem-entry, rollout, total-byte, line-byte, record, distinct-CWD, JSON-depth, and retained-error limits across both sources.
- Complete candidate coverage is explicit. A source I/O failure, deadline, or cap preserves healthy rows already found but marks `coverage_status: partial` with bounded counters and structured `discovery_coverage`; when no repository was found, an inconclusive sentinel prevents partial coverage from looking like authoritative `[]`.
- The helper and hook rename primitive now reject every host other than macOS and Linux before Git selection or platform-specific libc lookup.
- Missing auxiliary paths remain authoritative negatives, while EACCES, EIO, and other inspection failures independently null `has_journal_dir`, `journal_count`, `has_index`, `index_ignored`, or `hooks_installed` and attach structured errno evidence.
- Worktree generated-index classification reads only a bounded three-line prefix. A truly disappeared entry is skipped, while EACCES, EIO, a dangling symlink, a directory, or an oversized marker produces a dedicated structured error and leaves `journal_count` unknown.
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
