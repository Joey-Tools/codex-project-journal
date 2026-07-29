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
- The final two fresh-review findings are closed by binding rollout reads to no-follow/nonblocking descriptors with post-read identity and content revalidation, and by adding a Darwin `kqueue` child-status fallback for the supported Xcode Python 3.9 runtime.
- The subsequent fresh-review findings are closed by proving and revalidating waitable `SIGCHLD` semantics around every child/PID-fence operation and by classifying rollout object identity, access policy, and content drift independently.
- The latest fresh-review findings are closed by querying native Linux `sigaction` on reviewed LP64 glibc/musl ABIs before trusting a numeric PID/PGID fence, and by rejecting NUL-bearing rollout CWDs before any filesystem path or parent probe.
- The remaining rollout-enumeration finding is closed by matching candidate names before no-follow type inspection and reporting stable non-regular or vanished candidates as partial coverage.
- The final rollout-discovery findings are closed by filtering failures against the requested window only with reliable date/mtime evidence and by replacing path-reopening recursion with descriptor-relative directory traversal that binds object identity and access policy.

## Current State

- The skill frontmatter, body, UI metadata, templates, migration guidance, helper, and tests implement the adoption boundary and preserve concise top-level tracker and squash-merge target-branch semantics.
- The index parser ignores a structurally valid record whose path is exactly `docs/project_journal`, so a same-name file, symlink, or gitlink remains unadopted while real child entries retain normal validation.
- Repository discovery gives a still-inconclusive same-root adoption check one retry only when a later CWD retains at least one additional second of its deadline; two attempts per root is the hard cap, and auxiliary-only uncertainty does not trigger the retry.
- Repository discovery now streams both `sessions` and flat `archived_sessions`, consumes every physical copy sharing a rollout basename, unions their CWD evidence, and counts each logical rollout-to-repository association once.
- The sources share one 60-second absolute deadline plus filesystem-entry, bound-directory-depth, logical-rollout, total-byte, line-byte, record, distinct-CWD, JSON-depth, JSON-integer-digit, CWD-UTF-8-byte, CWD-component, and retained-error limits. Strict UTF-8 validation, NUL rejection, and the CWD caps occur before `Path` construction, existence checks, or parent fallback.
- Complete candidate coverage is explicit. A source I/O failure, non-empty record parse failure, invalid CWD, deadline, or cap preserves healthy rows already found but marks `coverage_status: partial` with bounded counters and structured `discovery_coverage`; when no repository was found, an inconclusive sentinel prevents partial coverage from looking like authoritative `[]`.
- The helper and hook rename primitive now reject every host other than macOS and Linux before Git selection or platform-specific libc lookup.
- Missing auxiliary paths remain authoritative negatives, while EACCES, EIO, and other inspection failures independently null `has_journal_dir`, `journal_count`, `has_index`, `index_ignored`, or `hooks_installed` and attach structured errno evidence.
- Repository resolution establishes one candidate deadline capped by the aggregate scan deadline. Adoption, journal enumeration, generated-index classification, exclude-path Git lookup and read, hook configuration lookup and reads, and index presence inspection all consume that same absolute deadline.
- Worktree generated-index, exclude, and hook reads use no-follow/nonblocking descriptors, require stable regular-file identity, and enforce hard retained-byte ceilings. A truly disappeared entry is an authoritative negative, while a deadline, EACCES, EIO, FIFO, symlink, directory, unstable file, or oversized input produces structured partial/limit evidence and leaves only the affected field unknown.
- Rollout enumeration now selects every `rollout-*.jsonl` name before no-follow type inspection. Stable symlinks, FIFOs, directories, and entries that disappear or fail inspection during enumeration become candidate-level partial-coverage errors unless their parsed date or enumerated candidate mtime reliably proves they predate the requested window; uncertain failures still count toward the shared error budget. Directory traversal opens each child relative to a held parent with `O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK`, caps bound depth at 256, and revalidates every retained directory's `(st_dev, st_ino)` identity plus `(st_uid, st_gid, permission mode)` access policy. Missing, unreadable, replaced, or policy-changed directories remain distinct failures, while timestamp, link-count, and child-entry churn alone are accepted. Regular candidates carry the observed object identity into a descriptor-bound `O_NOFOLLOW | O_NONBLOCK` open; the reader hashes the bounded first pass, rereads the same descriptor, and revalidates descriptor identity, path identity, size, and digest before accepting evidence.
- Session/archive dates are derived only relative to the explicit rollout root, so a similarly named dated outer ancestor cannot suppress an uncertain failure. Every directory scan/open/stat error handoff first revalidates the retained ancestor chain and prefers a proved ancestor replacement or access-policy change.
- Child supervision uses `waitid(..., WNOWAIT)` where available and registers a one-shot Darwin `EVFILT_PROC/NOTE_EXIT` observer immediately after launch when Xcode Python 3.9 lacks `os.waitid`. The observer proves exit without reaping; the final bounded `wait()` remains the sole exact return-code source, preserving process-group cleanup and zombie-free normal and timeout paths.
- Before any child starts, supervision requires `SIGCHLD` to have its default waitable disposition. Darwin and reviewed LP64 Linux x86_64/AArch64 glibc/musl ABIs also verify the native handler and reject `SA_NOCLDWAIT`; unknown Linux machine, libc multiarch, or word-size layouts fail closed before libc or `Popen`. That property is revalidated after launch and before kqueue `NOTE_EXIT`/`ESRCH`, non-reaping observation, final wait, and each numeric PID/PGID operation. Lost evidence prohibits process-group signalling and permits only a nonblocking direct-child reap whose status remains untrusted.
- Rollout enumeration now records `(st_dev, st_ino)`, `(st_uid, st_gid, permission mode)`, and size as separate protected properties. True descriptor/path replacement reports `object_replaced`, `object_changed`, or `path_replaced`; chmod/chown-style policy drift reports `access_policy_changed`; append, truncation, or digest drift reports `content_changed`.
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
- Descriptor-bound rollout focused coverage: 37 discovery tests passed in 67.952 seconds with Python 3.13.0.
- Child-status focused coverage: 13 process tests passed in 4.194 seconds; 29 cleanup tests passed with 2 skipped in 15.559 seconds.
- Xcode Python 3.9.6 compatibility coverage: 7 targeted rollout, Darwin observer, and real CLI adoption tests passed in 6.244 seconds. This supersedes the earlier evidence above that treated missing `WNOWAIT` as a runtime gate.
- Final Python 3.13 full suite after descriptor-bound rollout reads and Darwin child supervision: 238 passed, 2 skipped in 345.143 seconds.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9 `py_compile`, project journal validation, and `git diff --check` passed. The installed validator remained dependency-blocked as documented above; its local YAML/frontmatter/interface/resource fallback passed without installing another runtime.
- New ignored-SIGCHLD, `SA_NOCLDWAIT`, kqueue-`ESRCH`, real Xcode CLI, append/truncation, chmod/chown-style policy, digest, and replacement regressions: 11 passed in 3.158 seconds with Python 3.13.0 and 11 passed in 2.482 seconds with Xcode Python 3.9.6.
- Combined process coverage: 15 passed in 4.166 seconds. Cleanup coverage: 29 passed with 2 skipped in 12.587 seconds. Discovery coverage: 40 passed in 46.382 seconds.
- Final Python 3.13 full suite after SIGCHLD fencing and rollout property classification: 245 passed, 2 skipped in 275.279 seconds.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9 `py_compile`, project journal validation, and `git diff --check` passed. The installed validator again remained dependency-blocked by unavailable PyYAML/DNS; its equivalent YAML/frontmatter/interface/three-resource fallback passed without installing another runtime.
- Cleanup-phase identity-loss regression: 1 passed with Python 3.13.0 and proves that a lost PID/PGID fence during cleanup does not trigger a second numeric group probe or signal.
- Real Xcode Python 3.9.6 CLI/process coverage: 10 targeted SIGCHLD, kqueue, identity-loss, timeout, and descendant-cleanup tests passed in 4.066 seconds.
- The first final Python 3.13 full-suite attempt had one transient two-second Git version-probe timeout; that test passed alone in 3.029 seconds, and the clean full rerun passed 247 tests with 2 skipped in 250.461 seconds.
- Final Ruff check/format, Python 3.13 compile, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed.
- Linux `SA_NOCLDWAIT`, reviewed-layout, unknown-multiarch, post-spawn identity-loss, and NUL-CWD focused coverage: 16 passed with 1 platform skip in 3.541 seconds under Python 3.13.0; the equivalent Xcode Python 3.9.6 matrix passed 16 with 1 platform skip in 4.135 seconds. The isolated native Linux auto-reap proof is retained for supported Linux execution and was not executed on this macOS host.
- Final Python 3.13 full suite after Linux native-disposition and NUL-CWD hardening: 253 tests with 3 platform skips in 242.610 seconds.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9 `py_compile`, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed.
- Name-first rollout discovery coverage: all 44 `discover-repos` tests passed in 97.237 seconds with Python 3.13.0 and in 100.452 seconds with Xcode Python 3.9.6, including stable symlink/FIFO/directory candidates and an enumeration-time disappearance.
- Final Python 3.13 full suite after candidate enumeration hardening: 255 tests passed with 3 platform skips in 250.619 seconds.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9.6 `py_compile`, project journal validation, installed Joey/OpenAI skill validation, and `git diff --check` passed.
- Window-filtering and descriptor-relative directory traversal coverage: all 53 `discover-repos` tests passed in 80.296 seconds with Python 3.13.0 and in 82.839 seconds with Xcode Python 3.9.6. The focused regressions cover more than 32 reliably old failures followed by a healthy archive, uncertain-date failures beneath ordinary and misleading dated ancestors, bound-directory replacement and chmod, missing and symlinked intermediate directories, ancestor replacement during a scan failure, complete stacked-resource cleanup attempts, and accepted timestamp/child-entry churn.
- Final Python 3.13 full suite after rollout traversal hardening: 265 tests passed with 3 platform skips in 311.179 seconds.
- The Xcode Python 3.9.6 full suite ran all 265 tests in 311.420 seconds: 258 passed, 4 were platform-skipped, and three existing runtime-compatibility tests produced 2 failures plus 1 error. The same three results reproduced unchanged in 0.428 seconds from a clean archive of parent commit `57f3a81438ce5aabb63b31fde4d4c735e3b42f77`: copied Xcode Python cannot load its relative `Python3` library, Python 3.9 has no `BaseException.add_note` attribute for the test to patch, and one cleanup assertion does not accept `returncode=None`.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9.6 `py_compile`, project journal validation, installed Joey/OpenAI skill validation, and `git diff --check` passed.
