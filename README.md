# Codex Project Journal

Public Codex skill and helper scripts for lightweight per-workstream project journals in repositories whose policy requires the workflow, that contain a valid tracked non-generated journal entry, or that have an explicit durable-state need.
The skill does not bootstrap trackers, generated indexes, or local hooks across repositories by default.
An empty `docs/project_journal/` directory or generated `INDEX.md` does not establish adoption.
Adoption checks validate exact unconflicted stage-0 regular-file blobs from the Git index, not substituted worktree paths or content.

## Test

```bash
python3 -m unittest discover -s tests
```
