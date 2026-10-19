---
id: 20261019-legacy-journal-read-buffer
title: Legacy Journal Read Buffer
status: completed
created: 2026-10-19
updated: 2026-10-19
branch: wip/journal-legacy-buffer
pr: https://github.com/Joey-Tools/codex-project-journal/pull/21
supersedes: []
superseded_by:
---

# Legacy Journal Read Buffer

## Summary
- The journal read ceiling is 4 MiB for worktree validation/generation and indexed adoption checks. Ordinary journals should still stay within 1 MiB; the extra buffer accommodates longer legacy snapshots.
- The indexed aggregate ceiling remains 16 MiB. Unrelated configuration, hook, diagnostic, and discovery limits retain their existing values.

## Evidence
- A tracked-file inventory of HDR-streaming at `738d214c` found 77 journals and one file above 1 MiB. The largest was `docs/project_journal/2026/05/2026-05-05-legacy-tracker-snapshot-7a7bd5e.md`, at 1,907,774 bytes (about 1.82 MiB); the second largest was 206,745 bytes.
- The 4 MiB ceiling leaves 2,286,530 bytes above that legacy snapshot, about 2.20 times its measured size.
- The installed baseline helper rejected the snapshot with `journal file exceeds 1048576 bytes` before reading its content.
- Eight focused tests passed on CPython 3.13.0, covering a 2 MiB legacy journal through validate/generate/adoption, exact-cap reads, oversized sparse files, concurrent growth, batch aggregate limits, and documentation contracts. Ruff 0.13.2, skill validation, this repository's journal validation, and whitespace checks passed.
- With the candidate helper, HDR-streaming adoption succeeds with 73 root-level tracked entries and 72 valid entries. Whole-root validation now reaches an unrelated pre-existing `superseded_by` error in `2026-06-25-cross-process-transport-plan.md`; this change does not alter that metadata.
