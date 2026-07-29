---
name: project-journal
description: Maintain repository project journals and their optional local tooling. Use only when repo policy requires this workflow, the repo has at least one valid tracked non-generated entry under `docs/project_journal/`, or a task spans Codex sessions, a PR, or a durable workstream. Require an explicit product need before introducing the first tracker into an unadopted repo. Update the smallest applicable journal layer, preserve squash-merge target-branch semantics, and generate indexes or install hooks only when their workflows need them.
---

# Project Journal

## Overview

Keep repo memory lightweight, durable, and low-conflict.
Treat project journals as an adopted or explicitly justified repo workflow, not as a default bootstrap for every repository.
Treat the workflow as adopted only when repo policy requires it or at least one valid tracked non-generated journal entry exists.
Within that workflow, use per-workstream journal files under `docs/project_journal/YYYY/MM/` as the dynamic source of truth for a task, thread, PR, blocker, or handoff.
Use an existing or explicitly needed `PROJECT_STATE` only for stable repo-wide pulse, recovery pointers, and global blockers; use an existing or explicitly needed `PROJECT_TODO` only for cross-workstream actionable backlog.
Treat the generated `docs/project_journal/INDEX.md` as a local ignored convenience artifact, not as source of truth.

## Workflow

1. Decide whether the workflow applies and whether first adoption is justified.
- Read the repo `AGENTS.md`, existing docs, and the user request.
- Auto-trigger this skill only when repo policy requires project journaling, the bundled helper reports a valid tracked non-generated journal entry, or the task spans Codex sessions, a PR, or a durable workstream.
- Run the bundled helper's `adoption-status --repo <path>` command to inspect tracked journal evidence. Directory presence, untracked files, and an empty or generated-`INDEX.md`-only directory do not establish adoption.
- Treat `adoption-status` as index-authoritative: it accepts only unconflicted stage-0 regular-file entries and validates the exact indexed blob. Worktree file type or content cannot create or remove adoption evidence.
- Before any repository Git read, the helper opens one resolved Git executable with required `O_NOFOLLOW|O_NONBLOCK`, binds the selected pathname and descriptor to the same initial regular-file identity and access policy, and copies stable content under a 64 MiB ceiling and one monotonic time budget into an owner-private snapshot. Only snapshotted ELF or Mach-O native launchers are accepted; script wrappers and unknown formats fail closed because private-copy execution cannot preserve relative interpreter or wrapper location semantics. It runs the bounded credential-free `git --version` gate against those exact bytes; Git older than 2.45 fails closed. Before every repository command, it reopens the snapshot with required no-follow/nonblocking flags, distinguishes identity, content, and access-policy changes, and copies the verified descriptor bytes into a fresh owner-private command launch. It locks the launch directory, repeats descriptor/path identity, access, size, and digest validation, and makes `Popen` execute that copy with the durable source path as `argv[0]`. This preserves native Git runtime-prefix and `%(prefix)` resolution; the copied-byte version gate fails closed if startup dependencies cannot load from the private runtime location. The self-managed launch directory remains until the direct child reaches a verified terminal state; an unverified terminal retains and reports the locator. Launch-copy and process-group cleanup failures preserve the exact original exception object, type, code, arguments, and traceback while attaching bounded cleanup evidence, including on runtimes without `BaseException.add_note`. Verification, launch copying, process startup, command execution, and the initialization final check share their single applicable monotonic deadline. The helper clears ambient Git control variables, disables external Git configuration and lazy object fetching, and never executes the mutable source executable. The copy-stage deadline prevents blocking on a substituted FIFO but does not claim to preempt one stalled regular-file kernel operation or a malicious same-UID process.
- Adoption validation establishes one absolute monotonic deadline before repository resolution, then binds that bounded resolution, the initial raw index snapshot, one bounded `git cat-file --batch` session, frontmatter parsing, semantic validation, valid-entry classification, and final raw index revalidation to the same deadline. It validates exact OID/type/size/content framing, invalidates every member of a duplicate-ID group, and enforces index, entry, frontmatter field/list, validation-issue, byte, record, and stderr limits. Per-path validation state is structured; it does not recover validity by rescanning formatted issue strings.
- The helper supports only macOS and Linux and rejects every other platform, including other POSIX systems, before selecting or executing Git, so its process and filesystem safety properties are not silently weakened. The direct child stays unreaped as the PID/PGID identity fence while status is observed with `WNOWAIT`; after Linux confirms that leader has exited, a bounded `/proc` scan excludes the retained zombie leader while proving whether any live nonleader still belongs to the group; macOS retains signal-zero group probes. Successful and failed commands both complete process-group cleanup before that leader is reaped. Explicit ownership states guard the transition from numeric process-group cleanup to reap-only cleanup, and no numeric PGID is signalled after that fence is released. Error-path cleanup reuses the already claimed ownership object; if cleanup itself raises, only a proven reap-only state permits the final reap, and bounded cleanup exception evidence attaches to the original action exception instead of replacing it. A missing fence, interrupted ownership handoff, or unverified final cleanup is reported as `cleanup-incomplete`. A redirected, incomplete, malformed, changed, over-limit, or cleanup-incomplete validation fails closed.
- CLI parsing, including `--help`, completes before Git runtime initialization. Git runtime initialization and one command dispatch then share one deferred-signal lifetime. Before either protected action, the macOS/Linux helper requires working POSIX `pthread_sigmask`, `sigpending`, and synchronous `sigwait` support; use libc `sigwait` when Python lacks it and otherwise fail closed. Block candidate `SIGHUP`, `SIGTERM`, and `SIGQUIT` signals while installing handlers, leave `SIG_IGN` and caller-blocked signals untouched, and let the handler record only the first managed request without performing cleanup. Bounded checkpoints raise that request through existing `BaseException` cleanup paths so launch-owned process groups are drained and hook rename state is revalidated. At terminal exit, block all actually managed signals and unconditionally commit process-private Git runtime cleanup, including after normal return; all Git subprocesses for the command have already finished, and a later in-process `main()` call initializes a fresh runtime. A terminal cleanup with no retained runtime clears a cached initialization failure so a repaired `PATH` can retry, while incomplete cleanup retains the runtime and locator-bearing error. An ordinary `Exception` from terminal runtime cleanup becomes the locator-bearing cleanup issue. A non-`Exception` `BaseException` such as `KeyboardInterrupt` or `SystemExit` instead receives bounded retained-locator evidence. When that interruption is the sole terminal failure and no managed signal is being propagated, it is re-raised without replacement, preserving the same object, type, arguments, and traceback. Against the cleanup failure, an active action exception remains primary and receives the cleanup evidence; existing precedence for an earlier terminal-convergence failure or managed-signal propagation is unchanged. If terminal mask acquisition fails, outer convergence must still commit runtime cleanup, retry the mask only after that commit, and otherwise perform best-safe handler and known-mask restoration plus active-state reset before surfacing the original failure. While still masked, consume pending managed signals with `sigpending` plus `sigwait` before and after restoring the original handlers, report any cleanup-failure locator, requeue only the recorded first signal, and atomically restore the prior mask. When the protected action raises, preserve that exact action exception as primary even if terminal convergence also fails; attach bounded convergence details, existing action recovery notes, and any cleanup locator, including through the fallback for Python runtimes without `BaseException.add_note`. Pending-signal reporting reserves its final bounded detail slot for a runtime cleanup issue so saturated action notes cannot hide the retained locator. This makes `SIG_DFL` terminate, invokes a restored custom handler once even when it returns, preserves ignored signals, and keeps a signal generated in the final check-to-unmask window behind an already terminal runtime state.
- Snapshot cleanup during creation, the version probe, or final validation preserves an active failure as the exact primary exception and attaches bounded retained-locator evidence. With no active exception, report the cleanup failure as the initialization failure.
- If a spanning task is the only trigger and the repo has neither an adopted journal nor a policy requirement, treat the trigger as a reason to assess durable state, not as permission to create files.
- Before introducing the first tracker into an unadopted repo, identify an explicit product need in the user request or established project workflow for repo-owned coordination, recovery, or backlog state. General preference for journaling is not enough.
- If that need is absent, use the current task, PR, issue, or handoff channel and leave `docs/PROJECT_STATE.md`, `docs/PROJECT_TODO.md`, and `docs/project_journal/` unchanged.
- If the repo has a stronger equivalent tracker or the user chooses another mechanism, follow it instead.

2. Recover context before planning.
- If the workflow is adopted, read only the relevant workstream entries before planning.
- Read `PROJECT_STATE` and `PROJECT_TODO` when they exist and are relevant; do not create a missing counterpart merely because one exists.
- Reuse existing section names, task labels, and terminology.
- Keep top-level trackers short and stable; do not append ordinary PR/thread changelog noise to `PROJECT_STATE` or `PROJECT_TODO`.
- Use the bundled helper script when the task is to find repositories recently touched by Codex sessions.
- Repository discovery streams active `sessions` and flat `archived_sessions` through one shared absolute deadline and aggregate filesystem-entry, logical-rollout, total-byte, line-byte, record, normalized-distinct-CWD, JSON-depth, JSON-integer-digit, CWD-UTF-8-byte, CWD-component, and retained-error caps. Consume every physical active/archive file sharing a rollout basename and union its CWD evidence, but count each repository association once; an unreadable or unparsable copy makes coverage partial. Enforce strict CWD encoding and its byte/component caps before constructing a `Path` or probing parents. Require `coverage_status: complete` before treating the candidate set as complete; a deadline, limit, non-empty record parse failure, invalid CWD, or source I/O failure preserves already found rows but adds structured `discovery_coverage`, and a partial scan with no repository emits an inconclusive sentinel instead of `[]`.
- In `discover-repos` output, treat `adoption_status` as the authoritative tag and require it to be `adopted` or `unadopted` before consuming adoption evidence. `inconclusive` carries a structured `adoption_error` and null adoption fields; it is not evidence that the repository is unadopted. Each candidate receives one deadline before bounded repository resolution, and its first adoption check immediately consumes the same remaining budget rather than resetting it after the rollout scan. If a later CWD reaches the same root with at least one additional second remaining, retry a still-inconclusive adoption check once; each root is enriched at most twice. Auxiliary worktree failures are isolated to that row through `discovery_status` and a field-keyed `discovery_error`; they null only affected auxiliary fields and do not overwrite authoritative index adoption. Inaccessible journal, index, exclude, or hook paths remain null and inconclusive rather than becoming false or zero. Treat `false` as an authoritative negative check and `null` as unknown, including when effective hook configuration cannot be proved.
- Generated-index classification reads only the bounded three-line marker prefix. A file that truly disappeared is skipped; permission/read failures, dangling symlinks, directories, and over-limit marker candidates propagate as structured `journal_count` errors, leaving the count null rather than classifying them as ordinary journals.
- Use the bundled helper script to verify journal frontmatter before relying on a migrated journal set.
- Do not generate the local index merely to read or update one known entry.

### Helper Script Path

`scripts/project_journal.py` belongs to this skill, not to every target repository.
When invoking the helper from a target repo, resolve the script relative to the loaded skill directory and call it with `python3`, for example:

```bash
python3 "<loaded-skill-dir>/scripts/project_journal.py" adoption-status --repo <path>
python3 "<loaded-skill-dir>/scripts/project_journal.py" validate --repo <path>
```

Use a repo-relative `python3 scripts/project_journal.py ...` command only when the current repo is this skill's source checkout and the script file exists there.
Do not report the journal validator as unavailable merely because `<target-repo>/scripts/project_journal.py` is missing.

3. Update the smallest applicable layer automatically.
- Once the workflow applies and any first-adoption gate is satisfied, update the smallest applicable existing or required layer without waiting for another prompt.
- For ordinary task, PR, thread, blocker, or handoff state, create or update the relevant journal note under `docs/project_journal/YYYY/MM/YYYY-MM-DD-<slug>-<shortid>.md`.
- Update or introduce `PROJECT_STATE` only when the repo-wide pulse, recovery path, or a global blocker needs a stable entrypoint.
- Update or introduce `PROJECT_TODO` only when a cross-workstream actionable backlog needs repo-root visibility.
- Do not create both top-level files as a pair by default. During first adoption, introduce only the layer justified by the explicit product need.
- Start from the templates in `references/templates.md`, then adapt to the repo.

4. Update them at the right moments.
- Early in the task: read them to recover context.
- Late in the task: automatically sync the new current state, completed work, and next steps into the smallest applicable layer. For ordinary PR/thread/workstream updates, that layer is the relevant per-workstream journal, not the top-level entrypoints.
- Update `PROJECT_STATE` only when repo-wide state, recovery pointers, or global blockers change.
- Update `PROJECT_TODO` only when cross-workstream actionable backlog changes.
- Before a commit: include relevant doc updates in the same commit when they materially changed.
- If using per-workstream journals, run the bundled helper's `validate --repo <path>` command before committing.

5. Prepare PR-bound docs for target-branch semantics.
- Before marking a PR ready, check the repo's merge model from repo guidance, branch protection, PR settings, or existing project convention.
- In squash-merge repos, tracked journal docs should describe the target branch after the PR lands. If the PR fully completes a workstream, set the journal `status: completed` before merge and use the PR link as evidence.
- Do not leave tracked docs saying "waiting for merge", "not merged yet", "ready for review", or similar transient PR states. Put those states in the PR body, checklist, or review comments instead.
- If the PR only completes part of a larger workstream, keep the workstream journal `active` or `blocked`, record the completed slice, and leave only real follow-up work in `Next Steps`.
- If the merge model is unclear or not squash-merge-only, keep each commit's tracked docs self-consistent and do not mark a workstream completed before that same commit contains the completed implementation, docs, and validation evidence.

6. Leave compact handoff checkpoints when a phase changes.
- If the work is pausing, changing owner, or moving from discovery to implementation, testing, or review, add a short handoff block instead of a long narrative dump.
- Prefer a small structured shape such as phase, summary, next steps, blockers, and evidence references.
- Put PR/thread-local handoff blocks in the relevant workstream journal. Use `PROJECT_STATE` for handoff only when it changes the repo-wide recovery path.
- Evidence references can be commit hashes, PR links, build URLs, log paths, issue IDs, or links to topic/date subfiles.

7. Split or archive when the top-level docs stop being scannable.
- When present, keep `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md` as concise top-level entrypoints, not as endless dumps.
- If either file becomes too long or mixes too many unrelated threads, move durable detail into per-workstream journals under `docs/project_journal/`.
- If a blocker cluster, closure plan, review bundle, or artifact summary needs more room than the top-level trackers should carry, create a focused note under `docs/notes/` or a comparable nearby location and link it from the trackers.
- Leave short pointers in the top-level file so a future Codex instance can still recover the active context quickly.
- Archive stale, completed, or superseded TODO clusters in journals instead of keeping them in the live backlog forever.

8. When migrating an existing repo, update the discovery pointers too.
- Treat migration as an explicit adoption or maintenance workflow, not as incidental cleanup during an unrelated task.
- For discovery-driven migrations, first check whether `docs/PROJECT_STATE.md` or `docs/PROJECT_TODO.md` ever existed in git history. Skip repos that never committed either tracker unless the user explicitly asks to start journaling there.
- Existing tracker history makes a repo a migration candidate; it does not by itself require migration.
- Treat one Git common dir as one migration target by default. Prefer the canonical checkout or target branch; list feature worktrees separately only when that branch still needs its own tracked journal state before merge.
- Keep personal, cloud-storage, downloaded-sample, and temporary replay repos out of default migration batches unless the user manually confirms them.
- Do not stop after splitting `PROJECT_STATE` and `PROJECT_TODO`; search repo-local guidance and documentation indexes for references to project records, `PROJECT_STATE`, `PROJECT_TODO`, and `project_journal`.
- Update repo `AGENTS.md` or repo-local skills so future agents know that the top-level trackers are stable short entrypoints, ordinary dynamic workstream state belongs in `docs/project_journal/YYYY/MM/*.md`, and generated `docs/project_journal/INDEX.md` is local and untracked.
- Keep README and docs index changes minimal: preserve existing `PROJECT_STATE` / `PROJECT_TODO` links, and add a pointer to `docs/project_journal/` when those files become the durable source of truth.
- Do not add `docs/project_journal/README.md` unless the validator explicitly excludes it or it uses valid journal frontmatter; the bundled helper's `validate` command treats Markdown files under `docs/project_journal/` as journal entries.
- For remote repos, make these guidance updates in the same migration PR/branch after confirming the canonical repo root and worktree layout.
- For multi-repo migrations or legacy tracker splits, load `references/migration-playbook.md` before editing.

9. Generate local tooling only for the workflow that needs it.
- Use the bundled helper's `generate --repo <path> --output docs/project_journal/INDEX.md --ensure-exclude` command only when the active workflow needs multi-entry navigation, an explicit index refresh, or an already opted-in hook refresh.
- Do not regenerate an index on every skill invocation or single-entry update.
- Use the bundled helper's `install-hooks --repo <path>` command only when the user explicitly wants opt-in local hook refresh for that repo.
- Hook installation supports macOS and Linux only. It accepts system configuration only when `GIT_CONFIG_NOSYSTEM=1` disables it or `GIT_CONFIG_SYSTEM` names an explicit absolute path; the implicit build-dependent system path fails closed. The helper snapshots explicit system and global Git configuration through `O_NOFOLLOW|O_NONBLOCK` descriptors, requires regular files, performs bounded double reads without following includes, and refuses installation when either scope sets `core.hooksPath` or contains an unresolved include. Set an explicit repo-local `core.hooksPath` and rerun when instructed; that remediation names the durable selected source Git executable, never the helper's process-private snapshot. Effective worktree and local values are queried through trusted Git with `--type=path --null`, so `%(prefix)` expansion and significant leading or trailing whitespace are preserved before the result is constrained to the repository and Git roots. Installation binds the complete absolute path from the filesystem root, traverses and creates allowed relative components with descriptor-relative `O_NOFOLLOW` operations, retains and revalidates every ancestor identity/access policy, and opens existing targets with required `O_NOFOLLOW|O_NONBLOCK` flags. It serializes cooperating installers with an owner-private lock, stages owner-private files in the same directory, and commits with native no-replace or exchange rename semantics. For an absent target, `EEXIST` and `ENOTEMPTY` prove an uncommitted conflict; any other rename error triggers immediate exact target-and-staging revalidation so a proved commit continues directory durability and final verification, a proved uncommitted operation cleans its staging file, and an uncertain result preserves and reports its recovery locator. An absent, replaced, or content-changed target otherwise fails closed; a mismatched exchanged entry is rolled back only while the installed object still belongs to the current transaction. If rollback fails or its object bindings become uncertain, the helper preserves the displaced entry under a reported recovery locator instead of deleting it. Before displaced-hook deletion, it verifies both the displaced and installed objects and enters an explicit installed-target-committed state. An asynchronous interruption before that state preserves the temporary entry and reports whether it verifies as the displaced-hook recovery object; an interruption during post-commit deletion reports either a retained cleanup locator or completed cleanup and never claims a missing recovery object. Every published recovery or cleanup locator is resolved from the held root-to-directory descriptor chain, accepts renamed components only after a bounded descriptor-relative identity/access-policy match, and is emitted only after revalidating the held directory and leaf object/type/ownership/mode/size/content binding. If the current path cannot be resolved safely, the report marks it `path_unverified` and records structured directory device/inode plus leaf and held-object evidence before closing the descriptors. Target revalidation protects path and target object identity, type, ownership, access mode, size, and content while allowing timestamp and directory child-entry churn. This contract detects ordinary concurrent installer races; it does not claim to exclude an actively malicious same-UID process that ignores the protocol and mutates entries after a final verified kernel operation.
- If cleanup of an uncommitted staging entry fails while an installation error is active, preserve that exact installation error and attach bounded descriptor-bound locator evidence after path verification; report an unresolved path as structured `path_unverified` evidence instead. With no active error, report the cleanup failure independently.
- Treat `discover-repos` output as a candidate report; discovery does not establish adoption or authorize tracker, index, or hook creation.
- Do not commit `docs/project_journal/INDEX.md`; the helper writes it to `.git/info/exclude`.

10. Keep the signal high.
- `PROJECT_STATE` should answer: what is the repo-wide pulse, where is the recovery entrypoint, and what global blocker changes the next action.
- `PROJECT_TODO` should contain cross-workstream actionable backlog, not PR-local done/pending items or narrative status reports.
- `docs/project_journal/**` should contain durable per-workstream state, not a second append-only transcript.
- Move completed or inactive PR/thread-local TODOs into the relevant workstream journal instead of keeping them in the live top-level backlog.

## Guardrails

- Do not assume every Joey repository should adopt project journals.
- Do not treat an empty `docs/project_journal/`, untracked entries, or a generated `INDEX.md` as adoption evidence.
- Do not bootstrap `PROJECT_STATE`, `PROJECT_TODO`, or `docs/project_journal/` without the first-adoption product-need gate.
- Do not create a missing top-level companion file solely for symmetry.
- Keep any top-level tracker concise and stable, not exhaustive.
- Do not duplicate README, design docs, or PR summaries.
- Do not invent future work just to fill the files.
- If the repo has a stronger local convention, follow the repo over this skill.
- If the user explicitly chooses another tracking mechanism for the repo, follow that choice and stop enforcing these files.
- Prefer moving old detail out of the top-level files over deleting useful context outright; do this opportunistically when the related workstream is touched, not as a mandatory cleanup pass.
- Do not turn the docs into a fake append-only event log; keep checkpoints and evidence references compact.
- Top-level trackers should point to focused notes when needed, not absorb every long blocker narrative inline.
- Do not batch-install hooks across repositories by default; first generate a candidate report with `discover-repos`.
- Remote hosts use the same personal skill script host-locally; let `$remote-host-context` own remote evidence gathering and host selection.
- The bundled `scripts/project_journal.py` is intentionally stdlib-only so it can run in local repos, temporary validation repos, and remote hosts after skill sync.
- Hook installation keeps the installed-target-committed state after the transaction temporary is consumed and through directory fsync plus final object-identity/content/access-policy verification. A failure or interruption after commit is an incomplete durability or verification result, not proof that installation failed and not a displaced-object recovery locator.
- During migrations, preserve every old tracker item somewhere intentional: active backlog, completed/history journal, superseded note, or legacy snapshot. Do not leave actionable items only in the snapshot.
- Do not require a second migration from date-based journals into slot or active directories; keep using existing `docs/project_journal/YYYY/MM/*.md` unless the repo has a stronger local convention.
- Do not commit generated `docs/project_journal/INDEX.md`, local hooks, or transient PR/branch states unless the user explicitly asks for that exact local state to be tracked.

## References

- Use `references/templates.md` for starter structures and wording patterns.
- Use `references/migration-playbook.md` for repo migration campaigns, legacy tracker splitting, candidate filtering, clean-context review, and merge handling.
