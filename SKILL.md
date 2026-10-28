---
name: project-journal
description: Maintain repository project journals under `docs/project_journal/`. Use only when repo policy requires this workflow, the repo has at least one valid tracked non-generated entry under `docs/project_journal/`, or the user explicitly requests project journals or repository-owned durable coordination, recovery, or backlog state. A PR or multi-session task alone is not a trigger. Require an explicit product need before introducing the first tracker into an unadopted repo. Update the smallest applicable journal layer and preserve squash-merge target-branch semantics.
---

# Project Journal

Keep durable, low-conflict repo memory in per-workstream journals under
`docs/project_journal/YYYY/MM/`. Optional `docs/PROJECT_STATE.md` (repo-wide
pulse, recovery pointers, global blockers) and `docs/PROJECT_TODO.md`
(cross-workstream backlog) are short, stable entrypoints. The generated
`docs/project_journal/INDEX.md` is a local, ignored convenience, never a
source of truth.

### Helper Script Path

`scripts/project_journal.py` belongs to this skill, not to target repos.
Resolve it relative to the loaded skill directory:

```bash
python3 "<loaded-skill-dir>/scripts/project_journal.py" adoption-status --repo <path>
python3 "<loaded-skill-dir>/scripts/project_journal.py" validate --repo <path>
```

Use a repo-relative `python3 scripts/project_journal.py ...` command only inside
this skill's own checkout. Do not report the validator as unavailable because
`<target-repo>/scripts/project_journal.py` is missing. The helper needs Python
3.9+, native Git 2.45+, and macOS or reviewed glibc Linux; elsewhere it fails
closed, which means "helper unavailable", not a journal verdict.

## 1. Decide whether the workflow applies

- Read the repo `AGENTS.md`, relevant docs, and the request.
- The workflow is adopted only when repo policy requires it or
  `adoption-status --repo <path>` reports a valid tracked journal entry.
  Directory presence, untracked files, and an empty or generated-`INDEX.md`-only directory do not establish adoption.
- `adoption-status` is index-authoritative: it accepts only unconflicted stage-0 regular-file entries and validates the exact indexed blob.
  Worktree file type or content cannot create or remove adoption evidence.
- A spanning task (multiple sessions, a PR, or a durable workstream) is not a
  trigger. When the skill is already loaded and the repo has neither adoption
  nor policy, treat such a task only as a signal to assess durable state, not as permission to create files.
- First adoption needs an explicit product need for repo-owned coordination,
  recovery, or backlog state in the request or established workflow; a general
  preference for journaling is not enough. Without it, use the task, PR, issue,
  or handoff channel and leave `docs/PROJECT_STATE.md`, `docs/PROJECT_TODO.md`, and `docs/project_journal/` unchanged.
- Follow a stronger repo tracker or the user's chosen mechanism instead.

## 2. Recover context

- Read only the relevant workstream journals. Read `PROJECT_STATE` and
  `PROJECT_TODO` when they exist and matter; never create a missing counterpart
  merely because one exists.
- Reuse existing section names, task labels, and terminology.
- Do not generate the index merely to read or update one known entry.

## 3. Update the smallest applicable layer

- Once the workflow applies and any first-adoption gate is satisfied, update the smallest applicable existing or required layer without waiting for another prompt.
- Ordinary task, PR, thread, blocker, or handoff state goes in
  `docs/project_journal/YYYY/MM/YYYY-MM-DD-<slug>-<shortid>.md`, starting from
  `references/templates.md`.
- Touch `PROJECT_STATE` only for repo-wide pulse, recovery-path, or global
  blocker changes, and `PROJECT_TODO` only for cross-workstream backlog.
  Do not create both top-level files as a pair by default.
- When present, keep `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md` short;
  move long detail into a journal or a focused note (for example `docs/notes/`)
  and leave a pointer.
- Update current-state bullets instead of appending a transcript. At a phase
  change (pause, new owner, discovery to implementation, testing, or review),
  add a compact handoff: phase, summary, next steps, blockers, and evidence
  references such as commits, PRs, build URLs, log paths, or issue IDs.
- Do not duplicate README, design docs, or PR summaries, and do not invent work.

## 4. Validate and prepare commits and PRs

- Run `validate --repo <path>` before committing journal changes, and include
  material doc updates in the same commit.
- Check the merge model from repo guidance, branch protection, or convention.
  In squash-merge repos, tracked journal docs should describe the target branch after the PR lands.
  If the PR completes the workstream, set `status: completed` before merge and
  cite the PR; a partial slice stays `active` or `blocked` with only real
  follow-ups in `Next Steps`.
- Keep transient states such as "ready for review" or "waiting for merge" in the
  PR body or comments, never in tracked docs.
- Otherwise keep each commit's tracked docs self-consistent and do not mark a
  workstream completed before that commit contains the implementation, docs,
  and validation evidence.

## 5. Local index and hooks are opt-in

- Run `generate --repo <path> --output docs/project_journal/INDEX.md --ensure-exclude`
  only when the active workflow needs multi-entry navigation, an explicit index
  refresh, or an already opted-in hook refresh. Never commit `INDEX.md`;
  `--ensure-exclude` lists it in `.git/info/exclude`.
- Run `install-hooks --repo <path>` only when the user explicitly wants opt-in local hook refresh for that repo.
  Never batch-install hooks. Installed hooks embed the current Python and
  helper paths, so reinstall them after moving the skill checkout.

## 6. Read helper results conservatively

- A nonzero exit or `error:` output is a fail-closed result; report it.
- Adoption is decided only by `adopted` or `unadopted`. `inconclusive` carries a structured `adoption_error` and null adoption fields; it is not evidence that the repository is unadopted.
- `false` is an authoritative negative; `null` means unknown.
- JSON strings from `adoption-status` and `discover-repos` are UTF-8-safe display
  values: invalid path bytes appear as `backslashreplace` escapes, so a value
  may not round-trip to a real filesystem path. Verify actual paths before
  acting, and never run or open display escapes as commands or paths.
- Optional, Codex-only: `discover-repos [--codex-home <dir>] [--since-days N] [--json]`
  lists repositories seen in Codex session logs. It is a candidate report only
  and never establishes adoption or authorizes trackers, indexes, or hooks.
  Require `coverage_status: complete` before treating the candidate set as
  complete; resolve each partial row's `discovery_coverage_ref` to its anchor.
- Load `references/helper-safety-contract.md` only when changing or debugging
  the helper or interpreting a safety, cleanup, signal, or locator error.
  Routine journal updates do not need it.

## Migrations

Treat migration as an explicit adoption or maintenance task, never incidental
cleanup. Load `references/migration-playbook.md` first, preserve every old
tracker item intentionally, update repo guidance pointers, and keep existing
date-based journals instead of migrating them again.

## Guardrails

- Do not assume every repository should adopt project journals.
- Do not commit generated `INDEX.md`, local hooks, or transient PR states unless
  the user explicitly asks for that exact local state to be tracked.
- Repo conventions and explicit user choices override this skill.
