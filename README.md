# Project Journal Skill

A portable [Agent Skill](SKILL.md) plus a stdlib-only helper for lightweight
per-workstream project journals. It applies when repository policy requires the
workflow, a valid tracked non-generated journal entry exists, or the user
explicitly requests journals or repository-owned durable coordination,
recovery, or backlog state.

## Scope

- Journals live in `docs/project_journal/YYYY/MM/*.md` with validated
  frontmatter. Optional `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md` stay
  short, stable entrypoints.
- The skill does not bootstrap trackers, generated indexes, or local hooks by
  default. A PR or multi-session task alone does not trigger it, and an
  explicit durable-state request still passes the adoption gate before any
  first tracker is created.
- An empty `docs/project_journal/` directory or generated `INDEX.md` does not establish adoption.
  Adoption is read from exact tracked blobs in the Git index, not from
  worktree files.
- The generated `docs/project_journal/INDEX.md` is local and excluded from Git.

Journal frontmatter fields: `id`, `title`, `status` (`active`, `blocked`,
`completed`, `superseded`, or `abandoned`), `created` and `updated`
(`YYYY-MM-DD`), `branch`, `pr`, `supersedes`, and `superseded_by`. See
[references/templates.md](references/templates.md).

## Requirements

- Python 3.9 or newer, standard library only.
- A native (ELF or Mach-O) Git 2.45 or newer; script wrappers fail closed.
- macOS, or Linux with glibc on x86_64 or AArch64. Other platforms, including
  musl and other POSIX systems, fail closed. Every helper command, including
  `validate`, performs this Git and platform check.

## Install

Installing runs no code: there is no package manager, build step, or install
script. Clone the repository to a stable location and link the directory.

For Pi, add a symlink under its global skill directory without overwriting an
existing entry. Use an absolute clone path: a relative symlink target would
resolve against the skills directory instead of your current directory.

```bash
CLONE_DIR=/absolute/path/to/codex-project-journal
git clone https://github.com/Joey-Tools/codex-project-journal.git "$CLONE_DIR"
mkdir -p ~/.pi/agent/skills
if [ -e ~/.pi/agent/skills/project-journal ] || [ -L ~/.pi/agent/skills/project-journal ]; then
  echo "~/.pi/agent/skills/project-journal already exists; not replacing it" >&2
else
  ln -s "$CLONE_DIR" ~/.pi/agent/skills/project-journal
fi
```

Pi loads `~/.pi/agent/skills/project-journal/SKILL.md` and follows symlinked
skills. Because the skill is plain Markdown plus a script, edits or a
`git pull` in the clone apply after `/reload`.

For Codex, link the same clone into `${CODEX_HOME:-$HOME/.codex}/skills/project-journal`
using the same non-overwriting guard. `agents/openai.yaml` supplies the Codex UI
metadata. Other Agent Skills clients can link the clone into their own skill
directory.

## Helper commands

Run the helper from the installed skill directory:
`python3 "<skill-dir>/scripts/project_journal.py" <command>`.

| Command | Purpose |
| --- | --- |
| `adoption-status --repo <path>` | JSON adoption evidence from the Git index. |
| `validate --repo <path>` | Validate frontmatter of journal Markdown files. |
| `generate --repo <path> [--output <file>] [--ensure-exclude]` | Write the local index (default `docs/project_journal/INDEX.md`). |
| `install-hooks --repo <path>` | Opt in to `post-merge`, `post-checkout`, and `post-rewrite` index refresh. |
| `discover-repos [--codex-home <dir>] [--since-days N] [--json]` | Optional, Codex-only candidate report from Codex session logs. |

Installed hooks embed absolute Python and helper paths, always exit zero, and
are skipped when `PROJECT_JOURNAL_INDEX_DISABLE=1`. Reinstall them after moving
the clone. Discovery output is a candidate report and never proves adoption.

## Safety contract

The helper's bounded reads, Git snapshotting, signal handling, process cleanup,
hook transactions, and discovery coverage rules are specified in
[references/helper-safety-contract.md](references/helper-safety-contract.md).
Read it when changing or debugging the helper or interpreting a safety error.
Migration campaigns use
[references/migration-playbook.md](references/migration-playbook.md).

## Test

```bash
python3 -m unittest discover -s tests
PROJECT_JOURNAL_RUN_FATAL_SIGNAL_TESTS=1 python3 -m unittest -v \
  tests.test_project_journal.ProjectJournalTests.test_helper_defers_sigquit_until_git_group_cleanup_fatal_integration
python3 scripts/project_journal.py validate --repo .
```
