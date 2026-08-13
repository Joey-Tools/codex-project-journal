---
id: 20260813-rci001
title: Required CI Reusable Entry
status: completed
created: 2026-08-13
updated: 2026-08-13
branch: codex/daily-skill-friction-20260813-codex-project-journal-codex-review-v2
pr:
supersedes: []
superseded_by:
---

# Required CI Reusable Entry

## Summary
- Added a reusable required-CI entry that preserves the complete existing required test, including the fatal-signal opt-in check and SIGQUIT integration test.

## Current State
- `.github/workflows/required-ci.yml` is callable only through `workflow_call` and uses read-only contents permission.
- The existing event-driven `.github/workflows/ci.yml` remains unchanged for rollout canaries.

## Next Steps
- None in this repository slice.

## Evidence

- The reusable entry receives the fatal-signal opt-in as a required closed input from the central router. It does not read a repository variable through the cross-repository caller context.
- `python3 -m unittest tests.test_required_ci_workflow`
- `python3 -m unittest discover -s tests`
- `PROJECT_JOURNAL_RUN_FATAL_SIGNAL_TESTS=1 python3 -m unittest -v tests.test_project_journal.ProjectJournalTests.test_helper_defers_sigquit_until_git_group_cleanup_fatal_integration`
