---
id: 20261015-tfh001
title: Timeout Fixture Handshake
status: completed
created: 2026-10-15
updated: 2026-10-15
branch: codex/journal-timeout-fixture-handshake-20261015
pr:
supersedes: []
superseded_by:
---

# Timeout Fixture Handshake

## Summary
- The cat-file timeout regression protects the ability of process-group descendants to write after timeout cleanup returns, not the absence of writes before supervision starts.

## Current State
- The former fixture released a prestarted descendant before invoking the batch helper. Its 0.3-second delayed write could therefore occur before timeout cleanup began.
- A local controlled probe reproduced the assertion failure by adding only a 0.45-second pre-supervision delay: the marker already existed before the helper ran, while the direct child still terminated with SIGTERM. This proves a false-failure path, not the exact scheduling cause of the hosted failure.
- The descendant now publishes readiness itself, starts through the supervisor's process handoff, and waits until timeout cleanup has returned before its writer is released. Unrelated native Git preparation is isolated in this process-only fixture.
- The timeout regression includes the pre-supervision delay. A surviving-descendant control must write `survived`; expiry also emits a failure witness instead of silently making the safety assertion pass. Both post-release observations are bounded at three seconds.
- Production runtime, process-group cleanup, production timeout limits, and source-generation policy are unchanged. The fixture's timeout is one second, allowing its verified process-start handshake.

## Next Steps
- No further canonical changes are required for this fixture repair; downstream publication follows the declared source-sync workflow.

## Evidence
- Baseline: `bd2909c9d41d3586267a9de83a416a137f81d793`.
- [Private PR229 failing macOS job](https://github.com/Joey-Tools/codex-private-workflows/actions/runs/38761796733/job/116514690710): 447 tests, 79.695 seconds, one failure, six skips; raw log SHA-256 `dc42398ff84d12ce656ae006c4a211a3fc0cbb1af4525e6215887df782a383bf`.
- Local timing probe: supervisor exit 0, 2.624 seconds, complete output SHA-256 `4a2b5923d559c847380613dff690a23bf9422c21b8775dd7ee8c38feb55d930f`; the baseline passed, while the delayed control failed its marker assertion with the marker already present before supervision. The source test bytes remained unchanged.
- Native CPython 3.13.0: both focused tests passed in 4.601 seconds (complete log SHA-256 `2cbafbdb5387e38ba673f8071c9d9f673875b0a4c303b74fff82157ae17db19c`); the complete suite passed 448 tests in 588.683 seconds with five skips (supervisor exit 0, incomplete null, complete log SHA-256 `0023c89467c63ba42ac7056844c72a40780d1f9f8508bb231b3cd41044c82164`).
- Explicit fatal SIGQUIT integration passed one test in 1.802 seconds with `PROJECT_JOURNAL_RUN_FATAL_SIGNAL_TESTS=1` (complete log SHA-256 `3c75de4eb97208e169cd0100c6fe123fdc0f8cdfced93c3c4f247247f7b810c9`).
- Tested source SHA-256 `d10e7e33f18607c08b183747006f6585c96656ae858d9e5e7ec20819825c1112` was rechecked unchanged. Ruff and whitespace checks passed.
