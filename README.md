# Codex Project Journal

Public Codex skill and helper scripts for lightweight per-workstream project journals in repositories whose policy requires the workflow, that contain a valid tracked non-generated journal entry, or that have an explicit durable-state need.
The skill does not bootstrap trackers, generated indexes, or local hooks across repositories by default.
An empty `docs/project_journal/` directory or generated `INDEX.md` does not establish adoption.
Adoption checks validate exact unconflicted stage-0 regular-file blobs from the Git index, not substituted worktree paths or content.
Before any repository Git read, the helper copies one resolved Git executable into an owner-private immutable-by-policy snapshot, requires that exact snapshot to return a bounded credential-free Git 2.45-or-newer version, revalidates its identity and SHA-256 before every command, and never returns to the mutable source path. It ignores ambient Git control/configuration redirections and forbids lazy object fetching. An adoption read binds the initial raw index snapshot, one bounded `git cat-file --batch` session, bounded frontmatter parsing and semantic validation, structured per-path validity, and identical final raw index revalidation to one absolute monotonic deadline. Exact OID/type/size/content framing, entry/field/list/issue budgets, duplicate-ID group invalidation, and byte, record, and stderr bounds all fail closed.
POSIX status observation uses `WNOWAIT` so the unreaped direct child fences the PID/PGID through every bound process-group probe and signal. Explicit cleanup ownership states close the async-exception handoff between process-group cleanup and reap-only cleanup; the helper never signals a numeric PGID after the final reap and reports `cleanup-incomplete` if identity, ownership handoff, or cleanup cannot be verified.
Opt-in hook installation inspects both system and global Git configuration without following includes. If either scope sets `core.hooksPath` or contains an unresolved include, installation stops with an actionable repo-local override. Hook writes start from a verified allowed repository or Git-root descriptor, traverse and create every relative component with descriptor-relative no-follow operations, retain and revalidate every ancestor binding, inspect existing targets with `O_NOFOLLOW`, and use owner-private same-directory temporary files plus descriptor-relative atomic replacement. Target protection covers object identity, type, ownership, access mode, size, and content—not benign timestamp churn.
Repository discovery treats `adoption_status` as the authoritative `adopted`, `unadopted`, or `inconclusive` tag; inconclusive rows include a structured `adoption_error` and null adoption fields, and consumers must not use them as unadopted evidence. Auxiliary per-repository failures produce a structured `discovery_error` and null only the affected auxiliary fields without erasing authoritative index adoption or aborting healthy repository rows.

## Test

```bash
python3 -m unittest discover -s tests
```
