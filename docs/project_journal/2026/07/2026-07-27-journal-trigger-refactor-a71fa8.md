---
id: 20260727-a71fa8
title: Project Journal Trigger Refactor
status: completed
created: 2026-07-23
updated: 2026-08-03
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
- The latest fresh-review findings are closed by querying native Linux `sigaction` on reviewed LP64 glibc ABIs before trusting a numeric PID/PGID fence, failing closed on musl before libc or child-process access, and rejecting NUL-bearing rollout CWDs before any filesystem path or parent probe.
- The remaining rollout-enumeration finding is closed by matching candidate names before no-follow type inspection and reporting stable non-regular or vanished candidates as partial coverage.
- The final rollout-discovery findings are closed by filtering failures against the requested window only with reliable date/mtime evidence and by replacing path-reopening recursion with descriptor-relative directory traversal that binds object identity and access policy.
- The latest compatibility and cleanup-priority findings are closed by using a relocatable native Git fixture across supported Python runtimes, reporting the final known child return code, and retaining directory cleanup failures as bounded evidence beneath the already-proved inspection failure.
- The newest two fresh-review findings are closed by bounding retained validation-path labels and aggregate issue bytes, and by preserving parse, limit, replacement, access-policy, and generator-exit primaries when rollout descriptor cleanup also fails.
- The final three fresh-review findings are closed by bounding retained rollout associations across resolved and unresolved candidates, enforcing direct-child-only archive discovery, and integrating the Git source-descriptor close into launch preparation cleanup and exception precedence.
- The two post-head fresh-review findings are closed by preserving snapshot-creation failures across every descriptor close and by replacing the fixed two-second Git version probe with the shared initialization deadline plus non-cacheable transient classification.
- The hosted Linux Python 3.14 follow-up is closed by excluding only libc-injected `SA_RESTORER` trampoline metadata from restoration equivalence while retaining exact handler, mask, user-visible flag, `SA_NOCLDWAIT`, and real child-waitability checks.
- A later traversal audit was traced to an earlier pre-fix head; two missing direct regressions now pin ancestor object-identity and access-policy failures ahead of coincident child open/stat errors even at the one-error coverage budget boundary.
- The final hook-install fresh-review finding is closed by revalidating the complete bound hooks ancestor chain after the final installed-target snapshot and before recording the transaction as verified.
- The effective-hook follow-up is closed by re-reading the exact repository-scoped Git configuration before commit and after the final installed-target snapshot, comparing the semantic destination plan, and keeping changed versus unverified configuration failures distinct.
- The three current-head findings are closed by committing CWD evidence only after a rollout's second-pass verification and descriptor close, skipping exactly the two owner-directory DAC replacement tests under UID 0, and classifying failed-Git `.git` entries through one bounded same-deadline descriptor-relative ancestor scan.
- The post-handoff repository-resolution audit is closed by replacing every post-Git pathname marker/parent lookup with held-directory-FD operations, so a transient final-symlink A(marker)→B(no marker)→A retarget cannot mix objects and produce an ordinary non-repository result.
- The repository-resolution owner now distinguishes registered/uncommitted descriptors from close-dispatched descriptors. Its close handoff first queries the entry signal mask without changing it, blocks `SIGINT` plus managed termination signals, commits ownership and dispatches `close(2)`, retires the numeric FD, and only then restores the mask. A second-step mask exception attempts one best-effort rollback to the known entry mask before ownership commits; if rollback also fails, the original failure reports the thread mask state as unverified. Persistent acquisition failure leaves ownership explicit and a drain visits its entry-time FD snapshot once rather than spinning. This exact-once property is deliberately scoped to already-registered FDs and ordinary selected POSIX signal delivery in the single-threaded CLI path, not the `open(2)`-return-to-registration window, trace/thread-state `BaseException` injection, or a pre-kernel mocked close.
- Indexed-path diagnostics now share one raw-byte-stable 4 KiB display-label rule across validation, `cat-file`, frontmatter, and semantic failures; failed `ls-files` stdout is represented by byte-count/SHA-256 evidence rather than echoed.
- Partial discovery now retains the full coverage/error aggregate on one deterministic anchor only, gives every partial row a stable reference to it, and preserves row-local errors without multiplying the aggregate by the row count.
- The final GitHub Codex finding is closed by requiring secondary Git metadata to prove that a repository beneath `$CODEX_HOME/worktrees` is standalone or maps to one distinct source root; every unverified mapping is now an inconclusive repository-resolution row without install/generate commands.
- The subsequent fresh named-single finding is closed by making the discovery caller explicitly own both yielded iterators, surfacing generator-close failures, and delaying coverage serialization until ordered inner/outer cleanup evidence has attached to the frozen primary.
- The final hook-binding follow-up is closed by retiring the complete entry-time descriptor-owner set before restoring the signal mask, preserving the active error across an inter-close interruption, and making repeated cleanup unable to re-close reused numeric descriptors.
- The current-head GitHub Codex CWD finding is closed by treating rollout CWDs as literal filesystem paths, so relative and tilde-prefixed values remain unexpanded non-repository evidence instead of allowing an unknown-user alias to abort discovery.

## Current State

- The skill frontmatter, body, UI metadata, templates, migration guidance, helper, and tests implement the adoption boundary and preserve concise top-level tracker and squash-merge target-branch semantics.
- The index parser ignores a structurally valid record whose path is exactly `docs/project_journal`, so a same-name file, symlink, or gitlink remains unadopted while real child entries retain normal validation.
- Repository discovery gives a still-inconclusive same-root adoption check one retry only when a later CWD retains at least one additional second of its deadline; two attempts per root is the hard cap, and auxiliary-only uncertainty does not trigger the retry.
- Repository discovery now streams recursive `sessions` plus only direct `archived_sessions` rollout children, ignores nested archive directories before no-follow stat or traversal, consumes every accepted physical copy sharing a rollout basename, unions their CWD evidence, and counts each logical rollout-to-candidate association once.
- The sources share one 60-second absolute deadline plus filesystem-entry, bound-directory-depth, logical-rollout, total-byte, line-byte, record, distinct-CWD, 131,072 retained resolved-or-unresolved rollout-association, JSON-depth, JSON-integer-digit, CWD-UTF-8-byte, CWD-component, and retained-error limits. Strict UTF-8 validation, NUL rejection, and the CWD caps occur before `Path` construction, existence checks, or parent fallback.
- Complete candidate coverage is explicit. Each rollout buffers normalized distinct CWDs in first-seen order and commits them only after the second read, final descriptor/path/content revalidation, and descriptor close succeed. A failed rollout contributes no pending rows but does not roll back aggregate counters; a source I/O failure, non-empty record parse failure, invalid CWD, deadline, or cap preserves rows committed by earlier verified rollouts and marks `coverage_status: partial`. One deterministic final-sort anchor carries the full structured `discovery_coverage` object and stable `coverage_id`; every partial row carries `discovery_coverage_ref`, keeps any row-local error, and resolves to that anchor. When no repository was found, the inconclusive sentinel carries both the full object and its reference so partial coverage cannot look like authoritative `[]`.
- The helper and hook rename primitive now reject every host other than macOS and Linux before Git selection or platform-specific libc lookup.
- Missing auxiliary paths remain authoritative negatives, while EACCES, EIO, and other inspection failures independently null `has_journal_dir`, `journal_count`, `has_index`, `index_ignored`, or `hooks_installed` and attach structured errno evidence.
- Repository resolution establishes one candidate deadline capped by the aggregate scan deadline. Adoption, journal enumeration, generated-index classification, exclude-path Git lookup and read, hook configuration lookup and reads, and index presence inspection all consume that same absolute deadline. When `git rev-parse` fails, the helper opens the retained lexical current path once with required directory/close-on-exec/nonblocking flags while permitting a stable final symlink, then requires `fstat` to match the pre-Git `(st_dev, st_ino)`. It inspects `.git` and binds `".."` only relative to held directory descriptors, with no-follow required for every parent, at most current plus parent open, parent acquired before current closes, and `MAX_DISCOVERY_CWD_COMPONENTS + 1`, root, and starting-device bounds. One authoritative owner records every acquired descriptor; current/parent locals are only aliases. For already-registered FDs, its signal-fenced uncommitted/close-dispatched state makes an ordinary selected signal before successful fence acquisition retryable without retrying a numeric FD after `close(2)` dispatch. Each drain invocation is snapshot-bounded, retains unresolved pre-close ownership, preserves any exact active `BaseException`, and persists the first no-primary close failure/cause across later cleanup interruption. Early recovery additionally requires an owner-marked close attempt and rejects the identical ambient exception recorded at close start, so the surrounding Git/classification error cannot be relabeled as a close failure. This property excludes the acquisition-to-registration bytecode window and arbitrary trace/thread-state injection. Only a complete point-in-time no-marker scan with successful cleanup returns ordinary non-repository. Marker identity/type, inspection or deadline uncertainty, persistent `ENOTDIR`, initial identity drift, scan limit, and close failure remain structured `repository_resolution_failed` evidence; lexical marker spellings are explicitly `path_unverified`, and cleanup faults cannot replace an active primary. This is descriptor-bound post-Git ancestry proof, not a continuous lock across Git.
- Worktree generated-index, exclude, and hook reads use no-follow/nonblocking descriptors, require stable regular-file identity, and enforce hard retained-byte ceilings. A truly disappeared entry is an authoritative negative, while a deadline, EACCES, EIO, FIFO, symlink, directory, unstable file, or oversized input produces structured partial/limit evidence and leaves only the affected field unknown.
- Rollout enumeration now selects every `rollout-*.jsonl` name before no-follow type inspection. Stable symlinks, FIFOs, directories, and entries that disappear or fail inspection during enumeration become candidate-level partial-coverage errors unless their parsed date or enumerated candidate mtime reliably proves they predate the requested window; uncertain failures still count toward the shared error budget. Directory traversal opens each child relative to a held parent with `O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK`, caps bound depth at 256, and revalidates every retained directory's `(st_dev, st_ino)` identity plus `(st_uid, st_gid, permission mode)` access policy. Missing, unreadable, replaced, or policy-changed directories remain distinct failures, while timestamp, link-count, and child-entry churn alone are accepted. Regular candidates carry the observed object identity into a descriptor-bound `O_NOFOLLOW | O_NONBLOCK` open; the reader hashes the bounded first pass, rereads the same descriptor, and revalidates descriptor identity, path identity, size, and digest before accepting evidence.
- Session/archive dates are derived only relative to the explicit rollout root, so a similarly named dated outer ancestor cannot suppress an uncertain failure. Every directory scan/open/stat error handoff first revalidates the retained ancestor chain and prefers a proved ancestor replacement or access-policy change.
- Child supervision uses `waitid(..., WNOWAIT)` where available and registers a one-shot Darwin `EVFILT_PROC/NOTE_EXIT` observer immediately after launch when Xcode Python 3.9 lacks `os.waitid`. The observer proves exit without reaping; the final bounded `wait()` remains the sole exact return-code source, preserving process-group cleanup and zombie-free normal and timeout paths.
- Before any child starts, supervision requires `SIGCHLD` to have its default waitable disposition. Darwin and reviewed LP64 Linux x86_64/AArch64 glibc ABIs also verify the native handler and reject `SA_NOCLDWAIT`; musl, unknown Linux machine, libc multiarch, or word-size layouts fail closed before libc or `Popen`. That property is revalidated after launch and before kqueue `NOTE_EXIT`/`ESRCH`, non-reaping observation, final wait, and each numeric PID/PGID operation. Lost evidence prohibits process-group signalling and permits only a nonblocking direct-child reap whose status remains untrusted.
- Linux restoration coverage treats `SA_RESTORER` and its restorer pointer as libc-private signal-trampoline metadata, because glibc may add that bit while reinstalling an otherwise identical action. The protected property remains the handler, complete signal mask, every other flag bit, exact `SA_NOCLDWAIT` state, and observable ability to reap a newly forked child.
- Rollout enumeration now records `(st_dev, st_ino)`, `(st_uid, st_gid, permission mode)`, and size as separate protected properties. True descriptor/path replacement reports `object_replaced`, `object_changed`, or `path_replaced`; chmod/chown-style policy drift reports `access_policy_changed`; append, truncation, or digest drift reports `content_changed`.
- Directory traversal closes bound frames and descriptors before handing off a selected structured inspection failure. Iterator or descriptor cleanup faults are retained in bounded `cleanup_errors` evidence and exception notes without replacing a proved object-identity or access-policy failure; when no primary failure exists, cleanup faults still propagate.
- Indexed validation retains the exact invalid path for structured validity decisions, but every user-visible indexed-path diagnostic uses one valid UTF-8 display label capped at 4 KiB of final UTF-8-with-`backslashreplace` bytes. A short non-UTF-8 raw path is displayed with stable literal surrogate escapes while its structured `raw_path`/`rel_path` remains exact; larger values become a stable JSON `path_ref` whose byte count and SHA-256 bind the raw Git path bytes. The same label covers `cat-file` constructor/header/framing/finish errors, frontmatter limits, semantic list limits, ordinary validation errors, and the worktree loader. A failed `git ls-files` never echoes stdout; an empty-stderr failure reports only a stable stdout byte-count/SHA-256 reference. Retained issue text keeps its independent existing 1 MiB aggregate cap.
- Candidate-binding and rollout-extraction descriptor cleanup uses the same close-preserving helper as directory cleanup. A close fault is bounded and attached as `cleanup_errors` to the active parse, limit, replacement, access-policy, or generator-exit error instead of replacing that primary; an unaccompanied close fault still propagates.
- Git launch preparation closes its source-snapshot descriptor before returning the prepared launch. A close fault remains secondary to an active preparation error; after otherwise successful preparation, it becomes the structured primary while the launch directory is removed or its cleanup locator is retained as bounded evidence.
- Git launch-source close uses the same two-stage POSIX signal fence and close-dispatch ownership rule as repository resolution. A pre-fence interruption leaves the source FD owned for one safe drain; a post-dispatch signal is delivered only after the source ownership is retired, so cleanup cannot re-close a reused numeric FD. If both fence acquisition and the one drain fail, the exact primary reports that the source descriptor remains owned rather than claiming cleanup completed.
- Close and mask restoration errors are captured separately after dispatch. The exact close exception is primary when both fail, preserving its type, errno, cause, and descriptor-state uncertainty; restoration type/message/errno/notes become bounded secondary evidence. A successful close leaves restoration failure primary. Source and repository-resolution wrappers propagate deduplicated cause notes under the common eight-detail and 4 KiB-per-detail budgets, and an active launch-preparation error receives structured close type/errno plus restoration details. Repository owner acquisition failures use attempt-time wording so a successful outer drain cannot leave a terminal `remains owned` claim; only descriptors still owned after the final context-exit drain add one bounded past-tense final-boundary record.
- Git snapshot creation closes its source, destination, and directory descriptors through the same bounded precedence helper. Copy, verification, deadline, or other active failures retain their exact object, arguments, and traceback with close evidence attached; an unaccompanied close fault becomes primary and the temporary snapshot is removed or its retained locator is reported.
- The credential-free Git version probe now consumes the remainder of the existing five-second initialization deadline instead of a separate two-second cap. Timeout, nonzero exit, and malformed output raise non-cacheable `git_version_probe_failed`, so a later initialization retries; only a successfully parsed version below 2.45 is cached as `unsupported_git_version`.
- Hook finalization protects three distinct properties. The descriptor-relative target snapshot binds the installed hook's object identity, exact content, and access policy inside the held directory. A strict bounded Git query re-resolves the effective local/worktree configuration before atomic rename and after that final snapshot, comparing only the initial and current semantic plan root/components; destination drift is `effective_hook_destination_changed`, while query, parse, or safety uncertainty is `effective_hook_configuration_unverified`. Both effective-configuration wrappers copy bounded source exception notes, so a top-level CLI error preserves retained launch locators and cleanup-incomplete evidence. The held ancestor chain and owner-private lock are then revalidated for object identity and access policy before `mark_verified()`. That transition enters a distinct `verified` terminal phase rather than leaving the state classified as post-commit verification pending, so a deferred terminal signal observed in the final checkpoint propagates without false incomplete/recovery evidence. A post-commit effective-destination failure remains explicitly committed with final effective-destination verification incomplete; failures at earlier post-commit boundaries report their applicable pending step instead. These checks are point-in-time proofs, not a continuous configuration lock; they accept equivalent config scope/text that resolves to the same plan and ignore benign timestamps or child-entry churn.
- Repository resolution never applies shell-style home expansion to rollout CWDs. Absolute CWDs retain the existing repository-resolution path; relative values, including every leading-tilde spelling, remain ignored as non-repository evidence.
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
- Python 3.9 compatibility and cleanup-priority focused coverage: 9 tests passed in 3.697 seconds with Python 3.13.0 and in 3.694 seconds with Xcode Python 3.9.6. The regressions cover native Git runtime-prefix behavior, legacy exception-note injection, final child return-code reporting, binding/unframed/frame cleanup faults, inherited cleanup evidence, and ancestor replacement during a scan-plus-close failure.
- Updated discovery coverage: all 53 `discover-repos` tests passed in 111.106 seconds with Python 3.13.0 and in 113.990 seconds with Xcode Python 3.9.6.
- Final serial Python 3.13 full suite after compatibility and cleanup-priority fixes: 269 tests passed with 3 platform skips in 296.234 seconds.
- Final serial Xcode Python 3.9.6 full suite after compatibility and cleanup-priority fixes: 269 tests passed with 4 platform skips in 332.320 seconds. This supersedes the three failed compatibility results recorded above.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9.6 `py_compile`, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed.
- Validation-output and rollout-descriptor cleanup focused coverage: 12 tests passed in 3.297 seconds with Python 3.13 and in 3.299 seconds with Xcode Python 3.9.6. The regressions cover a near-4 MiB indexed path repeated through the per-entry issue budget, the independent aggregate issue-byte cap, candidate-binding and extraction primary-plus-close failures, all structured discovery error families, and generator cleanup.
- Final serial Python 3.13 full suite after validation-output and rollout-descriptor cleanup hardening: 274 tests passed with 3 platform skips in 291.058 seconds.
- Final serial Xcode Python 3.9.6 full suite after the same fixes: 274 tests passed with 4 platform skips in 414.510 seconds.
- Fresh named-single review of signed head `a425cbc1e8ab910f919b48d433057b16bd1d2998` identified the aggregate association, source-descriptor close, and flat-archive findings closed by this final patch.
- Final finding-focused suite: 11 tests passed in 7.346 seconds with Python 3.13 and in 6.590 seconds with Xcode Python 3.9.6. It covers shared resolved/unresolved association pressure, physical-copy deduplication, direct-child archive membership and pre-stat skipping, nested archive date rejection, source binding/verification/launch close precedence, launch cleanup, and documentation contracts.
- The first Python 3.13 full-suite attempt exposed three real failures at the former fixed two-second Git version-probe budget under load. The exact three tests passed alone in 11.495 seconds and a clean serial rerun passed all 282 tests with 3 platform skips in 381.513 seconds, but that rerun did not remove the structural timeout/cache defect addressed by the later evidence below.
- Final serial Xcode Python 3.9.6 full suite: all 282 tests passed with 4 platform skips in 286.666 seconds.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9.6 `py_compile`, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed.
- Fresh whole-range review of signed head `ba582e4c99bcd83ba3417a1eadd84f9c0cbc4b31` identified the snapshot-creation descriptor-close and Git version-probe timeout/cache findings closed by this patch.
- Post-head finding-focused coverage: 11 tests passed in 0.201 seconds with Python 3.13.0 and in 0.150 seconds with Xcode Python 3.9.6. The regressions inject active and otherwise-successful close faults independently for source, destination, and directory descriptors; preserve cleanup locators; and cover first-timeout-then-success, repeated timeout, nonzero, malformed, old-version caching, and shared-deadline exhaustion.
- Final serial Python 3.13 full suite after snapshot-close and version-probe hardening: all 288 tests passed with 3 platform skips in 283.184 seconds; the former fixed two-second version timeout did not recur.
- Final serial Xcode Python 3.9.6 full suite after the same fixes: all 288 tests passed with 4 platform skips in 313.683 seconds.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9.6 `py_compile`, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed.
- Hosted CI run `30472452365` used Ubuntu Python 3.14.6 and ran all 288 tests; its sole failure compared restored flags `0x04000000` with original flags `0`. Linux defines that bit as `SA_RESTORER`, and glibc may inject it with its private signal trampoline whenever an action is installed, so it is not disposition, mask, `SA_NOCLDWAIT`, or other user-visible flag drift.
- SIGCHLD restoration-focused coverage passed 9 tests with 1 Linux-only skip under Python 3.13.0 and 9 tests with 2 platform skips under Xcode Python 3.9.6. The native Linux test now compares handler, the complete signal mask, every flag except `SA_RESTORER`, the exact `SA_NOCLDWAIT` bit, and an actual post-restore fork/wait.
- Final serial Python 3.13 full suite after the Linux restoration assertion fix: all 288 tests passed with 3 platform skips in 287.028 seconds.
- Final serial Xcode Python 3.9.6 full suite after the same fix: all 288 tests passed with 4 platform skips in 307.130 seconds.
- A local Linux Python 3.14.6 rerun was unavailable because Apple Container had no configured Linux arm64 kernel and the installed Podman client had no running VM; no host VM or kernel configuration was initialized. The prior hosted failure remains the authoritative native-Linux evidence until the unpushed fix runs in CI.
- Final Ruff check/format, Python 3.13 and Xcode Python 3.9.6 `py_compile`, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed. The system `quick_validate.py` fallback entrypoint could not import PyYAML, while the installed validator wrapper completed successfully without changing the runtime.
- A fresh read-only narrow audit of the final SIGCHLD patch returned `No findings.`; it was an informal implementation audit, not a formal named-review lane.
- Current-head traversal audit: the reported absolute-path date parsing and missing chain-revalidation branches were both already closed by `5593a093`; the still-missing child-open/ancestor-replacement and entry-stat/ancestor-policy race regressions each passed under Python 3.13.0 and Xcode Python 3.9.6.
- Final exact-state Python 3.13.0 full suite: all 290 tests passed with 3 platform skips in 283.136 seconds. Final exact-state Xcode Python 3.9.6 full suite: all 290 tests passed with 4 platform skips in 295.896 seconds.
- Final-snapshot hooks-directory replacement red/green proof: without the post-snapshot ancestor-chain revalidation, both absent-target and existing-target cases completed without raising; with the fix, the regression passed in 5.020 seconds under Python 3.13.0 and in 5.234 seconds under Xcode Python 3.9.6, failing closed before the raced target reached `mark_verified()`.
- Hook post-commit and path-binding focused coverage: 7 tests passed in 26.423 seconds under Python 3.13.0. Python 3.13.0 and Xcode Python 3.9.6 `py_compile`, Ruff check/format verification, project journal validation, and `git diff --check` passed; the Xcode bytecode cache was redirected into a task-scoped worktree directory and removed afterward.
- Final exact-state full suites after the hook path-binding fix: Python 3.13.0 passed all 291 tests with 3 platform skips in 441.310 seconds; Xcode Python 3.9.6 passed all 291 tests with 4 platform skips in 462.387 seconds. Ruff check/format verification, both interpreters' `py_compile`, project journal validation, and `git diff --check` passed; the task-scoped Xcode bytecode cache was removed.
- Effective-hook P2 red/green proof: 13 newly scoped tests failed against the prior implementation because the strict query/parser, effective-plan binding, and pre/post-commit revalidation did not exist. After implementation, the core hook cases passed; the complete `install-hooks` focused suite then passed all 42 tests in 294.138 seconds with Python 3.13.0.
- Indexed-path and discovery-anchor focused coverage passed 6 tests in 0.024 seconds, the combined indexed-path set passed 8 tests in 0.044 seconds, and all 58 `discover-repos` tests passed in 66.736 seconds with Python 3.13.0.
- Current-head traversal regressions remained green: 4 tests covering rollout-root-relative dates and ancestor replacement/access-policy precedence passed in 5.195 seconds with Python 3.13.0, confirming that the two reported P1s were already fixed rather than prompting a duplicate refactor.
- Final exact-state Python 3.13.0 suite after all three P2 fixes and documentation synchronization: all 305 tests passed with 3 platform skips in 506.645 seconds. The first full attempt exposed four stale direct-test calls after the private `_bind_hook_directory(repo, plan)` invariant was made explicit; those four fixtures passed in 0.383 seconds after correction before this clean full rerun.
- Post-format exact P2 coverage passed all 14 newly added regressions in 40.873 seconds. Python 3.13.0 `py_compile`, Ruff check/format verification, the installed Joey/OpenAI skill-validator wrapper, project journal validation, and `git diff --check` passed. The system `quick_validate.py` entrypoint remained dependency-blocked by missing PyYAML; no dependency was installed because the wrapper supplied the successful authoritative skill validation.
- Fresh-audit display-label red/green proof: the short non-UTF-8 helper assertion and strict UTF-8 discovery JSON sink both failed before the fix, including a `UnicodeEncodeError`, then passed after rendering the short label from the already bounded `backslashreplace` bytes. The related path set passed 7 tests in 0.050 seconds, adoption passed 20 tests in 31.008 seconds, and all 58 `discover-repos` tests passed in 70.344 seconds.
- Fresh-audit exception-note red/green proof: both the effective-configuration wrapper and its post-commit wrapper initially lost the injected cleanup/locator note, then passed after bounded note copying. Effective-hook coverage passed 3 tests in 2.398 seconds, post-commit coverage passed 4 tests in 33.452 seconds, and effective-destination coverage passed 3 tests in 39.519 seconds.
- Fresh-single final-signal red/green proof: the state-machine assertion and the SIGHUP/SIGTERM/SIGQUIT final-window subcases all failed while `mark_verified()` left the phase as `installed-target-committed`, then passed after it entered the independent `verified` phase. The two exact tests passed in 9.344 seconds; four adjacent earlier-window hook interruption tests passed in 32.607 seconds, four deferred-signal propagation tests passed in 2.699 seconds, and the four-test post-commit set passed in 36.772 seconds.
- Independent pre-commit audit found no state-machine or test defect and closed two documentation precision gaps: incomplete/recovery evidence now applies only while the committed state remains active, and the verified transition is documented as following both installed-target and final effective-destination verification.
- Rollout-buffer and failed-Git marker coverage passed 22 focused tests, including second-pass content drift, late parse failure, distinct-CWD overflow, first-seen commit order after descriptor close, all four marker types, marker/ancestor EACCES and EIO, shared-deadline exhaustion, stale and persistent `ENOTDIR`, scan and device boundaries, kernel symlink/parent traversal, linked-worktree mapping, structured unresolved output, metadata contracts, and the two owner-directory DAC tests on this non-root host. All 60 `discover-repos` tests then passed in 65.654 seconds.
- The first Python 3.13.0 full-suite run exposed one stale test expectation that still treated a failed script-Git runtime plus `.git` directory as permission to infer a repository root. The updated regression now requires `repo: null`, preserves `candidate_cwd`, and verifies structured `repository_resolution_failed` marker evidence. The exact test passed in 0.130 seconds.
- Final exact-state Python 3.13.0 suite after the rollout-buffer, UID-0 DAC-skip, and failed-Git marker-classification changes: all 323 tests passed with 3 platform skips in 530.598 seconds. Python compilation, Ruff check/format verification, the installed Joey/OpenAI skill validator, project journal validation, and `git diff --check` passed; the task-scoped bytecode cache was removed.
- Descriptor-relative repository-resolution red/green proof: the atomic final-symlink A(marker)→B(no marker)→A injection made the pathname implementation return ordinary `None`; after binding the pre-Git directory identity and moving `.git` plus parent lookup under held FDs, the same injection used a non-null `dir_fd`, observed A's marker, and returned structured `git_marker_present` with only a `path_unverified` lexical hint. The 17-test classifier matrix passed in 0.031 seconds with Python 3.13.0 and in 0.047 seconds with Xcode Python 3.9.6.
- The descriptor matrix also covers required initial/parent flags and stable final-symlink semantics, initial identity mismatch, parent open/fstat failure, all marker types, same-FD `ENOTDIR` retry, shared-deadline expiry after marker lookup, root/device/`MAX+1` termination, a two-FD peak, exact-once close ownership, successful-scan close failure, marker-primary cleanup evidence, and missing required primitives. All 60 `discover-repos` tests passed in 89.684 seconds; 13 marker tests plus repository-resolution and linked-worktree focused coverage also passed.
- Final exact-state Python 3.13.0 suite after descriptor-bound repository resolution: all 330 tests passed with 3 platform skips in 659.632 seconds. The existing indexed-path bounding and effective-hooks destination tests remained green and their implementations were not overwritten.
- Final Ruff check/format, Python 3.13.0 and Xcode Python 3.9.6 `py_compile`, installed Joey/OpenAI skill validation, project journal validation, and `git diff --check` passed. Both task-scoped bytecode caches were removed.
- Historical single-owner handoff coverage passed all 20 failed-resolution tests in 0.057 seconds with Python 3.13.0 and in 0.102 seconds with Xcode Python 3.9.6. Its trace-injected custom `BaseException` matrix established active-primary and drain-precedence behavior, but its direct pre-kernel close injection is not the current exact-once property boundary and is superseded by the selected-signal fence coverage below. Two owner-state tests passed in 0.009 and 0.018 seconds respectively: the two-FD no-primary matrix preserves the first `descriptor_close_failed` and exact cause across wrapper-construction, tuple-persistence, and later-selection interruptions while closing the remaining FD once; the ambient-context regression proves a pre-close drain interruption inside an outer `UnsupportedGitVersion` handler remains the exact primary instead of relabeling that outer exception as a close failure.
- Latest-state discovery coverage passed all 60 `discover-repos` tests in 78.918 seconds. Marker, repository-resolution, and Codex-worktree mapping coverage passed 13, 5, and 2 tests respectively before the unchanged final owner provenance refinement.
- A fresh independent Codex-only audit successively identified the classifier handoff, owner close-attempt, context-drain, no-primary precedence, split primary/cause persistence, and ambient-context provenance windows. After the final owner-marked close-attempt plus recorded-ambient refinement, the same auditor returned `No findings.` Claude lane temporarily waived by Joey before 2026-08-01 00:00 Asia/Shanghai; the lane was not run and is not classified as completed.
- Superseding exact-state Python 3.13.0 suite after the single-owner asynchronous cleanup hardening: all 335 tests passed with 3 platform skips in 583.453 seconds. This preserved the earlier indexed-path bounding and effective-hooks destination implementations and tests.
- Final post-evidence verification passed Ruff check/format, Python 3.13.0 and Xcode Python 3.9.6 `py_compile`, the installed Joey/OpenAI skill validator, project journal validation, four skill metadata tests, and `git diff --check`. The two task-scoped bytecode caches were removed with the protected cleanup helper.
- Selected-signal close-handoff proof uses real `SIGINT` delivery at the pre-fence line, after ownership commit before `close(2)`, and after successful close before retirement, for both Git launch-source and repository-resolution descriptors. It also reuses the just-closed numeric FD before delayed signal delivery and proves no second close, retains and reports the source FD when both pre-close fence attempts fail, preserves an already-pending blocked `SIGINT` and exact entry mask, rolls back a second-step mask call that changes the kernel mask before raising, bounds persistent owner mask-acquisition failure to one entry-snapshot pass, and retires each close-dispatched FD before persistent restoration errors surface. All 12 focused signal/owner regressions passed in 0.034 seconds with Python 3.13.0 and in 0.056 seconds with Xcode Python 3.9.6.
- Final exact-state suites after the close-dispatch ownership fix passed all 344 tests in both supported runtimes: Python 3.13.0 completed in 513.945 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 624.513 seconds with 4 platform skips. Ruff check/format verification, both runtimes' `py_compile`, the installed Joey/OpenAI skill validator, project journal validation, and `git diff --check` passed.
- The final documentation audit narrowed the second-step acquisition claim from guaranteed restoration to one best-effort rollback. A new exact regression makes the block call change the mask and raise, makes rollback also raise, and proves that the original exception remains primary with explicit `thread signal-mask state is unverified` evidence. The rollback-success and rollback-failure pair passed in 0.002 seconds with Python 3.13.0 and in 0.003 seconds with Xcode Python 3.9.6; Ruff check/format, both runtimes' `py_compile`, skill validation, journal validation, and `git diff --check` passed again afterward.
- Final exception-precedence and owner-boundary coverage passed all 11 focused regressions in 0.030 seconds with Python 3.13.0 and in 0.035 seconds with Xcode Python 3.9.6. The matrix preserves close exception identity and errno across a simultaneous mask-restoration failure, retains the underlying ordinary restore type/errno/message/notes through its platform wrapper, keeps an active action exception exact with structured cleanup evidence, deduplicates and deterministically caps repeated note propagation, retires close-dispatched FDs before pending `SIGINT`, and distinguishes transient attempt-time evidence from a persistent final context-exit ownership boundary.
- The first post-audit Python 3.13.0 full-suite attempt found one stale SKILL contract assertion after the owner-state sentence had been reworded. Restoring the more precise `not-yet-closed` versus `close-attempted` wording made that exact regression pass in 0.001 seconds with Python 3.13.0 and in 0.002 seconds with Xcode Python 3.9.6. Clean final exact-state reruns then passed all 354 tests in both supported runtimes: Python 3.13.0 completed in 559.292 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 558.617 seconds with 4 platform skips.
- A final independent read-only audit of exception identity and errno, bounded notes, FD retirement and no-retry behavior, and final-boundary evidence returned exact `No findings.` after the underlying restore evidence fix.
- Final post-evidence verification passed Ruff check/format, Python 3.13.0 and Xcode Python 3.9.6 `py_compile`, the installed Joey/OpenAI skill validator, project journal validation, and `git diff --check`.
- The fresh single review of signed head `ff6d870f62a83b0f0cdfbbbc122987ce7ef22678` found two remaining boundaries. Secure bounded reads now preserve the exact active read/validation/deadline exception when descriptor close also fails and wrap an otherwise standalone ordinary close failure as `UserError`. Per-command Git launches now bind and revalidate the lexical temporary-root marker/target chain, require every lexical and descriptor-bound component to be owned by root or the current user, reject unsafe writable/ACL policy, retain root-to-leaf descriptors, and clean only through the held parent/leaf relationship.
- A subsequent read-only implementation audit exposed four defects in the first launcher fix: post-`Popen` drift could be mislabeled as start failure and prematurely authorize deletion, a foreign-UID owner of a sticky or lexical-symlink component remained outside the claimed threat boundary, several new close paths could replace an active exception or wrap a close-only non-`Exception` `BaseException`, and a displaced launch object was reported with an unverified pathname as though it were a locator. The final implementation routes post-start drift through process-group/reap cleanup, marks cleanup safe only after verified terminal state, closes unregistered child pipes, excludes foreign-owned lexical and actual ancestors, preserves exact close precedence, and reports either a revalidated locator or retained directory identity plus an explicitly unverified original path hint.
- Final launcher-focused coverage passed all 29 tests in 4.433 seconds with Python 3.13.0 and in 3.015 seconds with Xcode Python 3.9.6. It includes foreign-owned sticky roots and lexical symlinks, pre- and post-start revalidation, incomplete child cleanup retention, replacement-parent cleanup, exact raw-`BaseException` close behavior, and active-primary close evidence. The two secure-read close-precedence regressions and the two skill/README contract tests also passed in both runtimes.
- Final exact-state suites after all review fixes passed all 364 tests in both supported runtimes: Python 3.13.0 completed in 740.695 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 593.779 seconds with 4 platform skips.
- Non-UTF-8 discovery-error repair routes hook-inspection and adoption failures through one strict-UTF-8 `backslashreplace` display boundary while preserving their machine reason codes. A real non-UTF-8 repo-scoped `core.hooksPath` error now survives `ensure_ascii=False` publication to a strict UTF-8 sink. Final exact-state suites passed all 365 tests in both supported runtimes: Python 3.13.0 completed in 1009.576 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 1035.602 seconds with 4 platform skips.
- Follow-up strict-output repair applies the same UTF-8 display boundary recursively to every serialized discovery-error value, including rollout source paths, repository marker paths, and nested cleanup evidence, while leaving valid machine reason codes byte-for-byte unchanged. Focused strict-sink coverage includes a non-UTF-8 rollout source with an `invalid_json` terminal error and nested non-UTF-8 cleanup fields; the 11-test discovery/error selection passed in both supported runtimes. Final exact-state suites passed all 367 tests: Python 3.13.0 completed in 936.197 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 959.650 seconds with 4 platform skips. Ruff check/format verification, dual-runtime compilation, project-journal validation, and `git diff --check` also passed.
- Descriptor-close precedence follow-up routes generated-index marker inspection plus discovery auxiliary bind/read/existence cleanup through one bounded evidence helper. An active read, deadline, replacement, or semantic-limit exception remains the exact primary with its machine code and limit fields intact; an ordinary close-only failure is wrapped in the matching generated-index or discovery-auxiliary domain error, while a close-only non-`Exception` `BaseException` remains exact. Each cleanup region records only an exception raised by its own operation body, so an unrelated outer exception cannot absorb a close failure or receive its evidence. Eight focused regressions passed in both supported runtimes. Final exact-state suites passed all 375 tests: Python 3.13.0 completed in 816.596 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 836.294 seconds with 4 platform skips.
- A fresh whole-range review identified that the admitted musl multiarch values do not share the reviewed glibc `struct sigaction` layout. Musl now fails closed before loading libc, starting `Popen`, or issuing `killpg`; the reviewed native layout remains limited to 64-bit glibc x86_64/AArch64 until a separate musl layout and real-runtime proof are available. The focused ABI rejection selection passed in both supported runtimes. Final exact-state suites again passed all 375 tests: Python 3.13.0 completed in 1015.694 seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 1034.613 seconds with 4 platform skips.
- A fresh whole-range review of signed head `335fc9e` found three remaining
  cleanup regions that still treated an unrelated outer `sys.exc_info()`
  value as though it came from the current secure-read or process operation.
  Secure reads and outer Git-launch cleanup now record only an exception raised
  by their own operation body. Process supervision separately records whether
  its own body entered an exception handler and, only in that case, binds
  observer cleanup evidence to the exception actually propagating through the
  terminal `finally`. A narrow follow-up audit caught and closed the deeper
  handler-failure case: identity settlement or generic cleanup may itself
  replace the original exception, and observer cleanup must attach to that
  final replacement rather than the stale original. Observer cleanup that
  itself raises a `BaseException` is also exact on an otherwise-successful
  operation and becomes bounded evidence without replacing an active primary.
  Seven new ambient, handler-failure, and observer-error regressions plus four
  existing primary/close/identity regressions passed under both supported
  runtimes, and the independent implementation audit returned clean. Final
  exact-state suites passed all 382 tests: Python 3.13.0 completed in 867.351
  seconds with 3 platform skips, and Xcode Python 3.9.6 completed in 891.611
  seconds with 4 platform skips. Ruff lint/format, dual-runtime compilation,
  installed OpenAI skill validation, project-journal validation, and
  `git diff --check` passed.
- The fresh whole-range review of signed head `bcce985` found one remaining
  cleanup-precedence gap in Git runtime directory revalidation and one stale
  musl eligibility statement. Directory revalidation now records only an
  exception raised by its own operation body and routes descriptor close
  through the existing exact-primary-preservation helper, so a simultaneous
  close fault becomes bounded evidence without replacing the ACL, identity, or
  deadline failure. The journal now consistently limits native Linux
  `sigaction` support to reviewed LP64 glibc x86_64/AArch64 and states that
  musl fails closed before libc or child-process access.
- The new directory-revalidation primary-plus-close regression passed in both
  supported runtimes. Final exact-state suites passed all 383 tests:
  Python 3.13.0 completed in 923.861 seconds with 3 platform skips, and Xcode
  Python 3.9.6 completed in 949.927 seconds with 4 platform skips.
- The fresh whole-range review of signed head
  `c7402e2ec3817f0ad8ea938566f1fddf18e3b333` found that decoding Git pathname
  output with UTF-8 replacement could collapse distinct non-UTF-8 byte names
  onto the same `U+FFFD` spelling. Git pathname records now use
  `os.fsdecode()` and an exact one-newline framing parser without generic
  whitespace stripping, while stderr remains a bounded diagnostic rendering.
  Unit coverage preserves raw path bytes, rejects malformed framing, and
  proves that a non-UTF-8 repository cannot alias a real `U+FFFD` sibling.
  A real raw-byte repository/install-hooks integration test is Linux-only
  because the macOS filesystem rejects creation of the invalid byte name.
- Final exact-state suites after the pathname-identity repair passed all 386
  tests: Python 3.13.0 completed in 965.031 seconds with 4 platform skips, and
  Xcode Python 3.9.6 completed in 994.701 seconds with 5 platform skips.
- The fresh whole-range review of signed head `9104327` found two remaining
  P2 boundaries. Installed hooks no longer depend exclusively on the pinned
  Python/helper chain for terminal diagnostics: under `umask 077` they bind
  separate log/status descriptors in a `mktemp` directory, unlink both names
  and remove the directory before helper launch, retain at most 64 KiB while
  draining the producer, and emit the captured bytes to stderr when persistent
  append cannot start or fails. The hook still returns zero, uses only
  non-recursive cleanup, and explicitly does not claim protection from a
  malicious same-UID replacement before descriptor binding.
- `adoption-status` and JSON `discover-repos` now share one recursive
  UTF-8-safe serialization boundary. Filesystem surrogate escapes in
  repository paths, linked-worktree source paths, and nested cleanup evidence
  become literal `backslashreplace` display text before `ensure_ascii=False`
  serialization, while valid schema keys and machine reason codes remain
  unchanged. Strict UTF-8 sinks therefore cannot raise or emit non-UTF-8 JSON.
- The final focused selection passed all 19 tests in both supported runtimes:
  Python 3.13.0 completed in 42.901 seconds and Xcode Python 3.9.6 completed in
  45.007 seconds. It covers missing helper scripts and interpreters, append
  dispatch failure, Git preflight failure, the 64 KiB ceiling, anonymous
  temporary cleanup, closed-stderr `SIGPIPE`/exit-zero behavior, POSIX shell
  syntax, strict JSON sinks, nested errors, and non-UTF-8 linked-worktree
  mapping, plus existing hook success/failure paths.
  Ruff check/format, dual-runtime `py_compile`, generated-hook `bash -n` and
  `shellcheck`, installed skill validation, project-journal validation, and
  `git diff --check` passed. The parent workstream retains ownership of the
  final full-suite rerun.
- The parent-owned final exact-state suites passed all 396 tests in both
  supported runtimes: Python 3.13.0 completed in 1140.769 seconds with 4
  platform skips, and Xcode Python 3.9.6 completed in 1162.014 seconds with 5
  platform skips. Ruff check/format, dual-runtime `py_compile`, generated-hook
  `/bin/sh -n`, `bash -n`, and ShellCheck validation, the installed skill
  validator, project-journal validation, and `git diff --check` passed against
  the same frozen implementation.
- The fresh whole-range review of signed head `17314a7` found that persistent
  diagnostic append could follow a symlink or block on a FIFO, and that
  `dd bs=4096 count=16` counted short pipe reads rather than exact retained
  bytes. Red evidence reproduced all three failures: the FIFO hook exceeded
  its five-second bound, the symlink target received the diagnostic, and a
  fragmented producer lost every fragment after number 14 despite remaining
  below 64 KiB.
- Persistent append now opens and binds the resolved Git-path parent and leaf
  with required no-follow, nonblocking, close-on-exec, directory, and append
  flags. It requires current-user ownership, no group/world write access, no
  extended ACL, a regular-file leaf, and stable parent/leaf object identities
  plus owner/group/mode policies before and after descriptor-only writes.
  Special targets, unsafe policy, replacement, or inspection uncertainty fail
  immediately into the hook's independent stderr fallback. The retained
  diagnostic stage now counts one-byte `dd` input records while batching
  output into 4 KiB blocks, preserving exactly the first
  `min(stream bytes, 64 KiB)` despite fragmented pipe writes and still draining
  the producer.
- A subsequent read-only implementation audit found that the first fix could
  still issue a write-mode open against a stable character or block special
  file before rejecting its type. It also found that the fragmented-stream
  regression checked only unordered substring presence and that the 64 KiB
  assertion did not prove the producer was drained. Persistent append now
  performs a descriptor-relative no-follow leaf stat first, rejects every
  stable non-regular target before a leaf write-open, and uses
  `O_CREAT|O_EXCL` when that stat proves the leaf absent.
- Ten finding-focused tests passed in both supported runtimes: Python 3.13.0
  completed in 31.008 seconds and Xcode Python 3.9.6 in 31.453 seconds. They
  cover ordinary append, append-dispatch failure, required parent/leaf and
  exclusive-create flags, FIFO rejection before leaf open, end-to-end FIFO,
  symlink, group-writable and post-write-replaced log targets, exact ordered
  fragmented bytes, and a differentiated 100 KiB producer. The last
  regression requires an out-of-band producer-completion marker and compares
  the capture byte-for-byte with its exact 64 KiB prefix. Later evidence below
  supersedes its original claim to prove downstream EOF drain.
- The exact-state Python 3.13.0 full suite passed all 403 tests in 1029.497
  seconds with 4 platform skips. The simultaneously launched Xcode Python
  3.9.6 suite reached all 403 tests but recorded one five-second outer timeout
  in the symlink-log hook regression after 1056.150 seconds; that test then
  passed three consecutive isolated runs in 6.836 seconds total. A clean
  non-competing Xcode Python 3.9.6 full rerun passed all 403 tests in 641.546
  seconds with 5 platform skips, superseding the parallel-load timeout.
- A late child result from the informal read-only audit superseded its
  parent-level `No findings.` summary: a 100 KiB producer can finish after
  `dd` frees enough pipe capacity while unread bytes remain buffered, so its
  completion marker does not prove that the following `cat` reaches EOF. The
  corrected regression replaces that exact drain stage with an observable
  reader that records its byte count only after EOF, and requires the count to
  equal the complete stream remainder after the first 64 KiB.
- The formal fresh whole-range named-single review of signed head `5708e4b`
  found one additional P2: `_snapshot_hook_target()` allowed a simultaneous
  descriptor-close failure to replace an active target validation or read
  error. Snapshot cleanup now uses the existing exact-primary-preservation
  helper. The regression injects validation-plus-close and read-plus-close
  failures independently, preserves the exact operation exception, and
  retains bounded close evidence.
- The corrected exact-drain and snapshot primary-plus-close regressions passed
  in both supported runtimes: Python 3.13.0 completed the two tests in 3.864
  seconds, and Xcode Python 3.9.6 completed them in 3.868 seconds.
- Final exact-state full suites after both follow-up fixes passed all 404
  tests: Python 3.13.0 completed in 958.148 seconds with 4 platform skips, and
  Xcode Python 3.9.6 completed in 977.729 seconds with 5 platform skips.
- The next formal fresh whole-range named-single review of signed head
  `e827a50` found two remaining P2 failure-evidence gaps. The hook pipeline let
  a successful EOF drain mask a failed bounded copy and did not consume the
  drain status, so an incomplete diagnostic could be accepted for persistent
  append. Several hook path, lock, and staging cleanup paths also let a
  descriptor-close failure replace the active validation, write, sync, or
  permission error.
- The hook now sends distinct generation, exact bounded-copy, and EOF-drain
  statuses over an anonymous parent-owned status channel and classifies the
  records independently of producer/capture completion order. It accepts
  persistent append only when both capture stages succeed; otherwise it skips
  the durable log and emits an explicit hard-bounded partial diagnostic to
  stderr. Path-component, root/ancestor, installation-lock, and staged-hook
  descriptor cleanup now preserves the exact active exception and attaches
  bounded close-failure evidence. `OSError` wrappers copy that evidence to the
  top-level `UserError`, while a close failure with no active exception remains
  independently actionable.
- A follow-up read-only implementation audit found that root-descriptor
  ownership began too late, two natural `OSError` wrapping paths discarded
  attached cleanup notes, and the first drain-failure test failed only after
  EOF. Root ownership now covers every fallible validation, the wrappers retain
  bounded notes, and the drain regression uses a 1 MiB producer plus a reader
  that consumes at most 4 KiB before failing. The hook finishes inside the
  five-second test deadline, reports the independent copy/drain statuses,
  skips persistent append, and keeps stderr within the documented ceiling.
- A final bounded audit found that an unbounded all-digit status could overflow
  the shell integer comparison and be treated as false rather than invalid.
  Status admission now accepts only canonical decimal `0..255` before any
  integer operation, with the shell parser pinned to the C locale. The
  regression injects 128-digit generation, copy, and drain records and proves
  each fails closed without invoking an unsafe comparison.
- Ten focused regressions passed in both supported runtimes: Python 3.13.0
  completed in 21.025 seconds and Xcode Python 3.9.6 in 20.825 seconds. They
  cover exact bounded retention plus observable EOF drain, independent copy
  and pre-EOF drain failure injection, POSIX hook syntax, and simultaneous
  operation plus descriptor-close failures across target snapshot,
  filesystem-root binding, path component, path ancestor, install lock, and
  staged hook write paths, plus bounded status-record admission. The early
  drain failure's substituted append command writes an invocation marker, and
  the regression proves that marker remains absent rather than inferring
  skipped append from a missing log alone.
- Final implementation-state full suites passed all 411 tests in both
  supported runtimes: Python 3.13.0 completed in 1004.673 seconds with 4
  platform skips, and Xcode Python 3.9.6 completed in 1028.254 seconds with 5
  platform skips.
- The formal fresh whole-range named-single review of signed head `ed9964b`
  found one remaining P2: worktree frontmatter parsing used
  `Path.read_bytes()` before enforcing the 1 MiB journal limit, so an
  oversized or sparse Markdown file could consume unbounded memory before
  rejection.
- Worktree journal parsing now reuses the secure descriptor reader. It rejects
  an already-oversized regular file from descriptor metadata before any
  content read and consumes at most the 1 MiB limit plus one byte in each of
  two passes. Only the first complete snapshot remains retained across passes;
  the second is compared through one 64 KiB chunk. Final descriptor/path
  revalidation binds object identity, owner/group/mode access policy, and size,
  while the streamed comparison rejects same-size content replacement.
  Bounded display labels lead every helper message, and `OSError` detail is
  reduced to type/errno/strerror so an original absolute pathname cannot bypass
  that bound or be duplicated by the issue collector.
- The initial nine-test focused selection passed in Python 3.13.0 and Xcode
  Python 3.9.6, but a subsequent narrow implementation audit correctly found
  that its growth case modified the first pass rather than the second and that
  raw `OSError` text could reintroduce an unbounded original pathname. That
  initial selection is retained only as intermediate evidence and does not
  satisfy the final gate.
- The corrected fifteen-test focused selection passed in both supported
  runtimes: Python 3.13.0 completed in 0.018 seconds and Xcode Python 3.9.6
  completed in 0.026 seconds. It covers metadata-first sparse-file rejection,
  bounded second-pass growth, bounded non-UTF-8 labels without original-path
  leakage or collector duplication, path replacement, access-policy and size
  changes, same-size content replacement, exact limit-error precedence over a
  descriptor-close failure, the generic secure-reader close contracts, and
  the skill adoption/limit wording.
- A final independent read-only audit rechecked the streamed comparison,
  per-pass byte ceilings, bounded diagnostic path, collector integration, and
  replacement/access/size/close-precedence regressions against its earlier
  findings and returned `No findings.`
- The first exact-state dual-runtime full run completed all 420 tests. Xcode
  Python 3.9.6 passed in 1152.388 seconds with 5 platform skips. Python 3.13.0
  completed in 1110.669 seconds with 4 platform skips but recorded one
  five-second outer `TimeoutExpired` in the pre-existing
  `test_hook_group_writable_log_target_falls_back_without_appending` timing
  harness while the two suites competed for resources.
- A dedicated read-only timing audit proved that the current frontmatter
  change adds about 0.18 milliseconds for the test's single journal, while
  the fallback hook serially launches two CLIs that each copy, hash, and
  validate the 3,622,896-byte Git runtime. The same timeout reproduced by
  running that one unchanged test concurrently across runtimes, so it is the
  pre-existing five-second harness margin rather than a frontmatter product
  regression. After one immediate post-load timeout, the exact Python 3.13.0
  test passed six consecutive isolated runs in 2.222 to 3.259 seconds.
  Because this workstream did not change the hook path and a clean full rerun
  is authoritative, the unrelated test threshold remains unchanged.
- The final non-competing Python 3.13.0 full rerun passed all 420 tests in
  639.117 seconds with 4 platform skips. Together with the harder competing
  Xcode Python 3.9.6 pass, this supplies a clean full-suite result in both
  supported runtimes for the final implementation and regression state.
- Cleanup-failure injection retained seventeen empty owner-private,
  read-only Git-launch directories after the test processes had terminated.
  Each exact directory was verified empty, current-user-owned, and free of
  open process references before `rmdir`; a bounded follow-up inventory found
  no remaining recent `project-journal-git-launch-*` directory.
- Signed head `e620980` passed exact-secret admission with a clean result and
  complete temporary cleanup, then was pushed to PR #5. Its first
  fresh-context named-single workspace materialized and validated cleanly,
  but the lane was intentionally interrupted and not counted after the
  head-bound GitHub Actions run exposed a test-harness portability failure.
  The interrupted workspace passed terminal revalidation, the trusted SKILL
  and guard hashes remained unchanged, and the exact private workspace was
  removed after proving it had no open process references.
- GitHub Actions used Linux Python 3.14.6 and failed only
  `test_non_utf8_repo_generate_and_hooks_use_exact_raw_path`: the shared
  `run_git()` test helper requested strict text decoding, while `git init`
  correctly emitted the repository's raw `0xff` path byte. The resulting
  `UnicodeDecodeError` occurred in `subprocess` before the product assertions.
  The helper now fixes its text contract to UTF-8 plus `surrogateescape`, so
  existing string-based callers remain unchanged while arbitrary Git path
  bytes round-trip. The Linux integration itself remains the regression for
  this exact failure.
- An independent read-only audit checked all 84 `run_git()` call sites.
  Except for two callers that parse ASCII object IDs, they inspect only return
  codes or failure diagnostics; none relies on strict UTF-8 rejection.
  Therefore valid UTF-8 behavior is unchanged and `surrogateescape` is the
  minimal lossless portability fix. The audit returned `No findings.`
- Final exact-state full suites after the CI portability fix passed all 420
  tests in both supported local runtimes: Python 3.13.0 completed in 1016.834
  seconds with 4 platform skips, and Xcode Python 3.9.6 completed in 1041.244
  seconds with 5 platform skips. A bounded follow-up inventory found no
  retained recent Git-launch directory.
- The next formal fresh whole-range named-single review of signed head
  `0291adf` found one P2: final `_close_hook_binding()` cleanup unconditionally
  discarded installation-lock and retained-ancestor descriptor close
  failures. A close-only failure could therefore leave `install-hooks`
  reporting success, while an active installation failure lost its cleanup
  evidence.
- Final hook binding cleanup now attempts the installation lock followed by
  every retained ancestor even after a failure. An active operation exception
  remains the exact primary and accumulates bounded close evidence. Without an
  active exception, the first close failure becomes an actionable `UserError`;
  later close failures attach to it while the remaining descriptors still
  drain. Binding revalidation, preflight, and the top-level install command all
  pass their exact active exception into that aggregate cleanup.
- Three focused final-binding regressions passed in both supported runtimes:
  Python 3.13.0 completed in 0.004 seconds and Xcode Python 3.9.6 in 0.005
  seconds. They prove continued drain after a first close-only failure,
  exact-primary preservation plus evidence from every final close, and
  close-only rejection before a nominal successful command return. An
  independent read-only audit rechecked all three production call sites and
  the new tests and returned `No findings.`
- The expanded final-binding regression set, including the existing component,
  traversal, installation-lock close, and documentation checks, passed all
  seven tests in both supported runtimes: Python 3.13.0 completed in 1.926
  seconds and Xcode Python 3.9.6 in 1.954 seconds.
- Final exact-state full suites after the final-binding fix passed all 423
  tests in both supported local runtimes: Python 3.13.0 completed in 1004.900
  seconds with 4 platform skips, and Xcode Python 3.9.6 completed in 1035.859
  seconds with 5 platform skips. A bounded follow-up inventory found no
  retained recent `project-journal-git-launch-*` directory.
- Final post-evidence verification passed Ruff check/format, both runtimes'
  `py_compile`, the installed Joey/OpenAI skill validator, both runtimes'
  project journal validation, and `git diff --check`. The two task-scoped
  bytecode caches were removed with the protected cleanup helper.
- Signed head `80310ce` passed exact-secret admission with complete temporary
  cleanup, was pushed to PR #5, and passed Linux Python 3.14.6 CI in 75
  seconds. The fully paginated request ledger proved zero GitHub Codex
  requests on that head and no overlap among the four terminal historical
  requests.
- The first fresh named-single attempt returned `No findings.` but its own
  full-suite command created ignored `scripts/__pycache__/` and
  `tests/__pycache__/` entries. Terminal guard validation therefore stopped
  with `blocked-safety`, and that result was not counted. The exact workspace
  was removed only after proving it was owner-private, contained no symlink or
  foreign-owned object, and had no process references.
- A second independently materialized fresh-context, strictly read-only
  whole-range named-single review found one P2 and passed terminal guard
  validation: `_close_hook_binding()` drained multiple numeric descriptors
  without the existing POSIX close signal fence. A pending `SIGINT` could run
  Python between close dispatches, skip the remaining owners, and replace an
  active operation or first close error.
- After successful fence acquisition, one fence now covers the installation
  lock and the complete reversed entry-time ancestor set. Every numeric
  descriptor receives one close dispatch before the mask is restored; a close
  error leaves reuse state uncertain and is never retried. Fence acquisition
  failure performs no unprotected close and reports the count not attempted.
  Error priority is active operation, then first close, then restoration or
  newly delivered pending `SIGINT`; later failures remain bounded evidence.
  A caller-entry blocked pending `SIGINT` remains blocked and pending.
- Thirteen focused final-binding, real-`SIGINT`, acquisition-failure,
  restoration-matrix, command-precedence, and documentation regressions
  passed in both supported runtimes after the final documentation correction:
  Python 3.13.0 completed in 0.019 seconds and Xcode Python 3.9.6 in 0.025
  seconds. Ruff check/format and
  `git diff --check` also passed.
- An independent final read-only audit rechecked the single-fence owner set,
  zero unprotected closes after acquisition failure, reuse-uncertain no-retry
  rule, active/close/restore priority matrix, real pending-`SIGINT` delivery,
  and entry-blocked pending state. After the documentation scope correction it
  returned `No findings.`
- Final exact-state full suites passed all 431 tests in both supported local
  runtimes: Python 3.13.0 completed in 996.951 seconds with 4 platform skips,
  and Xcode Python 3.9.6 completed in 1021.178 seconds with 5 platform skips.
  A bounded follow-up inventory found no retained recent
  `project-journal-git-launch-*` directory.
- Final post-evidence verification passed Ruff check/format, both runtimes'
  `py_compile`, the installed Joey/OpenAI skill validator, both runtimes'
  project journal validation, and `git diff --check`. The two task-scoped
  bytecode caches were removed with the protected cleanup helper.
- Signed head `f2f501d` passed exact-secret admission with complete temporary
  cleanup, was pushed to PR #5, and passed Linux Python 3.14.6 CI in 78
  seconds. A new fully paginated request-ledger race close again proved zero
  GitHub Codex requests on that head and no unresolved historical request.
- The next independently materialized fresh-context, strictly read-only
  whole-range named-single review found one P2 and passed terminal guard
  validation: the generic post-commit `UserError` wrapper replaced its source
  exception without copying bounded `__notes__`. A final installed-target
  inspection failure could therefore lose descriptor-close evidence that was
  visible only on the source error.
- The generic committed-target branch now uses the same
  `_wrap_user_error_preserving_details()` path as the OSError branches. The
  wrapper remains a `UserError`, preserves its source as `__cause__`, and
  copies bounded source notes while retaining committed-target recovery
  context.
- The first new integration assertion correctly reached the generic
  post-commit wrapper but overbound the total target-snapshot count: recovery
  evidence performs a fourth read after the injected third final-verification
  failure. The regression now proves the exact third-call injection with an
  explicit flag while allowing that subsequent bounded evidence probe.
- Three exact wrapper-note regressions passed in both supported runtimes:
  Python 3.13.0 completed in 5.813 seconds and Xcode Python 3.9.6 in 5.773
  seconds. The complete five-test `post_commit` subset passed in 63.214 and
  63.679 seconds respectively. Ruff check/format and `git diff --check`
  passed.
- An independent read-only audit rechecked every committed-target OSError,
  generic Exception, special effective-destination, and BaseException branch,
  plus the real integration injection boundary. It returned `No findings.`
- Final exact-state full suites passed all 432 tests in both supported local
  runtimes: Python 3.13.0 completed in 964.951 seconds with 4 platform skips,
  and Xcode Python 3.9.6 completed in 985.234 seconds with 5 platform skips.
  A bounded follow-up inventory found no retained recent
  `project-journal-git-launch-*` directory.
- Final post-evidence verification passed Ruff check/format, both runtimes'
  `py_compile`, the installed Joey/OpenAI skill validator, both runtimes'
  project journal validation, and `git diff --check`. The two task-scoped
  bytecode caches were removed with the protected cleanup helper.
- Signed head `bccf397` passed exact-secret admission with complete temporary
  cleanup, was pushed to PR #5, and passed Linux Python 3.14.6 CI. A fresh
  independently materialized, strictly read-only whole-range named-single
  review returned `No findings.`; terminal guard validation passed, the
  trusted skill/guard digests remained unchanged, and the owner-private review
  workspace was removed only after proving no process held it.
- A fully paginated request-ledger race close proved zero requests on
  `bccf397` and no unresolved historical run. The one exact `@codex review`
  request was comment `5137568215`. Exact-bot terminal review `4824321375`,
  bound to `bccf397`, returned one P2: secondary `--git-dir` or
  `--git-common-dir` failure beneath `$CODEX_HOME/worktrees` returned `None`,
  so discovery could publish an unverifiable disposable worktree as a normal
  repository.
- Secondary metadata lookup now has only two successful outcomes: equal
  normalized Git/common directories prove and return the standalone root, or
  a distinct common directory independently resolves to a distinct source
  root. Unsupported/runtime errors, nonzero exits, malformed framing,
  unresolved or self-mapped sources, and source-resolution errors all emit
  `repository_resolution_failed` with
  `codex_worktree_mapping_unverified`; source cause, errno, and bounded notes
  remain attached. The unresolved row contains no repository or install/
  generate command.
- Seven focused positive, fail-closed, evidence-preservation, user-visible
  row, and documentation regressions passed in both supported runtimes:
  Python 3.13.0 completed in 10.198 seconds and Xcode Python 3.9.6 in 9.533
  seconds. Ruff check/format and `git diff --check` passed. An independent
  read-only audit checked both positive branches, every failure family,
  deadline reuse, exception priority, and row output and returned
  `No findings.`
- The exact-state Python 3.13.0 full suite passed all 437 tests in 1008.005
  seconds with 4 platform skips. A concurrent Xcode Python 3.9.6 run exposed
  one failure in the pre-existing descendant-process-group timeout regression;
  after the concurrent load ended, that exact test passed once and then three
  consecutive repetitions. The authoritative sequential Xcode Python 3.9.6
  full suite then passed all 437 tests in 709.152 seconds with 5 platform
  skips.
- Final post-evidence verification passed Ruff check/format, both runtimes'
  `py_compile`, both runtimes' project journal validation,
  `agents/openai.yaml` parsing, SKILL frontmatter/body and referenced-resource
  checks, and `git diff --check`. The installed OpenAI quick validator could
  not import its unbundled PyYAML dependency in the selected runtime, so the
  documented explicit fallback checks were used without installing a new
  dependency. The task-scoped bytecode cache was removed after owner/mode and
  no-open-holder checks.
- A fresh independently materialized named-single review of signed head
  `b22676b` found one P2: invalid-CWD or terminal deadline/limit exits could
  abandon the rollout-CWD and rollout-path generators without an explicit
  close, allowing cleanup evidence attached to `GeneratorExit` to disappear.
- The discovery caller now explicitly closes both iterators. The exact scan
  error remains primary; rollout-descriptor and directory-frame close failures
  are retained in order as bounded `cleanup_errors`. Retained coverage errors
  keep counters frozen at selection time and are serialized only after cleanup,
  including when the retained-error cap selects its synthetic terminal limit.
- Five new resource-ownership regressions and all 64 `discover-repos` tests
  passed in both supported runtimes. Python 3.13.0 completed the discovery set
  in 54.660 seconds and Xcode Python 3.9.6 in 59.357 seconds; all 30 rollout
  tests passed in 15.879 and 16.253 seconds respectively. Full-suite and final
  delivery evidence are recorded in the later checkpoints below.
- A parallel read-only implementation audit then found two bounded-state
  follow-ups: a standalone close failure selected as the `MAX+1` coverage
  error could disappear behind the synthetic error-count primary, and retained
  live tracebacks could pin the large rollout parser frame until final
  serialization. Close-only provenance now promotes that exact failure into
  the synthetic primary's cleanup evidence, while every handled retained error
  sheds traceback/context/cause references without losing its bounded fields,
  notes, or later cleanup attachments. The two new regressions and all 66
  `discover-repos` tests passed in both supported runtimes before the final
  full-suite gates below.
- The final audit found and closed two evidence-order refinements: a standalone
  close primary now precedes its nested frame cleanup under the synthetic cap,
  and same-frame entry-iterator plus descriptor failures are both structured
  rather than leaving the second only in an unserialized note. The exact
  same-frame two-fault regression passed under both runtimes, and the final
  read-only implementation audit returned `No findings.`
- Final exact-state full suites passed all 443 tests in both supported local
  runtimes: Python 3.13.0 completed in 446.961 seconds with 4 platform skips,
  and Xcode Python 3.9.6 completed in 569.791 seconds with 5 platform skips.
- A final fresh named-single review of signed head `d6d0bf7` found one P2 in
  `_close_hook_binding()`: its complete close drain was signal-fenced, but the
  binding retained stale descriptor ownership after dispatch. An interruption
  between owner close helpers could also escape the local aggregator, skip the
  remaining entry-time owners, and replace the active operation error.
- Hook binding ownership is now mutable only for terminal retirement. The
  function snapshots the complete entry-time owner set, keeps the existing
  POSIX fence through every single close dispatch, catches and records an
  inter-close `BaseException` without abandoning the drain, clears the lock and
  ancestor owner fields before mask restoration, and preserves the priority
  order active operation -> first close -> restoration. A failed fence
  acquisition still leaves the owner state intact and performs no unprotected
  close.
- The new regression injects `KeyboardInterrupt` after the first close helper
  returns, proves all three entry-time descriptors are dispatched once, keeps
  the exact active `LegacyInterrupt` primary with bounded interruption
  evidence, observes retired lock/ancestor ownership, and proves a second
  cleanup call performs no repeated close. All 10 focused hook-binding cleanup
  tests passed in Python 3.13.0 in 0.032 seconds and Xcode Python 3.9.6 in
  0.021 seconds.
- Final exact-state full suites passed all 444 tests: Python 3.13.0 completed in
  579.430 seconds with 4 platform skips, and Xcode Python 3.9.6 completed in
  571.828 seconds with 5 platform skips.
- The local CrashReporter follow-up separates the real default-action SIGQUIT
  case from ordinary discovery. Local SIGHUP/SIGTERM coverage still proves
  deferred process-group cleanup with real signals, while a custom-handler
  SIGQUIT test proves action return, cleanup, and propagation ordering without
  terminating the local test runner. The fatal integration remains in the
  repository and runs only when
  `PROJECT_JOURNAL_RUN_FATAL_SIGNAL_TESTS=1`; GitHub Actions fails closed when
  the repository variable is missing or differs from that exact value.
- The exact local state passed all 446 tests in 478.015 seconds with the fatal
  opt-in explicitly absent, plus Ruff, actionlint 1.7.12, Python compilation,
  and `git diff --check`. Signed commit `573f349` then passed hosted Python
  3.14.6 CI: all 446 ordinary tests passed, the workflow observed the exact
  repository variable value `1`, and the separately selected fatal SIGQUIT
  integration ran one test in 2.794 seconds and returned `OK` rather than a
  skip. An independent read-only audit found no default-action local SIGQUIT
  path or CI false-positive route.
- Exact-bot current-head review `4848531435` and inline comment `3707653887`
  identified that `Path.expanduser()` could raise for an unknown-user rollout
  CWD. Repository resolution now treats those values as literal paths and the
  regression proves both ordinary relative and leading-tilde spellings remain
  ignored without expansion.
- Python 3.14.0 passed the two exact regressions in 0.387 seconds, all 15 CWD
  tests in 5.138 seconds, and the complete 447-test suite in 392.201 seconds
  with five platform skips and the fatal SIGQUIT opt-in absent. Ruff 0.13.2
  check, Python compilation, project-journal validation, OpenAI quick skill
  validation through isolated PyYAML, and `git diff --check` passed. Ruff
  0.13.2's whole-file format check reports only the pre-existing HEAD line at
  `tests/test_project_journal.py:11770`; the current change adds no formatter
  diff of its own.
