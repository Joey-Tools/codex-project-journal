---
id: 20261028-4f7c2a
title: Portable Skill Doc Simplification
status: completed
created: 2026-10-28
updated: 2026-10-28
branch: docs/simplify-portable-project-journal
pr: https://github.com/Joey-Tools/codex-project-journal/pull/24
supersedes: []
superseded_by:
---

# Portable Skill Doc Simplification

## Summary
- `SKILL.md` is a concise, agent-neutral Agent Skill (48,690 to 7,759 bytes) that keeps the adoption gate, index-authoritative evidence, assess-versus-bootstrap distinction, smallest-layer updates, handoffs, validation, squash-merge target-branch semantics, skill-relative helper paths, opt-in index/hooks, and conservative `inconclusive`/`null`/partial-coverage handling.
- Frontmatter routing no longer treats a PR or multi-session task alone as a trigger. The skill applies when repo policy requires journals, a valid tracked non-generated journal exists, or the user explicitly requests journals or repository-owned durable coordination, recovery, or backlog state; that request still passes the adoption gate. `SKILL.md`, `README.md`, and `agents/openai.yaml` use the same routing.
- `references/helper-safety-contract.md` is the single canonical low-level helper contract, extracted verbatim from the former README internals plus the few clauses that existed only in `SKILL.md`; `SKILL.md` loads it only for helper changes, debugging, or safety-error interpretation.
- `README.md` is a short guide (38,292 to 4,590 bytes): scope, requirements, no-code symlink install for Pi and Codex, command table, and tests.
- `scripts/`, `.github/`, CLI behavior, journal schema, and helper output are unchanged.

## Current State
- Prose-contract tests now pin workflow phrases on `SKILL.md` and implementation phrases on the canonical contract; new regressions pin skill size and frontmatter shape, lazy contract loading, README install guidance, link resolution, and absence of host-specific home paths.

## Next Steps
- None.

## Evidence
- macOS, CPython 3.14.5, Git 2.49.0: `python3 -m unittest discover -s tests` ran 454 tests in 423.8 s, OK with 5 skips (baseline 451 tests, same 5 skips).
- The five original prose-contract tests passed on an exported baseline tree; the five retargeted tests and three new documentation regressions pass on this change.
- `PROJECT_JOURNAL_RUN_FATAL_SIGNAL_TESTS=1` fatal SIGQUIT integration test passed; `validate --repo .` and `git diff --check` passed.
- Pi 1.4 `loadSkillsFromDir` plus `formatSkillsForPrompt` on a global `~/.pi/agent/skills/project-journal` symlink to this checkout found exactly `project-journal` with zero diagnostics and only lazy metadata in the prompt; the bundled helper's `adoption-status` reported this repo adopted and `validate` passed.
- Every original README contract sentence appears verbatim in the canonical contract except three that were extended with former `SKILL.md`-only clauses.
