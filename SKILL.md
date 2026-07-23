---
name: project-journal
description: Maintain repository project journals and their optional local tooling. Use only when the repo already uses `docs/project_journal/`, repo policy requires this workflow, or a task spans Codex sessions, a PR, or a durable workstream. Require an explicit product need before introducing the first tracker into an unadopted repo. Update the smallest applicable journal layer, preserve squash-merge target-branch semantics, and generate indexes or install hooks only when their workflows need them.
---

# Project Journal

## Overview

Keep repo memory lightweight, durable, and low-conflict.
Treat project journals as an adopted or explicitly justified repo workflow, not as a default bootstrap for every repository.
Within that workflow, use per-workstream journal files under `docs/project_journal/YYYY/MM/` as the dynamic source of truth for a task, thread, PR, blocker, or handoff.
Use an existing or explicitly needed `PROJECT_STATE` only for stable repo-wide pulse, recovery pointers, and global blockers; use an existing or explicitly needed `PROJECT_TODO` only for cross-workstream actionable backlog.
Treat the generated `docs/project_journal/INDEX.md` as a local ignored convenience artifact, not as source of truth.

## Workflow

1. Decide whether the workflow applies and whether first adoption is justified.
- Read the repo `AGENTS.md`, existing docs, and the user request.
- Auto-trigger this skill only when the repo already uses `docs/project_journal/`, repo policy requires project journaling, or the task spans Codex sessions, a PR, or a durable workstream.
- If a spanning task is the only trigger and the repo has neither an adopted journal nor a policy requirement, treat the trigger as a reason to assess durable state, not as permission to create files.
- Before introducing the first tracker into an unadopted repo, identify an explicit product need in the user request or established project workflow for repo-owned coordination, recovery, or backlog state. General preference for journaling is not enough.
- If that need is absent, use the current task, PR, issue, or handoff channel and leave `docs/PROJECT_STATE.md`, `docs/PROJECT_TODO.md`, and `docs/project_journal/` unchanged.
- If the repo has a stronger equivalent tracker or the user chooses another mechanism, follow it instead.

2. Recover context before planning.
- If the repo already uses `docs/project_journal/`, read only the relevant workstream entries before planning.
- Read `PROJECT_STATE` and `PROJECT_TODO` when they exist and are relevant; do not create a missing counterpart merely because one exists.
- Reuse existing section names, task labels, and terminology.
- Keep top-level trackers short and stable; do not append ordinary PR/thread changelog noise to `PROJECT_STATE` or `PROJECT_TODO`.
- Use the bundled helper script when the task is to find repositories recently touched by Codex sessions.
- Use the bundled helper script to verify journal frontmatter before relying on a migrated journal set.
- Do not generate the local index merely to read or update one known entry.

### Helper Script Path

`scripts/project_journal.py` belongs to this skill, not to every target repository.
When invoking the helper from a target repo, resolve the script relative to the loaded skill directory and call it with `python3`, for example:

```bash
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
- Treat `discover-repos` output as a candidate report; discovery does not establish adoption or authorize tracker, index, or hook creation.
- Do not commit `docs/project_journal/INDEX.md`; the helper writes it to `.git/info/exclude`.

10. Keep the signal high.
- `PROJECT_STATE` should answer: what is the repo-wide pulse, where is the recovery entrypoint, and what global blocker changes the next action.
- `PROJECT_TODO` should contain cross-workstream actionable backlog, not PR-local done/pending items or narrative status reports.
- `docs/project_journal/**` should contain durable per-workstream state, not a second append-only transcript.
- Move completed or inactive PR/thread-local TODOs into the relevant workstream journal instead of keeping them in the live top-level backlog.

## Guardrails

- Do not assume every Joey repository should adopt project journals.
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
- During migrations, preserve every old tracker item somewhere intentional: active backlog, completed/history journal, superseded note, or legacy snapshot. Do not leave actionable items only in the snapshot.
- Do not require a second migration from date-based journals into slot or active directories; keep using existing `docs/project_journal/YYYY/MM/*.md` unless the repo has a stronger local convention.
- Do not commit generated `docs/project_journal/INDEX.md`, local hooks, or transient PR/branch states unless the user explicitly asks for that exact local state to be tracked.

## References

- Use `references/templates.md` for starter structures and wording patterns.
- Use `references/migration-playbook.md` for repo migration campaigns, legacy tracker splitting, candidate filtering, clean-context review, and merge handling.
