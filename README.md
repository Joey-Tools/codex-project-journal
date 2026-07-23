# Codex Project Journal

Public Codex skill and helper scripts for lightweight per-workstream project journals in repositories whose policy requires the workflow, that contain a valid tracked non-generated journal entry, or that have an explicit durable-state need.
The skill does not bootstrap trackers, generated indexes, or local hooks across repositories by default.
An empty `docs/project_journal/` directory or generated `INDEX.md` does not establish adoption.
Adoption checks validate exact unconflicted stage-0 regular-file blobs from the Git index, not substituted worktree paths or content.
The helper ignores ambient Git control/configuration redirections and forbids lazy object fetching. An adoption read binds the initial raw index snapshot, one bounded `git cat-file --batch` session, and identical final raw index revalidation to one absolute monotonic deadline, with exact OID/type/size/content framing plus byte, record, and stderr bounds. POSIX status observation uses `WNOWAIT` so the unreaped direct child fences the PID/PGID through every bound process-group probe and signal; the helper never signals a numeric PGID after the final reap, and reports `cleanup-incomplete` if that identity or cleanup cannot be verified.
Repository discovery treats `adoption_status` as the authoritative `adopted`, `unadopted`, or `inconclusive` tag; inconclusive rows include a structured `adoption_error` and null adoption fields, and consumers must not use them as unadopted evidence.

## Test

```bash
python3 -m unittest discover -s tests
```
