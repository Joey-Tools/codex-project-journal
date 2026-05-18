# Project Journal Templates

## Minimal `docs/PROJECT_STATE.md`

```md
# Project State

## Current State
- One or two stable bullets that describe the repo-wide status.
- Per-workstream state, handoffs, and PR-local next steps live under `docs/project_journal/`.

## Recovery Pointers
- Active workstream: `docs/project_journal/2026/03/2026-03-07-codec-fallback-a1b2c3.md`
- Local index: optional generated `docs/project_journal/INDEX.md`; regenerate with `scripts/project_journal.py generate`

## Global Blockers
- Only blockers that affect the whole repo or next recovery path.

## Notes
- Do not update this file for ordinary PR-local progress.
```

## Minimal `docs/PROJECT_TODO.md`

```md
# Project TODO

- [pending] Cross-workstream follow-up that should remain visible from the repo root.
- [blocked] Repo-wide blocker that is not owned by a single workstream journal.
- Use `[done]` or `[in_progress]` here only for repo-wide backlog; keep ordinary task-local status in the workstream journal.
```

## Minimal `docs/project_journal/YYYY/MM/YYYY-MM-DD-<slug>-<shortid>.md`

```md
---
id: 20260505-a1b2c3
title: Short Workstream Title
status: active
created: 2026-05-05
updated: 2026-05-05
branch:
pr:
supersedes: []
superseded_by:
---

# Short Workstream Title

## Summary
- The durable current state for this thread, PR, blocker, or workstream.

## Current State
- What is true now.

## Next Steps
- What should happen next.

## Evidence
- Commit, PR, build URL, log path, session id, or related note.
```

## Minimal Focused Note

```md
# Focused Note Title

## Context
- Short background for why this note exists.

## Findings Or Blockers
- The key blocker, risk, or evidence cluster.

## Next Steps
- What should happen next.

## Evidence
- Build URL, log path, commit, issue, or related detail file.
```

## Writing Rules

- `PROJECT_STATE` is the short repo memory for "where things stand now".
- `PROJECT_TODO` is the cross-workstream actionable backlog for repo-root visibility.
- `docs/project_journal/YYYY/MM/*.md` is the durable per-workstream source of truth and the default place for ordinary PR/thread state.
- `docs/project_journal/INDEX.md` is generated locally and should not be committed.
- Do not update top-level trackers for ordinary PR-local progress; update the relevant per-workstream journal instead.
- Prefer updating existing workstream journal bullets over appending a new dated section every time.
- Use a compact handoff block when the work crosses phases, pauses, or changes owner.
- Handoff summaries should cite evidence or detail files instead of restating every fact inline.
- Keep the top-level docs as stable entrypoints; move dynamic detail into topic/date subfiles by default.
- Use a focused note when a blocker set or closure plan would make the top-level trackers noisy.
- For squash-merge PRs, write tracked journal docs as the target-branch state after the PR lands, preferring the relevant per-workstream journal over top-level entrypoints. If the PR fully completes the workstream, `status: completed` is appropriate before merge; use the PR link as evidence and keep transient review or merge states in the PR body, checklist, or comments.
- Run `scripts/project_journal.py validate --repo <path>` when using journal frontmatter.
- Run `scripts/project_journal.py generate --repo <path> --output docs/project_journal/INDEX.md --ensure-exclude` to refresh the ignored local index.
- If the repo already has task IDs or status labels, keep them consistent.
