# Project Journal Migration Playbook

Use this reference when converting existing repositories from large tracked
`docs/PROJECT_STATE.md` / `docs/PROJECT_TODO.md` files to short entrypoints plus
per-workstream journal files. Prefer changing future write behavior over
performing a second broad file migration.
Treat this as an explicit adoption or maintenance workflow. Do not start a
migration merely because a repository belongs to Joey or lacks the current
journal layout.
An empty `docs/project_journal/` directory or generated `INDEX.md` is not
adoption evidence; require repo policy or a valid tracked non-generated entry.

## Candidate Filtering

- Start with the bundled `project_journal.py discover-repos` helper; do not crawl the full
  filesystem.
- Treat discovery output and historical tracker presence as candidate evidence,
  not as authorization to introduce or migrate trackers.
- For each discovered repo, check git history for committed trackers:
  `git log --all -- docs/PROJECT_STATE.md docs/PROJECT_TODO.md`.
- Skip repos with no tracker history unless the user explicitly asks to start
  journaling there.
- Treat one Git common dir as one default migration target. Prefer the canonical
  checkout or target branch, and only migrate feature worktrees separately when
  their tracked docs must stay branch-local before merge.
- Keep personal/cloud-storage paths, downloaded samples, and Codex replay
  worktrees in manual-confirm or skip buckets.

## Repo Preflight

- Confirm current branch, default or target branch, remotes, dirty status,
  existing `AGENTS.md`, README/docs indexes, and existing journal directory.
- Check whether the repo uses PRs, squash merge, or direct local commits. In
  squash-merge repos, tracked docs should describe the target branch after merge.
- Do not install hooks, generate a committed index, or push branches as part of
  the migration unless the user asks for those actions.

## Migration Shape

- Keep `docs/PROJECT_STATE.md` short and stable: repo-wide pulse, recovery
  pointers, global blockers, and pointers to relevant journals.
- Keep `docs/PROJECT_TODO.md` short: cross-workstream actionable backlog and
  pointers to journals.
- Create focused journal entries under `docs/project_journal/YYYY/MM/` for
  active workstreams, completed/history workstreams, and a legacy tracker
  snapshot when old trackers contain useful detail.
- Preserve every old tracker item intentionally. Active or deferred items must
  remain executable outside the legacy snapshot; completed or superseded items
  can live in history/snapshot entries with evidence.
- Do not require a second migration into `active/`, slot files, or renamed
  journal directories. Existing date-based per-workstream journals remain valid.
- Clean up old top-level tracker bullets opportunistically when the related
  workstream is touched; do not start a broad cleanup PR solely to make the
  entrypoints prettier.
- Update repo-local `AGENTS.md`, README, or docs index so future agents know
  where durable project records belong.

## Review And Merge

- Run the bundled `project_journal.py validate --repo <repo>` helper and
  `git diff --check` before review or commit.
- Use a clean-context review for migrations with substantial legacy tracker
  content. Ask the reviewer to compare old `PROJECT_STATE/TODO` against the new
  entrypoints and journals, especially active backlog preservation.
- For repos with no PR history, a local migration branch plus manual
  squash/cherry-pick can be enough. Do not invent a PR process solely for
  bookkeeping.
- Keep transient states such as "ready for review" or "waiting for merge" in PR
  text or branch notes, not in tracked journal docs.
- After merge, update the migration campaign record with the final target-branch
  commit and close obsolete next steps. Prefer updating the relevant workstream
  journal rather than top-level trackers unless the repo-wide recovery path
  changed.
