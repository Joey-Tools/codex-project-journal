---
name: project-journal
description: Manage repository project journals across local and remote Codex work, including short `PROJECT_STATE` / `PROJECT_TODO` entrypoints, per-workstream `docs/project_journal/**` notes, ignored generated indexes, local Git hook installation, and Codex-session repo discovery.
---

# Project Journal

## Overview

Keep repo memory lightweight, durable, and low-conflict.
Use per-workstream journal files under `docs/project_journal/YYYY/MM/` as the default dynamic source of truth for a task, thread, PR, blocker, or handoff.
Use `PROJECT_STATE` only for stable repo-wide pulse, recovery pointers, and global blockers; use `PROJECT_TODO` only for cross-workstream actionable backlog.
Treat the generated `docs/project_journal/INDEX.md` as a local ignored convenience artifact, not as source of truth.

## Workflow

1. Treat the docs as the default convention.
- Read the repo `AGENTS.md`, existing docs, and the user request.
- For repositorys, assume `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md` should exist as stable entrypoints unless the user explicitly opts out or the repo already has a stronger equivalent tracker.
- If the repo already uses `docs/project_journal/`, read the relevant entries and optionally regenerate the ignored index before planning.

2. Recover context before planning.
- Reuse existing section names, task labels, and terminology.
- Keep top-level trackers short and stable; do not append ordinary PR/thread changelog noise to `PROJECT_STATE` or `PROJECT_TODO`.
- Use `scripts/project_journal.py discover-repos` when the task is to find repositorys recently touched by Codex sessions.
- Use `scripts/project_journal.py validate --repo <path>` to verify journal frontmatter before relying on a migrated journal set.

3. Create the right layer when setup is needed.
- If top-level trackers do not exist yet, create both files in the same task.
- If the task needs durable per-workstream state, create or update a journal note under `docs/project_journal/YYYY/MM/YYYY-MM-DD-<slug>-<shortid>.md`.
- Start from the templates in `references/templates.md`, then adapt to the repo.

4. Update them at the right moments.
- Early in the task: read them to recover context.
- Late in the task: sync the new current state, completed work, and next steps into the smallest applicable layer. For ordinary PR/thread/workstream updates, that layer is the relevant per-workstream journal, not the top-level entrypoints.
- Update `PROJECT_STATE` only when repo-wide state, recovery pointers, or global blockers change.
- Update `PROJECT_TODO` only when cross-workstream actionable backlog changes.
- Before a commit: include relevant doc updates in the same commit when they materially changed.
- If using per-workstream journals, run `scripts/project_journal.py validate --repo <path>` before committing.

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
- Keep `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md` as the top-level entrypoints, not as endless dumps.
- If either file becomes too long or mixes too many unrelated threads, move durable detail into per-workstream journals under `docs/project_journal/`.
- If a blocker cluster, closure plan, review bundle, or artifact summary needs more room than the top-level trackers should carry, create a focused note under `docs/notes/` or a comparable nearby location and link it from the trackers.
- Leave short pointers in the top-level file so a future Codex instance can still recover the active context quickly.
- Archive stale, completed, or superseded TODO clusters in journals instead of keeping them in the live backlog forever.

8. When migrating an existing repo, update the discovery pointers too.
- For discovery-driven migrations, first check whether `docs/PROJECT_STATE.md` or `docs/PROJECT_TODO.md` ever existed in git history. Skip repos that never committed either tracker unless the user explicitly asks to start journaling there.
- Treat one Git common dir as one migration target by default. Prefer the canonical checkout or target branch; list feature worktrees separately only when that branch still needs its own tracked journal state before merge.
- Keep personal, cloud-storage, downloaded-sample, and temporary replay repos out of default migration batches unless the user manually confirms them.
- Do not stop after splitting `PROJECT_STATE` and `PROJECT_TODO`; search repo-local guidance and documentation indexes for references to project records, `PROJECT_STATE`, `PROJECT_TODO`, and `project_journal`.
- Update repo `AGENTS.md` or repo-local skills so future agents know that the top-level trackers are stable short entrypoints, ordinary dynamic workstream state belongs in `docs/project_journal/YYYY/MM/*.md`, and generated `docs/project_journal/INDEX.md` is local and untracked.
- Keep README and docs index changes minimal: preserve existing `PROJECT_STATE` / `PROJECT_TODO` links, and add a pointer to `docs/project_journal/` when those files become the durable source of truth.
- Do not add `docs/project_journal/README.md` unless the validator explicitly excludes it or it uses valid journal frontmatter; `scripts/project_journal.py validate` treats Markdown files under `docs/project_journal/` as journal entries.
- For remote repos, make these guidance updates in the same migration PR/branch after confirming the canonical repo root and worktree layout.
- For multi-repo migrations or legacy tracker splits, load `references/migration-playbook.md` before editing.

9. Generate local indexes only as convenience artifacts.
- Use `scripts/project_journal.py generate --repo <path> --output docs/project_journal/INDEX.md --ensure-exclude` to refresh the local index.
- Use `scripts/project_journal.py install-hooks --repo <path>` only when the user wants opt-in local hook refresh for that repo.
- Do not commit `docs/project_journal/INDEX.md`; the helper writes it to `.git/info/exclude`.

10. Keep the signal high.
- `PROJECT_STATE` should answer: what is the repo-wide pulse, where is the recovery entrypoint, and what global blocker changes the next action.
- `PROJECT_TODO` should contain cross-workstream actionable backlog, not PR-local done/pending items or narrative status reports.
- `docs/project_journal/**` should contain durable per-workstream state, not a second append-only transcript.
- Move completed or inactive PR/thread-local TODOs into the relevant workstream journal instead of keeping them in the live top-level backlog.

## Guardrails

- Keep both top-level docs concise and stable, not exhaustive.
- Do not duplicate README, design docs, or PR summaries.
- Do not invent future work just to fill the files.
- If the repo has a stronger local convention, follow the repo over this skill.
- If the user explicitly chooses another tracking mechanism for the repo, follow that choice and stop enforcing these files.
- Prefer moving old detail out of the top-level files over deleting useful context outright; do this opportunistically when the related workstream is touched, not as a mandatory cleanup pass.
- Do not turn the docs into a fake append-only event log; keep checkpoints and evidence references compact.
- Top-level trackers should point to focused notes when needed, not absorb every long blocker narrative inline.
- Do not batch-install hooks across repositorys by default; first generate a candidate report with `discover-repos`.
- Remote hosts use the same personal skill script host-locally; let `$remote-host-context` own remote evidence gathering and host selection.
- `scripts/project_journal.py` is intentionally stdlib-only so it can run in local repos, temporary validation repos, and remote hosts after skill sync.
- During migrations, preserve every old tracker item somewhere intentional: active backlog, completed/history journal, superseded note, or legacy snapshot. Do not leave actionable items only in the snapshot.
- Do not require a second migration from date-based journals into slot or active directories; keep using existing `docs/project_journal/YYYY/MM/*.md` unless the repo has a stronger local convention.
- Do not commit generated `docs/project_journal/INDEX.md`, local hooks, or transient PR/branch states unless the user explicitly asks for that exact local state to be tracked.

## References

- Use `references/templates.md` for starter structures and wording patterns.
- Use `references/migration-playbook.md` for repo migration campaigns, legacy tracker splitting, candidate filtering, clean-context review, and merge handling.
