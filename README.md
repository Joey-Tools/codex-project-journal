# Codex Project Journal

Public Codex skill and helper scripts for lightweight per-workstream project journals in repositories whose policy requires the workflow, that contain a valid tracked non-generated journal entry, or that have an explicit durable-state need.
The skill does not bootstrap trackers, generated indexes, or local hooks across repositories by default.
An empty `docs/project_journal/` directory or generated `INDEX.md` does not establish adoption.
Adoption checks validate exact unconflicted stage-0 regular-file blobs from the Git index, not substituted worktree paths or content.
Before any repository Git read, the helper fixes one absolute Git executable and requires a bounded credential-free version result of Git 2.45 or newer. It ignores ambient Git control/configuration redirections and forbids lazy object fetching. An adoption read binds the initial raw index snapshot, one bounded `git cat-file --batch` session, bounded frontmatter parsing and semantic validation, structured per-path validity, and identical final raw index revalidation to one absolute monotonic deadline. Exact OID/type/size/content framing, entry/field/list/issue budgets, and byte, record, and stderr bounds all fail closed.
POSIX status observation uses `WNOWAIT` so the unreaped direct child fences the PID/PGID through every bound process-group probe and signal. Explicit cleanup ownership states close the async-exception handoff between process-group cleanup and reap-only cleanup; the helper never signals a numeric PGID after the final reap and reports `cleanup-incomplete` if identity, ownership handoff, or cleanup cannot be verified.
Opt-in hook installation safely snapshots the actual global Git config and parses a bounded private copy without following includes. If global `core.hooksPath` or an unresolved global include could redirect actual Git, installation stops with an actionable repo-local override instead of writing hooks that Git will not use.
Repository discovery treats `adoption_status` as the authoritative `adopted`, `unadopted`, or `inconclusive` tag; inconclusive rows include a structured `adoption_error` and null adoption fields, and consumers must not use them as unadopted evidence.

## Test

```bash
python3 -m unittest discover -s tests
```
